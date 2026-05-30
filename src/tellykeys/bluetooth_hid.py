from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


SOCKET_PATH = Path.home() / ".cache" / "tellykeys" / "bluetooth-keyboard.sock"
SERVICE_PATH = Path.home() / ".config" / "systemd" / "user" / "tellykeys-bluetooth-keyboard.service"
HELPER_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tellykeys-bluetooth-keyboard-helper"
SYSTEM_FIX_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tellykeys-bluetooth-system-fix"
SYSTEM_UNFIX_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tellykeys-bluetooth-system-unfix"
SYSTEM_FIX_DROPIN = Path("/etc/systemd/system/bluetooth.service.d/tellykeys-hid-keyboard.conf")


@dataclass(frozen=True)
class BluetoothKeyboardDiagnostics:
    bluetoothctl_installed: bool
    controller_powered: bool | None
    peripheral_role: bool | None
    helper_running: bool
    keyboard_connected: bool
    system_fix_installed: bool
    socket_path: str


@dataclass(frozen=True)
class BluetoothKeyboardSetupResult:
    ok: bool
    message: str
    diagnostics: BluetoothKeyboardDiagnostics


def diagnostics() -> BluetoothKeyboardDiagnostics:
    bluetoothctl = shutil.which("bluetoothctl")
    powered: bool | None = None
    peripheral_role: bool | None = None

    if bluetoothctl:
        try:
            result = subprocess.run(
                [bluetoothctl, "show"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                powered = "Powered: yes" in result.stdout
                peripheral_role = "Roles: peripheral" in result.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass

    helper_running = _helper_running()
    return BluetoothKeyboardDiagnostics(
        bluetoothctl_installed=bluetoothctl is not None,
        controller_powered=powered,
        peripheral_role=peripheral_role,
        helper_running=helper_running,
        keyboard_connected=_keyboard_connected() if helper_running else False,
        system_fix_installed=SYSTEM_FIX_DROPIN.exists(),
        socket_path=str(SOCKET_PATH),
    )


def send_text(text: str) -> bool:
    if not text or not _helper_running():
        return False

    payload = json.dumps({"type": "text", "text": text}).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(4)
            client.connect(str(SOCKET_PATH))
            client.sendall(payload)
            reply = client.recv(64)
        return reply.strip() == b"OK"
    except OSError:
        return False


def prepare(apply_system_fix: bool = True) -> BluetoothKeyboardSetupResult:
    if not shutil.which("bluetoothctl"):
        return _setup_result(False, "Bluetooth tools are not installed.")
    if not HELPER_SCRIPT.exists():
        return _setup_result(False, f"Missing helper script: {HELPER_SCRIPT}")

    try:
        HELPER_SCRIPT.chmod(0o755)
    except OSError as exc:
        return _setup_result(False, f"Could not prepare helper script: {exc}")

    SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    service = f"""[Unit]
Description=TellyKeys experimental Bluetooth keyboard helper
After=bluetooth.target

[Service]
Type=simple
ExecStart={HELPER_SCRIPT}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    try:
        SERVICE_PATH.write_text(service, encoding="utf-8")
    except OSError as exc:
        return _setup_result(False, f"Could not write user service: {exc}")

    commands = [
        ["systemctl", "--user", "stop", SERVICE_PATH.name],
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", SERVICE_PATH.name],
        ["bluetoothctl", "pairable", "on"],
        ["bluetoothctl", "discoverable", "on"],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _setup_result(False, f"Setup stopped at {' '.join(command)}: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return _setup_result(False, f"Setup stopped at {' '.join(command)}: {detail}")

    for _ in range(20):
        current = diagnostics()
        if current.helper_running:
            return BluetoothKeyboardSetupResult(
                ok=True,
                message="Bluetooth keyboard helper is running. On the TV, add a Bluetooth accessory named TellyKeys Keyboard.",
                diagnostics=current,
            )
        time.sleep(0.15)

    logs = _service_logs()
    if "UUID already registered" in logs:
        _stop_user_service()
        if not apply_system_fix:
            return _setup_result(
                False,
                "BlueZ still owns the Bluetooth keyboard profile after setup. HID mode cannot run yet.",
            )
        fixed = _apply_system_fix()
        if not fixed.ok:
            return fixed
        return prepare(apply_system_fix=False)
    return _setup_result(False, "Bluetooth keyboard helper did not start. Open Text input help for diagnostics.")


def reset(remove_system_fix: bool = False) -> BluetoothKeyboardSetupResult:
    commands = [
        ["systemctl", "--user", "disable", "--now", SERVICE_PATH.name],
        ["systemctl", "--user", "daemon-reload"],
    ]
    for command in commands:
        try:
            subprocess.run(command, check=False, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            pass

    for path in (SERVICE_PATH, SOCKET_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return _setup_result(False, f"Could not remove {path}: {exc}")

    if remove_system_fix and SYSTEM_FIX_DROPIN.exists():
        result = _remove_system_fix()
        if not result.ok:
            return result

    return _setup_result(True, "Bluetooth keyboard setup has been reset.")


def _setup_result(ok: bool, message: str) -> BluetoothKeyboardSetupResult:
    return BluetoothKeyboardSetupResult(ok=ok, message=message, diagnostics=diagnostics())


def _stop_user_service() -> None:
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", SERVICE_PATH.name],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _service_logs() -> str:
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", SERVICE_PATH.name, "-n", "40", "--no-pager"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
        return result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _apply_system_fix() -> BluetoothKeyboardSetupResult:
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return _setup_result(
            False,
            "BlueZ already owns the keyboard profile, and pkexec is not available for the automatic system fix.",
        )
    if not SYSTEM_FIX_SCRIPT.exists():
        return _setup_result(False, f"Missing system fix script: {SYSTEM_FIX_SCRIPT}")

    try:
        SYSTEM_FIX_SCRIPT.chmod(0o755)
        result = subprocess.run(
            [pkexec, str(SYSTEM_FIX_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _setup_result(False, f"Could not apply Bluetooth system fix: {exc}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return _setup_result(False, f"Bluetooth system fix was cancelled or failed: {detail}")

    time.sleep(1.0)
    return _setup_result(True, "Bluetooth system fix applied.")


def _remove_system_fix() -> BluetoothKeyboardSetupResult:
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return _setup_result(False, "pkexec is not available for removing the Bluetooth system fix.")
    if not SYSTEM_UNFIX_SCRIPT.exists():
        return _setup_result(False, f"Missing system unfix script: {SYSTEM_UNFIX_SCRIPT}")

    try:
        SYSTEM_UNFIX_SCRIPT.chmod(0o755)
        result = subprocess.run(
            [pkexec, str(SYSTEM_UNFIX_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _setup_result(False, f"Could not remove Bluetooth system fix: {exc}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return _setup_result(False, f"Bluetooth system fix removal was cancelled or failed: {detail}")

    time.sleep(1.0)
    return _setup_result(True, "Bluetooth system fix removed.")


def _helper_running() -> bool:
    if not SOCKET_PATH.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.25)
            client.connect(str(SOCKET_PATH))
            client.sendall(b'{"type":"ping"}\n')
            return client.recv(64).strip() == b"OK"
    except OSError:
        return False


def _keyboard_connected() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.25)
            client.connect(str(SOCKET_PATH))
            client.sendall(b'{"type":"status"}\n')
            reply = json.loads(client.recv(512).decode("utf-8"))
            return bool(reply.get("connected"))
    except (OSError, json.JSONDecodeError):
        return False
