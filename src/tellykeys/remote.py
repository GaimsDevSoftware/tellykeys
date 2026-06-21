from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import time
from array import array
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
from google.protobuf.message import DecodeError

from androidtvremote2.remotemessage_pb2 import RemoteMessage

APP_NAME = "TellyKeys"
CONFIG_DIR = Path.home() / ".config" / "tellykeys" / "devices"
VOICE_CHUNK_BYTES = 20 * 1024
VOICE_MIN_PEAK = 450
VOICE_BYTES_PER_SECOND = 8000 * 2
VOICE_SILENCE_TIMEOUT_BYTES = int(VOICE_BYTES_PER_SECOND * 3)
VOICE_NO_SPEECH_TIMEOUT_BYTES = int(VOICE_BYTES_PER_SECOND * 8)

# Android's `input text` injects characters back-to-back, and some TV
# keyboards/apps (notably Netflix) drop characters that arrive too fast.
# Send the text in small chunks with a short pause so the IME keeps up.
TEXT_ADB_CHARS_PER_CHUNK = 1
TEXT_ADB_CHUNK_DELAY = 0.05

# Google TV universal search jumps to a title's detail page; pressing OK on the
# focused play button opens it in whichever app has it (Netflix, Disney+, ...).
# Wait for the detail page to load before pressing OK.
SEARCH_OPEN_DELAY = 3.8


@dataclass(frozen=True)
class TvStatus:
    name: str | None
    is_on: bool | None
    current_app: str | None
    volume: str | None


@dataclass(frozen=True)
class TextInputDiagnostics:
    adb_installed: bool
    adb_authorized: bool | None
    adb_target: str | None
    ime_counter: int | None
    ime_field_counter: int | None
    current_app: str | None


@dataclass(frozen=True)
class TextFieldState:
    value: str
    start: int | None
    end: int | None
    label: str | None
    current_app: str | None


@dataclass(frozen=True)
class TextSendResult:
    method: str | None
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class MicrophoneSource:
    name: str
    description: str


