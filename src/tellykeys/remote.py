from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from array import array
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth

from . import bluetooth_hid


APP_NAME = "TellyKeys"
CONFIG_DIR = Path.home() / ".config" / "tellykeys" / "devices"
VOICE_CHUNK_BYTES = 20 * 1024
VOICE_MIN_PEAK = 450
VOICE_BYTES_PER_SECOND = 8000 * 2
VOICE_SILENCE_TIMEOUT_BYTES = int(VOICE_BYTES_PER_SECOND * 3)
VOICE_NO_SPEECH_TIMEOUT_BYTES = int(VOICE_BYTES_PER_SECOND * 8)


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
    bluetooth_enabled: bool
    bluetooth_helper_running: bool
    bluetooth_keyboard_connected: bool
    bluetooth_system_fix_installed: bool


@dataclass(frozen=True)
class BluetoothKeyboardSetup:
    ok: bool
    message: str
    helper_running: bool
    keyboard_connected: bool
    controller_powered: bool | None
    peripheral_role: bool | None
    system_fix_installed: bool


@dataclass(frozen=True)
class MicrophoneSource:
    name: str
    description: str


class BluetoothKeyboardNotConnected(RuntimeError):
    pass


class TellyKeysRemote:
    def __init__(self) -> None:
        self._remote: AndroidTVRemote | None = None
        self.host: str | None = None
        self._status_callback: Callable[[TvStatus], None] | None = None
        self._voice_status_callback: Callable[[str, bool], None] | None = None
        self.bluetooth_keyboard_enabled = False
        self._voice_task: asyncio.Task | None = None
        self._voice_process: asyncio.subprocess.Process | None = None
        self._voice_stream = None
        self.microphone_source = ""

    def set_status_callback(self, callback: Callable[[TvStatus], None]) -> None:
        self._status_callback = callback

    def set_voice_status_callback(self, callback: Callable[[str, bool], None]) -> None:
        self._voice_status_callback = callback

    def set_bluetooth_keyboard_enabled(self, enabled: bool) -> None:
        self.bluetooth_keyboard_enabled = enabled

    def set_microphone_source(self, source: str) -> None:
        self.microphone_source = source.strip()

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
        self._remote = AndroidTVRemote(APP_NAME, certfile, keyfile, self.host, enable_voice=True)
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

    def text(self, text: str) -> str | None:
        if text:
            if self.bluetooth_keyboard_enabled:
                if bluetooth_hid.send_text(text):
                    return "bluetooth_keyboard"
                diagnostics = bluetooth_hid.diagnostics()
                if not diagnostics.helper_running:
                    raise BluetoothKeyboardNotConnected("Set up Bluetooth keyboard first.")
                if diagnostics.helper_running and not diagnostics.keyboard_connected:
                    raise BluetoothKeyboardNotConnected("Pair TellyKeys Keyboard from the TV's Bluetooth menu first.")
            if self._send_contextual_search(text):
                return "youtube_search"
            if self._send_text_with_adb(text):
                return "adb"
            self._require_remote().send_text(text)
            return "remote_ime"
        return None

    def text_diagnostics(self) -> TextInputDiagnostics:
        remote = self._require_remote()
        protocol = getattr(remote, "_remote_message_protocol", None)
        adb_installed = shutil.which("adb") is not None
        adb_authorized = self._is_adb_authorized() if adb_installed and self.host else None
        bluetooth_diagnostics = bluetooth_hid.diagnostics()

        return TextInputDiagnostics(
            adb_installed=adb_installed,
            adb_authorized=adb_authorized,
            adb_target=f"{self.host}:5555" if self.host else None,
            ime_counter=getattr(protocol, "ime_counter", None),
            ime_field_counter=getattr(protocol, "ime_field_counter", None),
            current_app=remote.current_app,
            bluetooth_enabled=self.bluetooth_keyboard_enabled,
            bluetooth_helper_running=bluetooth_diagnostics.helper_running,
            bluetooth_keyboard_connected=bluetooth_diagnostics.keyboard_connected,
            bluetooth_system_fix_installed=bluetooth_diagnostics.system_fix_installed,
        )

    def bluetooth_text_status(self) -> tuple[bool, bool, bool]:
        diagnostics = bluetooth_hid.diagnostics()
        return (
            self.bluetooth_keyboard_enabled,
            diagnostics.helper_running,
            diagnostics.keyboard_connected,
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

    def prepare_bluetooth_keyboard(self) -> BluetoothKeyboardSetup:
        result = bluetooth_hid.prepare()
        return BluetoothKeyboardSetup(
            ok=result.ok,
            message=result.message,
            helper_running=result.diagnostics.helper_running,
            keyboard_connected=result.diagnostics.keyboard_connected,
            controller_powered=result.diagnostics.controller_powered,
            peripheral_role=result.diagnostics.peripheral_role,
            system_fix_installed=result.diagnostics.system_fix_installed,
        )

    def reset_bluetooth_keyboard(self, remove_system_fix: bool = False) -> BluetoothKeyboardSetup:
        result = bluetooth_hid.reset(remove_system_fix=remove_system_fix)
        self.bluetooth_keyboard_enabled = False
        return BluetoothKeyboardSetup(
            ok=result.ok,
            message=result.message,
            helper_running=result.diagnostics.helper_running,
            keyboard_connected=result.diagnostics.keyboard_connected,
            controller_powered=result.diagnostics.controller_powered,
            peripheral_role=result.diagnostics.peripheral_role,
            system_fix_installed=result.diagnostics.system_fix_installed,
        )

    def launch(self, app_id_or_url: str) -> None:
        self._require_remote().send_launch_app_command(app_id_or_url)

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

            escaped = _adb_input_text(text)
            result = subprocess.run(
                [adb, "-s", target, "shell", "input", "text", escaped],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

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
