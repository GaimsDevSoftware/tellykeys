from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


SERVICE_TYPE = "_androidtvremote2._tcp"


@dataclass(frozen=True)
class DiscoveredDevice:
    name: str
    host: str
    port: int
    interface: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.host})"


def discover_android_tvs(timeout_s: int = 5) -> list[DiscoveredDevice]:
    try:
        result = subprocess.run(
            ["avahi-browse", "-rtp", SERVICE_TYPE],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("avahi-browse mangler. Installer avahi-utils for automatisk TV-sok.") from exc
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
    else:
        if result.returncode not in (0, 1):
            message = (result.stderr or result.stdout or "avahi-browse feilet").strip()
            raise RuntimeError(message)
        output = result.stdout

    devices: dict[str, DiscoveredDevice] = {}
    for line in output.splitlines():
        device = _parse_resolved_line(line)
        if device and device.host not in devices:
            devices[device.host] = device
    return list(devices.values())


def _parse_resolved_line(line: str) -> DiscoveredDevice | None:
    if not line.startswith("="):
        return None

    fields = line.split(";")
    if len(fields) < 9:
        return None

    _kind, interface, protocol, name, service, _domain, _hostname, host, port_text, *_txt = fields
    if protocol != "IPv4" or service != SERVICE_TYPE:
        return None

    try:
        port = int(port_text)
    except ValueError:
        return None

    return DiscoveredDevice(
        name=_decode_avahi_escapes(name),
        host=host,
        port=port,
        interface=interface,
    )


def _decode_avahi_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 10))

    return re.sub(r"\\([0-9]{3})", replace, value)