class TellyKeysRemote:
    def __init__(self) -> None:
        self._remote: AndroidTVRemote | None = None
        self.host: str | None = None
        self._status_callback: Callable[[TvStatus], None] | None = None
        self._voice_status_callback: Callable[[str, bool], None] | None = None
        self._text_field_callback: Callable[[TextFieldState], None] | None = None
        self._voice_task: asyncio.Task | None = None
        self._voice_process: asyncio.subprocess.Process | None = None
        self._voice_stream = None
        self._last_text_field: TextFieldState | None = None
        self._last_text_field_seen_at = 0.0
        self.microphone_source = ""
        # Name shown to the TV and baked into the pairing certificate's Common
        # Name. Must be unique per device, otherwise the TV overwrites the
        # previous controller's pairing when a new one with the same name pairs.
        self.client_name = APP_NAME

    def set_status_callback(self, callback: Callable[[TvStatus], None]) -> None:
        self._status_callback = callback

    def set_voice_status_callback(self, callback: Callable[[str, bool], None]) -> None:
        self._voice_status_callback = callback

    def set_text_field_callback(self, callback: Callable[[TextFieldState], None]) -> None:
        self._text_field_callback = callback

    def set_microphone_source(self, source: str) -> None:
        self.microphone_source = source.strip()

    def set_client_name(self, name: str) -> None:
        self.client_name = name.strip()[:64] or APP_NAME

    def _paths_for_host(self, host: str) -> tuple[str, str]:
        safe_host = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in host)
        host_dir = CONFIG_DIR / safe_host
        host_dir.mkdir(parents=True, exist_ok=True)
        return str(host_dir / "cert.pem"), str(host_dir / "key.pem")

    async def prepare(self, host: str) -> bool:
        self.host = host.strip()
        if not self.host:
            raise ValueError("Missing IP address.")

        if self._remote:
            self._remote.disconnect()

        certfile, keyfile = self._paths_for_host(self.host)
        desired_name = self.client_name or APP_NAME
        if not _cert_common_name_matches(certfile, desired_name):
            # The stored certificate was issued under a different name; remove it
            # so a fresh one is generated under the current name (and re-paired).
            for path in (certfile, keyfile):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path)
        self._remote = AndroidTVRemote(desired_name, certfile, keyfile, self.host, enable_voice=True)
        return await self._remote.async_generate_cert_if_missing()

    async def device_name(self) -> str:
        remote = self._require_remote()
        name, mac = await remote.async_get_name_and_mac()
        return name or mac

    async def start_pairing(self) -> None:
        remote = self._require_remote()
        await remote.async_start_pairing()

    async def finish_pairing(self, code: str) -> None:
        remote = self._require_remote()
        await remote.async_finish_pairing(code.strip())

    async def connect(self) -> TvStatus:
        remote = self._require_remote()
        try:
            await remote.async_connect()
        except InvalidAuth:
            raise InvalidAuth("The TV must be paired first.") from None
        except CannotConnect:
            raise CannotConnect(f"Could not reach {self.host}.") from None
        except ConnectionClosed:
            raise ConnectionClosed("The connection closed while connecting.") from None

        remote.keep_reconnecting()
        self._register_status_callbacks(remote)
        return self.status()

    def disconnect(self) -> None:
        if self._remote:
            self._remote.disconnect()

    def status(self) -> TvStatus:
        remote = self._require_remote()
        volume = remote.volume_info
        volume_text = None
        if volume:
            if isinstance(volume, dict):
                level = volume.get("level")
                maximum = volume.get("maximum")
                muted_value = volume.get("muted", False)
            else:
                level = getattr(volume, "level", None)
                maximum = getattr(volume, "maximum", None)
                muted_value = getattr(volume, "muted", False)

            if level is not None and maximum is not None:
                muted = " muted" if muted_value else ""
                volume_text = f"{level}/{maximum}{muted}"

        name = None
        if remote.device_info:
            if isinstance(remote.device_info, dict):
                manufacturer = remote.device_info.get("manufacturer")
                model = remote.device_info.get("model")
            else:
                manufacturer = getattr(remote.device_info, "manufacturer", None)
                model = getattr(remote.device_info, "model", None)
            name = " ".join(part for part in [manufacturer, model] if part)

        return TvStatus(
            name=name or None,
            is_on=remote.is_on,
            current_app=remote.current_app,
            volume=volume_text,
        )

    def key(self, key_code: str) -> None:
        self._require_remote().send_key_command(key_code)

    def delete_text(self, count: int = 1) -> str:
        count = max(1, count)
        if self._send_key_with_adb("KEYCODE_DEL", count=count):
            return "adb"
        self._send_remote_key_repeated("DEL", count)
        return "remote_key"

    def clear_text(self) -> str:
        state = self._last_text_field if self._has_recent_text_field() else None
        delete_count = len(state.value) if state and state.value else 1
        return self.delete_text(delete_count)

    def text(self, text: str) -> TextSendResult:
        attempts: list[str] = []
        if not text:
            return TextSendResult(method=None, attempts=())

        if self._has_recent_text_field():
            attempts.append("adb")
            if self._send_text_with_adb(text):
                return TextSendResult(method="adb", attempts=tuple(attempts))
            attempts.append("remote_ime")
            self._require_remote().send_text(text)
            return TextSendResult(method="remote_ime", attempts=tuple(attempts))

        attempts.append("youtube_search")
        if self._send_contextual_search(text):
            return TextSendResult(method="youtube_search", attempts=tuple(attempts))

        attempts.append("adb")
        if self._send_text_with_adb(text):
            return TextSendResult(method="adb", attempts=tuple(attempts))

        attempts.append("remote_ime")
        self._require_remote().send_text(text)
        return TextSendResult(method="remote_ime", attempts=tuple(attempts))

    def text_diagnostics(self) -> TextInputDiagnostics:
        remote = self._require_remote()
        protocol = getattr(remote, "_remote_message_protocol", None)
        adb_installed = shutil.which("adb") is not None
        adb_authorized = self._is_adb_authorized() if adb_installed and self.host else None
        return TextInputDiagnostics(
            adb_installed=adb_installed,
            adb_authorized=adb_authorized,
            adb_target=f"{self.host}:5555" if self.host else None,
            ime_counter=getattr(protocol, "ime_counter", None),
            ime_field_counter=getattr(protocol, "ime_field_counter", None),
            current_app=remote.current_app,
        )

    def open_tv_keyboard_settings(self) -> bool:
        adb = shutil.which("adb")
        if not adb or not self.host or not self._is_adb_authorized():
            return False

        target = f"{self.host}:5555"
        try:
            result = subprocess.run(
                [adb, "-s", target, "shell", "am", "start", "-a", "android.settings.INPUT_METHOD_SETTINGS"],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def open_tv_settings(self) -> str:
        adb = shutil.which("adb")
        if adb and self.host and self._is_adb_authorized():
            target = f"{self.host}:5555"
            try:
                result = subprocess.run(
                    [adb, "-s", target, "shell", "am", "start", "-a", "android.settings.SETTINGS"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if result.returncode == 0:
                    return "adb"
            except (OSError, subprocess.TimeoutExpired):
                pass

        self.key("SETTINGS")
        return "key"

    def launch(self, app_id_or_url: str) -> None:
        self._require_remote().send_launch_app_command(app_id_or_url)

    def global_search(self, query: str, open_result: bool = True) -> bool:
        """Open a show via Google TV universal search.

        Launches the system search pre-filled with ``query`` (which jumps to the
        title's detail page), then optionally presses OK on the focused play
        button to open the show in whichever app has it. Requires ADB; returns
        ``False`` if ADB is unavailable so the caller can report it.
        """
        query = query.strip()
        if not query:
            return False

        adb = shutil.which("adb")
        if not adb or not self.host:
            return False

        target = f"{self.host}:5555"
        try:
            if not self._is_adb_authorized():
                subprocess.run([adb, "connect", target], check=False, capture_output=True, text=True, timeout=4)
            if not self._is_adb_authorized():
                return False

            # Single-quote the query for the device shell so titles with spaces
            # are passed as one argument.
            quoted = "'" + query.replace("'", "'\\''") + "'"
            command = f"am start -a android.search.action.GLOBAL_SEARCH -e query {quoted}"
            result = subprocess.run(
                [adb, "-s", target, "shell", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=6,
            )
            if result.returncode != 0:
                return False

            if open_result:
                time.sleep(SEARCH_OPEN_DELAY)
                subprocess.run(
                    [adb, "-s", target, "shell", "input", "keyevent", "DPAD_CENTER"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def start_voice_search(self) -> str:
        if self._voice_task and not self._voice_task.done():
            return "Voice search is already listening."
        if not _audio_capture_command(self.microphone_source):
            raise RuntimeError("Could not find pw-record or parec for microphone capture.")
        self._voice_task = asyncio.create_task(self._voice_loop())
        self._voice_task.add_done_callback(self._on_voice_done)
        self._emit_voice_status("Opening voice search ...", True)
        return "Opening voice search ..."

    def microphone_sources(self) -> list[MicrophoneSource]:
        sources, _message = _microphone_sources_with_status()
        return sources

    def microphone_sources_with_status(self) -> tuple[list[MicrophoneSource], str]:
        return _microphone_sources_with_status()

    def test_microphone(self) -> tuple[bool, int]:
        command = _audio_capture_command(self.microphone_source)
        if not command:
            raise RuntimeError("Could not find pw-record or parec.")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_pulse_env())
        try:
            data, _stderr = process.communicate(timeout=1.2)
        except subprocess.TimeoutExpired:
            process.terminate()
            data, _stderr = process.communicate(timeout=1)
        if not data:
            stderr = _stderr.decode(errors="replace").strip()
            raise RuntimeError(stderr or "No audio was captured.")
        peak = _pcm_peak(data)
        return peak > 400, peak

    def stop_voice_search(self) -> str:
        if self._voice_task and not self._voice_task.done():
            self._voice_task.cancel()
            return "Stopping voice search ..."
        return "Voice search is not active."

    async def _voice_loop(self) -> None:
        remote = self._require_remote()
        stream = None
        process = None
        try:
            stream = await remote.start_voice(timeout=4)
            self._voice_stream = stream
            command = _audio_capture_command(self.microphone_source)
            if not command:
                raise RuntimeError("Could not find pw-record or parec.")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_pulse_env(),
            )
            self._voice_process = process
            self._emit_voice_status("Listening ...", True)
            if not process.stdout:
                raise RuntimeError("Could not read microphone audio.")
            buffer = bytearray()
            started_audio = False
            silent_bytes = 0
            no_speech_bytes = 0
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                chunk_peak = _pcm_peak(chunk)
                if started_audio:
                    if chunk_peak < VOICE_MIN_PEAK:
                        silent_bytes += len(chunk)
                    else:
                        silent_bytes = 0
                    if silent_bytes >= VOICE_SILENCE_TIMEOUT_BYTES:
                        self._emit_voice_status("Voice search sent.", False)
                        return
                else:
                    no_speech_bytes += len(chunk)
                    if no_speech_bytes >= VOICE_NO_SPEECH_TIMEOUT_BYTES:
                        self._emit_voice_status("No speech heard.", False)
                        return
                buffer.extend(chunk)
                while len(buffer) >= VOICE_CHUNK_BYTES:
                    packet = bytes(buffer[:VOICE_CHUNK_BYTES])
                    del buffer[:VOICE_CHUNK_BYTES]
                    peak = _pcm_peak(packet)
                    if not started_audio and peak < VOICE_MIN_PEAK:
                        continue
                    if not started_audio:
                        started_audio = True
                        self._emit_voice_status(f"Listening ... level {peak}", True)
                    if not stream.send_chunk(packet):
                        return
            if buffer and started_audio:
                stream.send_chunk(bytes(buffer))
        except asyncio.CancelledError:
            raise
        finally:
            if stream:
                with contextlib.suppress(Exception):
                    stream.end()
            if process and process.returncode is None:
                process.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=1)
            self._voice_stream = None
            self._voice_process = None

    def _on_voice_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
            self._emit_voice_status("Voice search finished.", False)
        except asyncio.CancelledError:
            self._emit_voice_status("Voice search stopped.", False)
        except Exception as exc:  # noqa: BLE001
            self._emit_voice_status(f"Voice search failed: {exc}", False)

    def _emit_voice_status(self, message: str, active: bool) -> None:
        if self._voice_status_callback:
            self._voice_status_callback(message, active)

    def _register_status_callbacks(self, remote: AndroidTVRemote) -> None:
        def emit(_value=None) -> None:
            try:
                if self._status_callback:
                    self._status_callback(self.status())
            except Exception:
                return

        remote.add_current_app_updated_callback(emit)
        remote.add_is_on_updated_callback(emit)
        remote.add_volume_info_updated_callback(emit)
        self._install_text_field_observer(remote)

    def _install_text_field_observer(self, remote: AndroidTVRemote) -> None:
        protocol = getattr(remote, "_remote_message_protocol", None)
        if not protocol or getattr(protocol, "_tellykeys_text_observer", False):
            return

        original_handle_message = protocol._handle_message

        def handle_message(raw_msg: bytes) -> None:
            try:
                msg = RemoteMessage()
                msg.ParseFromString(raw_msg)
            except DecodeError:
                msg = None
            original_handle_message(raw_msg)
            if msg is not None:
                self._observe_text_field_message(msg)

        protocol._handle_message = handle_message
        protocol._tellykeys_text_observer = True

    def _observe_text_field_message(self, msg: RemoteMessage) -> None:
        status = None
        current_app = None
        if msg.HasField("remote_ime_show_request"):
            status = msg.remote_ime_show_request.remote_text_field_status
        elif msg.HasField("remote_ime_key_inject"):
            status = msg.remote_ime_key_inject.text_field_status
            current_app = msg.remote_ime_key_inject.app_info.app_package or None

        if not status:
            return

        state = TextFieldState(
            value=status.value,
            start=status.start,
            end=status.end,
            label=status.label or None,
            current_app=current_app,
        )
        self._last_text_field = state
        self._last_text_field_seen_at = time.monotonic()
        if self._text_field_callback:
            self._text_field_callback(state)

    def _has_recent_text_field(self) -> bool:
        return self._last_text_field is not None and time.monotonic() - self._last_text_field_seen_at < 30

    def _require_remote(self) -> AndroidTVRemote:
        if not self._remote:
            raise RuntimeError("Choose an IP address first.")
        return self._remote

    def _send_text_with_adb(self, text: str) -> bool:
        adb = shutil.which("adb")
        if not adb or not self.host:
            return False

        target = f"{self.host}:5555"
        try:
            if not self._is_adb_authorized():
                subprocess.run([adb, "connect", target], check=False, capture_output=True, text=True, timeout=4)
            if not self._is_adb_authorized():
                return False

            # Send in small chunks with a short pause to avoid dropped
            # characters from too-fast injection. Once the first chunk lands
            # we commit to ADB and return True, so the caller does not also
            # fall back to remote IME (which would re-type the whole string).
            last_start = max(0, len(text) - TEXT_ADB_CHARS_PER_CHUNK)
            for index in range(0, len(text), TEXT_ADB_CHARS_PER_CHUNK):
                chunk = text[index:index + TEXT_ADB_CHARS_PER_CHUNK]
                escaped = _adb_input_text(chunk)
                result = subprocess.run(
                    [adb, "-s", target, "shell", "input", "text", escaped],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if index == 0 and result.returncode != 0:
                    return False
                if index < last_start:
                    time.sleep(TEXT_ADB_CHUNK_DELAY)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _send_key_with_adb(self, key_code: str, count: int = 1) -> bool:
        adb = shutil.which("adb")
        if not adb or not self.host:
            return False

        target = f"{self.host}:5555"
        try:
            if not self._is_adb_authorized():
                subprocess.run([adb, "connect", target], check=False, capture_output=True, text=True, timeout=4)
            if not self._is_adb_authorized():
                return False

            for _ in range(count):
                result = subprocess.run(
                    [adb, "-s", target, "shell", "input", "keyevent", key_code],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if result.returncode != 0:
                    return False
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _send_remote_key_repeated(self, key_code: str, count: int) -> None:
        for _ in range(count):
            self.key(key_code)

    def _send_contextual_search(self, text: str) -> bool:
        remote = self._require_remote()
        if remote.current_app != "com.google.android.youtube.tv":
            return False

        search_url = _youtube_search_url(text)
        if not search_url:
            return False

        remote.send_launch_app_command(search_url)
        return True

    def _is_adb_authorized(self) -> bool:
        adb = shutil.which("adb")
        if not adb or not self.host:
            return False

        target = f"{self.host}:5555"
        try:
            devices = subprocess.run(
                [adb, "devices"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return f"{target}\tdevice" in devices.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False


class AsyncRunner:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()

    def run(self, coro, done: Callable[[Future], None]) -> Future:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        future.add_done_callback(done)
        return future

    def call(self, func: Callable, *args) -> None:
        self.loop.call_soon_threadsafe(func, *args)


def _cert_common_name_matches(certfile: str, name: str) -> bool:
    """Whether the stored certificate's Common Name equals ``name``.

    Returns True when there is no certificate yet or it cannot be read, so we
    never delete a certificate we are unsure about.
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID

        with open(certfile, "rb") as handle:
            cert = x509.load_pem_x509_certificate(handle.read())
        common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not common_names:
            return True
        return str(common_names[0].value) == name
    except FileNotFoundError:
        return True
    except Exception:  # noqa: BLE001 - never force a re-pair on a read error
        return True


def _adb_input_text(text: str) -> str:
    # Android's `input text` uses %s for spaces.
    safe = []
    for char in text:
        if char == " ":
            safe.append("%s")
        elif char in {'"', "'", "\\", "&", "|", ";", "<", ">", "(", ")", "$", "*"}:
            safe.append("\\" + char)
        else:
            safe.append(char)
    return "".join(safe)


def _pulse_env() -> dict[str, str]:
    env = os.environ.copy()
    runtime_dir = Path(f"/run/user/{os.getuid()}")
    if runtime_dir.exists():
        env.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))
        pulse_socket = runtime_dir / "pulse" / "native"
        if pulse_socket.exists():
            env.setdefault("PULSE_SERVER", f"unix:{pulse_socket}")
    return env


def _audio_capture_command(source: str) -> list[str]:
    source = source.strip()
    if shutil.which("pw-record"):
        command = ["pw-record", "--rate", "8000", "--channels", "1", "--format", "s16"]
        if source:
            command.extend(["--target", source])
        command.append("-")
        return command

    if shutil.which("parec"):
        command = ["parec", "--raw", "--format=s16le", "--rate=8000", "--channels=1"]
        if source:
            command.append(f"--device={source}")
        return command

    return []


def _pcm_peak(data: bytes) -> int:
    if len(data) < 2:
        return 0
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    return max((abs(sample) for sample in samples), default=0)


def _microphone_sources_with_status() -> tuple[list[MicrophoneSource], str]:
    if not shutil.which("pactl"):
        return [], "Could not find pactl."

    try:
        result = subprocess.run(
            ["pactl", "list", "sources"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            env=_pulse_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], "Could not ask PulseAudio/PipeWire for microphones."

    if result.returncode != 0:
        message = result.stderr.strip().splitlines()[0] if result.stderr.strip() else "PulseAudio/PipeWire did not answer."
        return [], message

    sources: list[MicrophoneSource] = []
    monitor_count = 0
    name = ""
    description = ""
    for raw_line in result.stdout.splitlines() + ["Source #end"]:
        line = raw_line.strip()
        if line.startswith("Source #"):
            if name:
                if name.endswith(".monitor") or description.startswith("Monitor of "):
                    monitor_count += 1
                else:
                    sources.append(MicrophoneSource(name=name, description=description or name))
            name = ""
            description = ""
        elif line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"):
            description = line.split(":", 1)[1].strip()
    if sources:
        return sources, f"Found {len(sources)} microphone source{'s' if len(sources) != 1 else ''}."
    if monitor_count:
        return [], "Only speaker monitor sources were found. No microphone input is visible."
    return [], "No microphone sources were found."


def _microphone_sources() -> list[MicrophoneSource]:
    sources, _message = _microphone_sources_with_status()
    return sources


def _youtube_search_url(text: str) -> str | None:
    query = quote_plus(text.strip())
    if not query:
        return None
    return f"https://www.youtube.com/results?search_query={query}"
