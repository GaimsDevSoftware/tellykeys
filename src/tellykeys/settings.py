from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".config" / "tellykeys"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEVICES_DIR = CONFIG_DIR / "devices"


@dataclass
class Device:
    host: str
    label: str | None = None

    @property
    def display_name(self) -> str:
        return self.label or self.host


@dataclass
class Settings:
    last_host: str = ""
    devices: list[Device] = field(default_factory=list)
    buttons: list["ShortcutButton"] = field(default_factory=list)
    app_buttons: list["ShortcutButton"] = field(default_factory=list)
    app_buttons_configured: bool = False
    shows: list["ShortcutButton"] = field(default_factory=list)
    microphone_source: str = ""


@dataclass
class ShortcutButton:
    label: str
    target: str


def load_settings() -> Settings:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Settings()

    devices = []
    for item in data.get("devices", []):
        if isinstance(item, dict) and isinstance(item.get("host"), str):
            label = item.get("label")
            devices.append(Device(item["host"], label if isinstance(label, str) else None))

    buttons = []
    for item in data.get("buttons", []):
        if isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("target"), str):
            label = item["label"].strip()
            target = item["target"].strip()
            if label and target:
                buttons.append(ShortcutButton(label, target))

    app_buttons = []
    if isinstance(data.get("app_buttons_configured"), bool):
        app_buttons_configured = data["app_buttons_configured"]
    else:
        app_buttons_configured = isinstance(data.get("app_buttons"), list)
    for item in data.get("app_buttons", []):
        if isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("target"), str):
            label = item["label"].strip()
            target = item["target"].strip()
            if label and target:
                app_buttons.append(ShortcutButton(label, target))

    shows = []
    for item in data.get("shows", []):
        if isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("target"), str):
            label = item["label"].strip()
            target = item["target"].strip()
            if label and target:
                shows.append(ShortcutButton(label, target))

    last_host = data.get("last_host")
    return Settings(
        last_host=last_host if isinstance(last_host, str) else "",
        devices=devices,
        buttons=buttons,
        app_buttons=app_buttons,
        app_buttons_configured=app_buttons_configured,
        shows=shows,
        microphone_source=data["microphone_source"].strip() if isinstance(data.get("microphone_source"), str) else "",
    )


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "last_host": settings.last_host,
        "devices": [{"host": device.host, "label": device.label} for device in settings.devices],
        "buttons": [{"label": button.label, "target": button.target} for button in settings.buttons],
        "app_buttons": [{"label": button.label, "target": button.target} for button in settings.app_buttons],
        "app_buttons_configured": settings.app_buttons_configured,
        "shows": [{"label": button.label, "target": button.target} for button in settings.shows],
        "microphone_source": settings.microphone_source,
    }
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def remember_device(settings: Settings, host: str, label: str | None = None) -> Settings:
    host = host.strip()
    if not host:
        return settings

    existing = [device for device in settings.devices if device.host != host]
    existing.insert(0, Device(host=host, label=label))
    return Settings(
        last_host=host,
        devices=existing[:12],
        buttons=settings.buttons,
        app_buttons=settings.app_buttons,
        app_buttons_configured=settings.app_buttons_configured,
        shows=settings.shows,
        microphone_source=settings.microphone_source,
    )


def forget_device(settings: Settings, host: str) -> Settings:
    host = host.strip()
    devices = [device for device in settings.devices if device.host != host]
    last_host = "" if settings.last_host == host else settings.last_host
    if not last_host and devices:
        last_host = devices[0].host
    _remove_device_credentials(host)
    return Settings(
        last_host=last_host,
        devices=devices,
        buttons=settings.buttons,
        app_buttons=settings.app_buttons,
        app_buttons_configured=settings.app_buttons_configured,
        shows=settings.shows,
        microphone_source=settings.microphone_source,
    )


def add_button(settings: Settings, label: str, target: str) -> Settings:
    label = label.strip()
    target = target.strip()
    if not label or not target:
        return settings

    buttons = [button for button in settings.buttons if button.label != label]
    buttons.append(ShortcutButton(label=label, target=target))
    return Settings(
        last_host=settings.last_host,
        devices=settings.devices,
        buttons=buttons[:12],
        app_buttons=settings.app_buttons,
        app_buttons_configured=settings.app_buttons_configured,
        shows=settings.shows,
        microphone_source=settings.microphone_source,
    )


def remove_button(settings: Settings, label: str) -> Settings:
    buttons = [button for button in settings.buttons if button.label != label]
    return Settings(
        last_host=settings.last_host,
        devices=settings.devices,
        buttons=buttons,
        app_buttons=settings.app_buttons,
        app_buttons_configured=settings.app_buttons_configured,
        shows=settings.shows,
        microphone_source=settings.microphone_source,
    )


def set_app_buttons(settings: Settings, buttons: list[ShortcutButton]) -> Settings:
    return Settings(
        last_host=settings.last_host,
        devices=settings.devices,
        buttons=settings.buttons,
        app_buttons=buttons[:18],
        app_buttons_configured=True,
        shows=settings.shows,
        microphone_source=settings.microphone_source,
    )


def set_shows(settings: Settings, shows: list[ShortcutButton]) -> Settings:
    return Settings(
        last_host=settings.last_host,
        devices=settings.devices,
        buttons=settings.buttons,
        app_buttons=settings.app_buttons,
        app_buttons_configured=settings.app_buttons_configured,
        shows=shows[:24],
        microphone_source=settings.microphone_source,
    )


def set_microphone_source(settings: Settings, source: str) -> Settings:
    return Settings(
        last_host=settings.last_host,
        devices=settings.devices,
        buttons=settings.buttons,
        app_buttons=settings.app_buttons,
        app_buttons_configured=settings.app_buttons_configured,
        shows=settings.shows,
        microphone_source=source.strip(),
    )


def reset_all() -> Settings:
    try:
        SETTINGS_FILE.unlink()
    except FileNotFoundError:
        pass
    try:
        shutil.rmtree(DEVICES_DIR)
    except FileNotFoundError:
        pass
    return Settings()


def _remove_device_credentials(host: str) -> None:
    safe_host = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in host)
    try:
        shutil.rmtree(DEVICES_DIR / safe_host)
    except FileNotFoundError:
        pass
