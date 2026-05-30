from __future__ import annotations

import argparse
import asyncio
import os
import threading
import warnings
from concurrent.futures import Future
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth

from .discovery import discover_android_tvs
from .remote import (
    AsyncRunner,
    BluetoothKeyboardNotConnected,
    BluetoothKeyboardSetup,
    TellyKeysRemote,
    TextInputDiagnostics,
    TvStatus,
)
from .settings import (
    Settings,
    ShortcutButton,
    forget_device,
    load_settings,
    remember_device,
    reset_all,
    save_settings,
    set_app_buttons,
    set_bluetooth_keyboard_enabled,
    set_microphone_source,
)


warnings.filterwarnings(
    "ignore",
    message="Attribute's length must be >= 1 and <= 64.*",
    category=UserWarning,
)

PAIRING_CODE_LENGTH = 6
DEFAULT_MICROPHONE_ID = "__default__"


class PairingCodeInput(Gtk.Box):
    def __init__(self, on_complete: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._on_complete = on_complete
        self._updating = False
        self.length = PAIRING_CODE_LENGTH
        self.entries: list[Gtk.Entry] = []
        self.set_length(self.length)

    def set_length(self, length: int) -> None:
        self.length = length
        for child in self.get_children():
            self.remove(child)
        self.entries = []
        for index in range(length):
            entry = Gtk.Entry()
            entry.set_width_chars(1)
            entry.set_max_width_chars(1)
            entry.set_alignment(0.5)
            entry.get_style_context().add_class("code-cell")
            entry.connect("changed", self._on_changed, index)
            entry.connect("key-press-event", self._on_key_press, index)
            self.pack_start(entry, True, True, 0)
            self.entries.append(entry)
        self.show_all()

    def focus_first(self) -> None:
        if self.entries:
            self.entries[0].grab_focus()

    def clear(self) -> None:
        self._updating = True
        for entry in self.entries:
            entry.set_text("")
        self._updating = False

    def code(self) -> str:
        return "".join(entry.get_text().strip() for entry in self.entries)

    def _on_changed(self, entry: Gtk.Entry, index: int) -> None:
        if self._updating:
            return

        raw = entry.get_text()
        clean = self._clean(raw)
        if len(clean) > 1:
            self.fill(clean)
            return

        if raw != clean:
            self._updating = True
            entry.set_text(clean)
            self._updating = False

        if clean and index + 1 < len(self.entries):
            self.entries[index + 1].grab_focus()
        elif clean and self.is_complete():
            self._on_complete()

    def _on_key_press(self, entry: Gtk.Entry, event: Gdk.EventKey, index: int) -> bool:
        if event.keyval == Gdk.KEY_BackSpace and not entry.get_text() and index > 0:
            previous = self.entries[index - 1]
            previous.set_text("")
            previous.grab_focus()
            return True
        return False

    def fill(self, text: str) -> None:
        clean = self._clean(text)[: self.length]

        self._updating = True
        for index, entry in enumerate(self.entries):
            entry.set_text(clean[index] if index < len(clean) else "")
        self._updating = False

        if self.is_complete():
            self._on_complete()
        else:
            next_index = min(len(clean), len(self.entries) - 1)
            self.entries[next_index].grab_focus()

    def is_complete(self) -> bool:
        return len(self.code()) == self.length

    def _clean(self, text: str) -> str:
        return "".join(ch for ch in text.upper() if ch in "0123456789ABCDEF")


class TellyKeysWindow(Gtk.Window):
    def __init__(self, use_tray: bool = False, start_hidden: bool = False) -> None:
        super().__init__(title="TellyKeys")
        self.set_default_size(420, 620)
        self.set_size_request(320, 360)
        self.set_border_width(0)
        self._install_css()

        self.settings: Settings = load_settings()
        self.connected = False
        self.compact_mode = False
        self.apps_manage_mode = False
        self.current_status: TvStatus | None = None
        self.use_tray = use_tray
        self.tray_icon: Gtk.StatusIcon | None = None
        self.remote = TellyKeysRemote()
        self.remote.set_bluetooth_keyboard_enabled(self.settings.bluetooth_keyboard_enabled)
        self.remote.set_microphone_source(self.settings.microphone_source)
        self.remote.set_status_callback(lambda status: GLib.idle_add(self.show_tv_status, status))
        self.remote.set_voice_status_callback(lambda message, active: GLib.idle_add(self.show_voice_status, message, active))
        self.runner = AsyncRunner()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.add(self.stack)

        main_scroll = self._content_scroll(Gtk.PolicyType.NEVER)
        self.stack.add_named(main_scroll, "remote")

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root = self.root
        root.get_style_context().add_class("app-shell")
        main_scroll.add(root)

        header = Gtk.Box(spacing=12)
        header.get_style_context().add_class("header")
        root.pack_start(header, False, False, 0)

        icon_wrap = Gtk.Box()
        icon_wrap.get_style_context().add_class("app-icon")
        icon = Gtk.Image.new_from_icon_name("video-display-symbolic", Gtk.IconSize.DIALOG)
        icon_wrap.pack_start(icon, True, True, 0)
        header.pack_start(icon_wrap, False, False, 0)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.pack_start(title_box, True, True, 0)
        title = Gtk.Label(label="TellyKeys", xalign=0)
        title.get_style_context().add_class("title")
        title_box.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label="Google TV Remote", xalign=0)
        subtitle.get_style_context().add_class("subtitle")
        title_box.pack_start(subtitle, False, False, 0)

        self.status_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.status_card.get_style_context().add_class("status-card")
        root.pack_start(self.status_card, False, False, 0)

        self.spinner = Gtk.Spinner()
        self.status_card.pack_start(self.spinner, False, False, 0)

        self.status_label = Gtk.Label(label="Finding your TV ...", xalign=0.5)
        self.status_label.set_line_wrap(True)
        self.status_label.set_justify(Gtk.Justification.CENTER)
        self.status_label.get_style_context().add_class("status-text")
        self.status_card.pack_start(self.status_label, False, False, 0)

        self.primary_button = Gtk.Button(label="Find TV")
        self.primary_button.get_style_context().add_class("primary")
        self.primary_button.connect("clicked", self.on_primary_action)
        root.pack_start(self.primary_button, False, False, 0)

        self.code_row = Gtk.Box(spacing=10)
        root.pack_start(self.code_row, False, False, 0)
        self.code_input = PairingCodeInput(lambda: self.on_finish_pairing(None))
        self.code_row.pack_start(self.code_input, True, True, 0)
        self.finish_button = Gtk.Button(label="Done")
        self.finish_button.get_style_context().add_class("primary")
        self.finish_button.connect("clicked", self.on_finish_pairing)
        self.code_row.pack_start(self.finish_button, False, False, 0)
        self.code_row.set_no_show_all(True)
        self.code_row.hide()

        self.remote_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.remote_panel.set_no_show_all(True)
        root.pack_start(self.remote_panel, True, True, 0)
        self.remote_panel.pack_start(self._build_dpad(), False, False, 0)
        self.remote_panel.pack_start(self._build_media(), False, False, 0)
        self.remote_panel.pack_start(self._build_text_box(), False, False, 0)
        self.remote_panel.pack_start(self._build_action_row(), False, False, 0)
        self.remote_panel.hide()

        nav_row = Gtk.Box(spacing=8)
        options_button = Gtk.Button(label="Options")
        options_button.connect("clicked", self.on_show_settings)
        nav_row.pack_start(options_button, True, True, 0)
        help_button = Gtk.Button(label="Help")
        help_button.connect("clicked", self.on_show_help)
        nav_row.pack_start(help_button, True, True, 0)
        root.pack_end(nav_row, False, False, 0)

        self.settings_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        settings_page = self.settings_page
        settings_page.get_style_context().add_class("app-shell")
        self.stack.add_named(settings_page, "settings")

        settings_header = Gtk.Box(spacing=8)
        settings_page.pack_start(settings_header, False, False, 0)
        back_button = Gtk.Button(label="Back")
        back_button.connect("clicked", self.on_show_remote)
        settings_header.pack_start(back_button, False, False, 0)
        settings_title = Gtk.Label(label="Settings", xalign=0)
        settings_title.get_style_context().add_class("title")
        settings_header.pack_start(settings_title, True, True, 0)

        self.settings_notebook = Gtk.Notebook()
        settings_notebook = self.settings_notebook
        settings_notebook.set_scrollable(True)
        settings_notebook.connect("switch-page", lambda *_: self.schedule_window_fit())
        settings_page.pack_start(settings_notebook, True, True, 0)

        tv_scroll, tv_box = self._settings_page()
        text_scroll, text_box = self._settings_page()
        buttons_scroll, buttons_box = self._settings_page()
        system_scroll, system_box = self._settings_page()
        settings_notebook.append_page(tv_scroll, Gtk.Label(label="TV"))
        settings_notebook.append_page(text_scroll, Gtk.Label(label="Text"))
        settings_notebook.append_page(buttons_scroll, Gtk.Label(label="Buttons"))
        settings_notebook.append_page(system_scroll, Gtk.Label(label="System"))

        top_row = Gtk.Box(spacing=8)
        tv_box.pack_start(top_row, False, False, 0)

        self.host_entry = Gtk.Entry()
        self.host_entry.set_placeholder_text("Google TV IP address")
        self.host_entry.set_text(self.settings.last_host)
        self.host_entry.connect("activate", self.on_connect)
        top_row.pack_start(self.host_entry, True, True, 0)

        self.device_combo = Gtk.ComboBoxText()
        self._refresh_device_combo()
        self.device_combo.connect("changed", self.on_device_selected)
        top_row.pack_start(self.device_combo, False, False, 0)

        self.scan_button = Gtk.Button(label="Scan")
        self.scan_button.connect("clicked", self.on_scan)
        top_row.pack_start(self.scan_button, False, False, 0)

        manage_row = Gtk.Box(spacing=8)
        tv_box.pack_start(manage_row, False, False, 0)
        self.forget_button = Gtk.Button(label="Forget selected TV")
        self.forget_button.connect("clicked", self.on_forget_device)
        manage_row.pack_start(self.forget_button, True, True, 0)

        pair_row = Gtk.Box(spacing=8)
        tv_box.pack_start(pair_row, False, False, 0)
        self.pair_button = Gtk.Button(label="Start pairing")
        self.pair_button.connect("clicked", self.on_pair)
        pair_row.pack_start(self.pair_button, True, True, 0)

        connect_row = Gtk.Box(spacing=8)
        tv_box.pack_start(connect_row, False, False, 0)
        self.connect_button = Gtk.Button(label="Connect")
        self.connect_button.connect("clicked", self.on_connect)
        connect_row.pack_start(self.connect_button, True, True, 0)
        self.power_button = Gtk.Button(label="Power")
        self.power_button.connect("clicked", lambda *_: self.send_key("POWER"))
        connect_row.pack_start(self.power_button, True, True, 0)

        manual_launch_label = Gtk.Label(label="Manual launch", xalign=0)
        manual_launch_label.get_style_context().add_class("section-label")
        tv_box.pack_start(manual_launch_label, False, False, 0)
        tv_box.pack_start(self._build_launch_box(), False, False, 0)

        text_help_row = Gtk.Box(spacing=8)
        text_box.pack_start(text_help_row, False, False, 0)
        text_help_button = Gtk.Button(label="Text input help")
        text_help_button.connect("clicked", self.on_text_input_help)
        text_help_row.pack_start(text_help_button, True, True, 0)
        keyboard_settings_button = Gtk.Button(label="Open TV keyboard settings")
        keyboard_settings_button.connect("clicked", self.on_open_tv_keyboard_settings)
        text_help_row.pack_start(keyboard_settings_button, True, True, 0)

        self.bluetooth_keyboard_check = Gtk.CheckButton(label="Use Bluetooth keyboard for text")
        self.bluetooth_keyboard_check.set_active(self.settings.bluetooth_keyboard_enabled)
        self.bluetooth_keyboard_check.connect("toggled", self.on_bluetooth_keyboard_toggled)
        text_box.pack_start(self.bluetooth_keyboard_check, False, False, 0)

        microphone_label = Gtk.Label(label="Microphone for voice search", xalign=0)
        microphone_label.get_style_context().add_class("section-label")
        text_box.pack_start(microphone_label, False, False, 0)
        microphone_row = Gtk.Box(spacing=8)
        text_box.pack_start(microphone_row, False, False, 0)
        self.microphone_combo = Gtk.ComboBoxText()
        self.updating_microphone_combo = False
        self.microphone_combo.connect("changed", self.on_microphone_selected)
        microphone_row.pack_start(self.microphone_combo, True, True, 0)
        refresh_microphones_button = Gtk.Button(label="Refresh")
        refresh_microphones_button.connect("clicked", self.on_refresh_microphones)
        microphone_row.pack_start(refresh_microphones_button, False, False, 0)
        test_microphone_button = Gtk.Button(label="Test mic")
        test_microphone_button.connect("clicked", self.on_test_microphone)
        microphone_row.pack_start(test_microphone_button, False, False, 0)
        self.microphone_status_label = Gtk.Label(label="", xalign=0)
        self.microphone_status_label.set_line_wrap(True)
        self.microphone_status_label.get_style_context().add_class("hint")
        text_box.pack_start(self.microphone_status_label, False, False, 0)
        self.refresh_microphone_combo()

        setup_bluetooth_button = Gtk.Button(label="Set up Bluetooth keyboard")
        setup_bluetooth_button.connect("clicked", self.on_setup_bluetooth_keyboard)
        text_box.pack_start(setup_bluetooth_button, False, False, 0)
        reset_bluetooth_button = Gtk.Button(label="Reset Bluetooth keyboard")
        reset_bluetooth_button.connect("clicked", self.on_reset_bluetooth_keyboard)
        text_box.pack_start(reset_bluetooth_button, False, False, 0)

        button_settings_label = Gtk.Label(label="Apps buttons", xalign=0)
        button_settings_label.get_style_context().add_class("section-label")
        buttons_box.pack_start(button_settings_label, False, False, 0)

        add_current_button = Gtk.Button(label="Add currently open app")
        add_current_button.connect("clicked", self.on_add_current_app_button)
        buttons_box.pack_start(add_current_button, False, False, 0)

        self.custom_button_combo = Gtk.ComboBoxText()
        self._refresh_custom_button_combo()
        self.custom_button_combo.connect("changed", self.on_custom_button_selected)
        buttons_box.pack_start(self.custom_button_combo, False, False, 0)

        custom_row = Gtk.Box(spacing=8)
        buttons_box.pack_start(custom_row, False, False, 0)
        self.custom_label_entry = Gtk.Entry()
        self.custom_label_entry.set_placeholder_text("Button name")
        custom_row.pack_start(self.custom_label_entry, True, True, 0)
        self.custom_target_entry = Gtk.Entry()
        self.custom_target_entry.set_placeholder_text("App ID or URL")
        custom_row.pack_start(self.custom_target_entry, True, True, 0)

        custom_actions = Gtk.Box(spacing=8)
        buttons_box.pack_start(custom_actions, False, False, 0)
        add_custom_button = Gtk.Button(label="Save button")
        add_custom_button.connect("clicked", self.on_add_custom_button)
        custom_actions.pack_start(add_custom_button, True, True, 0)
        remove_custom_button = Gtk.Button(label="Delete button")
        remove_custom_button.connect("clicked", self.on_remove_custom_button)
        custom_actions.pack_start(remove_custom_button, True, True, 0)
        restore_defaults_button = Gtk.Button(label="Restore defaults")
        restore_defaults_button.connect("clicked", self.on_restore_default_app_buttons)
        buttons_box.pack_start(restore_defaults_button, False, False, 0)

        system_label = Gtk.Label(label="Reset", xalign=0)
        system_label.get_style_context().add_class("section-label")
        system_box.pack_start(system_label, False, False, 0)
        self.reset_button = Gtk.Button(label="Reset all")
        self.reset_button.connect("clicked", self.on_reset_all)
        system_box.pack_start(self.reset_button, False, False, 0)

        self.help_cards: list[tuple[Gtk.Widget, str]] = []
        self.stack.add_named(self._build_help_page(), "help")

        self.connect("delete-event", self.on_delete_event)
        self.connect("destroy", self.on_destroy)
        self.connect("key-press-event", self.on_key_press)
        self.connect("size-allocate", self.on_size_allocate)
        demo_mode = os.environ.get("TELLYKEYS_DEMO") == "1"

        self.show_all()
        self.code_row.hide()
        self.remote_panel.hide()
        if self.use_tray:
            self.setup_tray()
        if start_hidden and self.use_tray:
            self.hide()
        if demo_mode:
            self.show_demo_state()
        else:
            GLib.timeout_add(350, self.start_auto_setup)
        GLib.timeout_add_seconds(3, self.refresh_bluetooth_text_status)
        self.schedule_window_fit()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window { background: #eef2f7; }
            .app-shell { padding: 16px; }
            .app-shell.compact { padding: 12px; }
            .settings-panel { padding: 18px; }
            .settings-panel.compact { padding: 12px; }
            .help-card {
              background: #ffffff;
              border: 1px solid #d8dee8;
              border-radius: 12px;
              padding: 14px;
              color: #111827;
            }
            .help-card.compact { padding: 10px; }
            .help-icon {
              min-width: 42px;
              min-height: 42px;
              border-radius: 12px;
              background: #eef4ff;
              color: #246bfe;
            }
            .help-title {
              font-size: 16px;
              font-weight: 800;
              color: #111827;
            }
            .help-body {
              font-size: 13px;
              color: #475467;
            }
            .shortcut-row { margin-top: 4px; }
            .keycap {
              min-width: 38px;
              min-height: 28px;
              border-radius: 7px;
              background: #f3f6fb;
              border: 1px solid #cfd8e6;
              color: #111827;
              font-size: 12px;
              font-weight: 800;
            }
            .header { margin-bottom: 2px; }
            .app-icon {
              min-width: 46px;
              min-height: 46px;
              border-radius: 14px;
              background: #ffffff;
              color: #246bfe;
              border: 1px solid #d7dee9;
            }
            .title { font-size: 24px; font-weight: 800; color: #111827; }
            .title.compact { font-size: 22px; }
            .subtitle { font-size: 14px; color: #667085; }
            .subtitle.compact { font-size: 12px; }
            .section-label { font-size: 14px; font-weight: 700; color: #667085; }
            .hint { font-size: 12px; color: #667085; }
            .status-card {
              background: #ffffff;
              border: 1px solid #d8dee8;
              border-radius: 14px;
              padding: 14px;
              color: #1f2933;
              box-shadow: 0 10px 30px rgba(16, 24, 40, 0.08);
            }
            .status-card.compact { padding: 12px; }
            .status-text {
              font-size: 16px;
              font-weight: 600;
              color: #111827;
            }
            .status-text.compact { font-size: 15px; }
            button {
              min-height: 40px;
              border-radius: 10px;
              font-size: 15px;
            }
            button.compact {
              min-height: 34px;
              font-size: 12px;
              padding: 3px 8px;
            }
            entry {
              min-height: 52px;
              background: #ffffff;
              color: #111827;
              border: 2px solid #246bfe;
              border-radius: 12px;
              padding: 8px 16px;
              font-size: 20px;
            }
            entry.compact {
              min-height: 40px;
              font-size: 16px;
              padding: 4px 10px;
            }
            .code-cell {
              min-height: 66px;
              font-size: 28px;
              font-weight: 800;
              padding: 6px 0;
            }
            .code-cell.compact {
              min-height: 44px;
              font-size: 18px;
            }
            .primary {
              min-height: 48px;
              background: #246bfe;
              color: #ffffff;
              font-size: 16px;
              font-weight: 700;
            }
            .primary.compact {
              min-height: 42px;
              font-size: 14px;
            }
            .remote-button {
              min-height: 46px;
              border-radius: 12px;
              background: #ffffff;
              color: #1f2937;
              border: 1px solid #d7dee9;
              font-size: 15px;
              font-weight: 600;
              box-shadow: 0 4px 12px rgba(16, 24, 40, 0.06);
            }
            .remote-button.compact {
              min-height: 38px;
              border-radius: 8px;
              font-size: 13px;
              padding: 2px 6px;
            }
            .remote-button:hover { background: #f8fbff; }
            .remote-button:active { background: #e9f1ff; }
            .danger-button {
              background: #fff5f5;
              color: #b42318;
              border: 1px solid #f4b6b0;
              font-weight: 700;
            }
            .ghost-button {
              background: #f8fafc;
              color: #344054;
              border: 1px solid #d7dee9;
            }
            .ok-button {
              min-height: 56px;
              background: #246bfe;
              color: #ffffff;
              font-size: 17px;
              font-weight: 800;
            }
            .ok-button:hover {
              background: #1d5bd8;
              color: #ffffff;
            }
            .ok-button:active {
              background: #174fc4;
              color: #ffffff;
            }
            .ok-button.compact {
              min-height: 46px;
              font-size: 15px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.runner.loop)
        self.runner.loop.run_forever()

    def on_size_allocate(self, _widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        compact = allocation.width < 390 or allocation.height < 820
        if compact == self.compact_mode:
            return
        self.compact_mode = compact
        self.apply_compact_mode(compact)

    def apply_compact_mode(self, compact: bool) -> None:
        for widget in self._walk_widgets(self):
            context = widget.get_style_context()
            if compact:
                context.add_class("compact")
            else:
                context.remove_class("compact")
        self.root.set_spacing(10 if compact else 16)
        if self.remote_panel:
            self.remote_panel.set_spacing(8 if compact else 12)

    def fit_window_to_page(self) -> bool:
        if not self.get_visible():
            return False

        width, height = self._preferred_window_size()
        self.resize(width, height)
        return False

    def schedule_window_fit(self) -> None:
        GLib.idle_add(self.fit_window_to_page)
        GLib.timeout_add(120, self.fit_window_to_page)

    def _preferred_window_size(self) -> tuple[int, int]:
        screen = self.get_screen()
        monitor = screen.get_monitor_at_window(self.get_window()) if self.get_window() else screen.get_primary_monitor()
        geometry = screen.get_monitor_workarea(monitor)
        max_width = max(320, geometry.width - 24)
        max_height = max(360, geometry.height - 48)

        page = self.stack.get_visible_child_name() or "remote"
        base_width = 560 if page in {"settings", "help"} else 420
        width = min(max_width, max(320, base_width))

        content = self._visible_content_widget(page)
        natural_height = 620
        if content:
            _minimum, natural = content.get_preferred_height()
            reserve = 44 if page == "remote" else 96
            natural_height = natural + reserve

        height = min(max_height, max(360, natural_height))
        return int(width), int(height)

    def _visible_content_widget(self, page: str) -> Gtk.Widget | None:
        if page == "remote":
            return self.root
        if page == "settings":
            return self.settings_page
        if page == "help":
            return getattr(self, "help_page", None)
        return self.stack.get_visible_child()

    def _walk_widgets(self, widget: Gtk.Widget) -> list[Gtk.Widget]:
        widgets = [widget]
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                widgets.extend(self._walk_widgets(child))
        return widgets

    def _settings_page(self) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroll = self._content_scroll(Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(380)
        scroll.set_min_content_height(320)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("settings-panel")
        scroll.add(box)
        return scroll, box

    def _content_scroll(self, horizontal_policy: Gtk.PolicyType) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(horizontal_policy, Gtk.PolicyType.AUTOMATIC)
        if hasattr(scroll, "set_propagate_natural_height"):
            scroll.set_propagate_natural_height(True)
        if hasattr(scroll, "set_propagate_natural_width"):
            scroll.set_propagate_natural_width(True)
        return scroll

    def on_show_settings(self, _button: Gtk.Button) -> None:
        self.stack.set_visible_child_name("settings")
        self.schedule_window_fit()

    def on_show_remote(self, _button: Gtk.Button) -> None:
        self.stack.set_visible_child_name("remote")
        self.schedule_window_fit()

    def on_show_help(self, _button: Gtk.Button) -> None:
        self.stack.set_visible_child_name("help")
        self.schedule_window_fit()

    def _build_help_page(self) -> Gtk.ScrolledWindow:
        scroll = self._content_scroll(Gtk.PolicyType.NEVER)
        self.help_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page = self.help_page
        page.get_style_context().add_class("app-shell")
        scroll.add(page)

        header = Gtk.Box(spacing=8)
        page.pack_start(header, False, False, 0)
        back_button = Gtk.Button(label="Back")
        back_button.connect("clicked", self.on_show_remote)
        header.pack_start(back_button, False, False, 0)
        title = Gtk.Label(label="Help", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search help")
        search.connect("search-changed", self.on_help_search_changed)
        page.pack_start(search, False, False, 0)

        overview = Gtk.Label(label="Overview", xalign=0)
        overview.get_style_context().add_class("section-label")
        page.pack_start(overview, False, False, 0)

        for icon, title_text, body, keywords in [
            (
                "network-wireless-symbolic",
                "Connect to your TV",
                "TellyKeys finds Google TV devices on your network. If pairing appears, type the six-character code shown on the TV.",
                "overview connect tv pair pairing code scan network",
            ),
            (
                "input-gaming-symbolic",
                "Use the remote",
                "The arrow pad, OK, Back, Home, volume, mute and media buttons work like a simple physical remote. Keyboard shortcuts also work when you are not typing in a text field.",
                "overview remote arrows ok back home volume mute media play keyboard shortcuts",
            ),
            (
                "input-keyboard-symbolic",
                "Send text",
                "Type into Send text to TV. TellyKeys picks the best available method: Bluetooth keyboard, YouTube search link, ADB, or Google TV text input.",
                "overview text keyboard bluetooth youtube adb search input",
            ),
            (
                "audio-input-microphone-symbolic",
                "Voice search",
                "Use Voice search to speak from your Linux microphone. TellyKeys streams microphone audio through the Google TV remote protocol when the TV accepts voice sessions.",
                "overview voice search microphone mic audio google assistant youtube",
            ),
            (
                "view-grid-symbolic",
                "Open apps",
                "Use Apps on the remote to launch common apps. Edit, remove, replace, or restore those buttons from Settings > Buttons.",
                "overview apps shortcuts buttons edit remove replace restore youtube netflix prime disney kodi launch",
            ),
            (
                "preferences-system-symbolic",
                "Settings",
                "Settings has pages for TV setup, text/keyboard, app buttons, and reset tools. Use it only when you need to change something.",
                "overview settings options tv text buttons system reset",
            ),
            (
                "dialog-warning-symbolic",
                "When something does not work",
                "Open Settings > Text > Text input help for diagnostics. For Bluetooth text, make sure TellyKeys Keyboard is paired with the TV.",
                "troubleshooting diagnostics help bluetooth pair text sony bravia",
            ),
        ]:
            card = self._help_card(icon, title_text, body)
            self.help_cards.append((card, f"{title_text} {body} {keywords}".lower()))
            page.pack_start(card, False, False, 0)

        shortcuts_label = Gtk.Label(label="Keyboard shortcuts", xalign=0)
        shortcuts_label.get_style_context().add_class("section-label")
        page.pack_start(shortcuts_label, False, False, 0)

        shortcuts_card = self._keyboard_shortcuts_card()
        shortcuts_text = (
            "keyboard shortcuts arrow keys d-pad enter ok space play pause backspace escape back home plus minus "
            "volume mute m power p"
        )
        self.help_cards.append((shortcuts_card, shortcuts_text))
        page.pack_start(shortcuts_card, False, False, 0)

        return scroll

    def _help_card(self, icon_name: str, title: str, body: str) -> Gtk.Box:
        card = Gtk.Box(spacing=12)
        card.get_style_context().add_class("help-card")

        icon_wrap = Gtk.Box()
        icon_wrap.get_style_context().add_class("help-icon")
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        icon_wrap.pack_start(icon, True, True, 0)
        card.pack_start(icon_wrap, False, False, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.pack_start(text_box, True, True, 0)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("help-title")
        title_label.set_line_wrap(True)
        text_box.pack_start(title_label, False, False, 0)
        body_label = Gtk.Label(label=body, xalign=0)
        body_label.get_style_context().add_class("help-body")
        body_label.set_line_wrap(True)
        text_box.pack_start(body_label, False, False, 0)
        return card

    def _keyboard_shortcuts_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("help-card")

        intro = Gtk.Label(label="These work when the main window has focus and you are not typing in a text field.", xalign=0)
        intro.get_style_context().add_class("help-body")
        intro.set_line_wrap(True)
        card.pack_start(intro, False, False, 0)

        for keys, action in [
            ("↑ ↓ ← →", "Move on the TV"),
            ("Enter", "OK / select"),
            ("Space", "Play or pause"),
            ("Backspace / Esc", "Back"),
            ("Home", "Home"),
            ("+ / -", "Volume up or down"),
            ("M", "Mute"),
            ("P", "Power"),
        ]:
            card.pack_start(self._shortcut_row(keys, action), False, False, 0)
        return card

    def _shortcut_row(self, keys: str, action: str) -> Gtk.Box:
        row = Gtk.Box(spacing=10)
        row.get_style_context().add_class("shortcut-row")

        key_label = Gtk.Label(label=keys, xalign=0.5)
        key_label.get_style_context().add_class("keycap")
        row.pack_start(key_label, False, False, 0)

        action_label = Gtk.Label(label=action, xalign=0)
        action_label.get_style_context().add_class("help-body")
        action_label.set_line_wrap(True)
        row.pack_start(action_label, True, True, 0)
        return row

    def on_help_search_changed(self, search: Gtk.SearchEntry) -> None:
        query = search.get_text().strip().lower()
        for card, haystack in self.help_cards:
            card.set_visible(not query or query in haystack)
        self.schedule_window_fit()

    def _build_dpad(self) -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.set_column_homogeneous(True)
        for label, key, col, row in [
            ("↑", "DPAD_UP", 1, 0),
            ("←", "DPAD_LEFT", 0, 1),
            ("OK", "DPAD_CENTER", 1, 1),
            ("→", "DPAD_RIGHT", 2, 1),
            ("↓", "DPAD_DOWN", 1, 2),
            ("Back", "BACK", 0, 3),
            ("Home", "HOME", 1, 3),
            ("Menu", "MENU", 2, 3),
        ]:
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("remote-button")
            if key == "DPAD_CENTER":
                button.get_style_context().add_class("ok-button")
            button.connect("clicked", lambda _button, key_code=key: self.send_key(key_code))
            grid.attach(button, col, row, 1, 1)
        return grid

    def _build_media(self) -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.set_column_homogeneous(True)
        for label, key, col, row in [
            ("Vol −", "VOLUME_DOWN", 0, 0),
            ("Mute", "MUTE", 1, 0),
            ("Vol +", "VOLUME_UP", 2, 0),
            ("⏮", "MEDIA_PREVIOUS", 0, 1),
            ("▶", "MEDIA_PLAY_PAUSE", 1, 1),
            ("⏭", "MEDIA_NEXT", 2, 1),
        ]:
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("remote-button")
            button.connect("clicked", lambda _button, key_code=key: self.send_key(key_code))
            grid.attach(button, col, row, 1, 1)
        return grid

    def _build_text_box(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(spacing=8)
        self.text_entry = Gtk.Entry()
        self.text_entry.set_placeholder_text("Send text to TV")
        self.text_entry.connect("activate", self.on_send_text)
        row.pack_start(self.text_entry, True, True, 0)
        button = Gtk.Button(label="Send")
        button.connect("clicked", self.on_send_text)
        row.pack_start(button, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.text_method_label = Gtk.Label(label="Text: automatic", xalign=0)
        self.text_method_label.get_style_context().add_class("hint")
        box.pack_start(self.text_method_label, False, False, 0)
        return box

    def _build_action_row(self) -> Gtk.Box:
        row = Gtk.Box(spacing=8)
        self.voice_button = Gtk.Button(label="Voice search")
        self.voice_button.get_style_context().add_class("remote-button")
        self.voice_button.connect("clicked", self.on_voice_search)
        row.pack_start(self.voice_button, True, True, 0)
        self.apps_row = self._build_apps_menu()
        row.pack_start(self.apps_row, True, True, 0)
        return row

    def _build_apps_menu(self) -> Gtk.Box:
        row = Gtk.Box(spacing=8)
        self.apps_button = Gtk.MenuButton(label="Apps")
        self.apps_button.get_style_context().add_class("remote-button")
        self.apps_popover = Gtk.Popover.new(self.apps_button)
        self.apps_popover.set_position(Gtk.PositionType.TOP)
        self.apps_button.set_popover(self.apps_popover)
        self.rebuild_shortcuts()
        row.pack_start(self.apps_button, True, True, 0)
        return row

    def _build_shortcuts_grid(self) -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        grid.set_column_homogeneous(True)
        for index, (label, target) in enumerate(self.shortcuts()):
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("remote-button")
            button.connect("clicked", self.on_shortcut_clicked, target)
            grid.attach(button, index % 3, index // 3, 1, 1)
        return grid

    def _build_apps_manage_list(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for label, target in self.shortcuts():
            row = Gtk.Box(spacing=6)
            launch_button = Gtk.Button(label=label)
            launch_button.get_style_context().add_class("remote-button")
            launch_button.connect("clicked", self.on_shortcut_clicked, target)
            row.pack_start(launch_button, True, True, 0)

            edit_button = Gtk.Button(label="Edit")
            edit_button.get_style_context().add_class("ghost-button")
            edit_button.connect("clicked", self.on_edit_app_button_from_popover, label)
            row.pack_start(edit_button, False, False, 0)

            delete_button = Gtk.Button(label="Remove")
            delete_button.get_style_context().add_class("danger-button")
            delete_button.connect("clicked", self.on_remove_app_button_from_popover, label)
            row.pack_start(delete_button, False, False, 0)
            box.pack_start(row, False, False, 0)
        return box

    def on_shortcut_clicked(self, _button: Gtk.Button, target: str) -> None:
        if hasattr(self, "apps_popover"):
            self.apps_popover.popdown()
        if target == "TV_SETTINGS":
            self.open_tv_settings()
        else:
            self.launch(target)

    def shortcuts(self) -> list[tuple[str, str]]:
        if self.settings.app_buttons_configured:
            return [(button.label, self.normalize_shortcut_target(button.target)) for button in self.settings.app_buttons]

        return self.default_shortcuts() + [
            (button.label, self.normalize_shortcut_target(button.target)) for button in self.settings.buttons
        ]

    def default_shortcuts(self) -> list[tuple[str, str]]:
        return [
            ("YouTube", "https://www.youtube.com"),
            ("Netflix", "com.netflix.ninja"),
            ("Prime", "com.amazon.amazonvideo.livingroom"),
            ("Disney+", "com.disney.disneyplus"),
            ("Kodi", "org.xbmc.kodi"),
            ("Settings", "TV_SETTINGS"),
        ]

    def normalize_shortcut_target(self, target: str) -> str:
        if target in {"SETTINGS", "com.android.tv.settings"}:
            return "TV_SETTINGS"
        return target

    def set_shortcuts(self, shortcuts: list[tuple[str, str]]) -> None:
        self.settings = set_app_buttons(
            self.settings,
            [ShortcutButton(label=label, target=target) for label, target in shortcuts if label and target],
        )

    def on_toggle_apps_manage(self, _button: Gtk.Button) -> None:
        self.apps_manage_mode = not self.apps_manage_mode
        self.rebuild_shortcuts(keep_open=True)

    def on_edit_app_button_from_popover(self, _button: Gtk.Button, label: str) -> None:
        for button_label, target in self.shortcuts():
            if button_label == label:
                self.custom_label_entry.set_text(button_label)
                self.custom_target_entry.set_text(target)
                self.custom_button_combo.set_active_id(button_label)
                break
        self.apps_popover.popdown()
        self.settings_notebook.set_current_page(2)
        self.stack.set_visible_child_name("settings")
        self.schedule_window_fit()

    def on_remove_app_button_from_popover(self, _button: Gtk.Button, label: str) -> None:
        if not self.confirm("Remove app button?", f"Remove {label} from Apps? You can restore defaults later."):
            return
        self.remove_app_button(label)
        self.rebuild_shortcuts(keep_open=True)

    def remove_app_button(self, label: str) -> None:
        shortcuts = [(button_label, target) for button_label, target in self.shortcuts() if button_label != label]
        self._save_apps_buttons(shortcuts)
        self.custom_label_entry.set_text("")
        self.custom_target_entry.set_text("")
        self.set_status(f"Removed {label} from Apps.")

    def _build_launch_box(self) -> Gtk.Box:
        row = Gtk.Box(spacing=8)
        self.launch_entry = Gtk.Entry()
        self.launch_entry.set_placeholder_text("Open app ID or URL once")
        self.launch_entry.connect("activate", self.on_launch_custom)
        row.pack_start(self.launch_entry, True, True, 0)
        button = Gtk.Button(label="Launch")
        button.connect("clicked", self.on_launch_custom)
        row.pack_start(button, False, False, 0)
        return row

    def start_auto_setup(self) -> bool:
        if self.host_entry.get_text().strip():
            self.on_connect(None)
        else:
            self.on_scan(None)
        return False

    def on_primary_action(self, _button: Gtk.Button) -> None:
        if not self.host_entry.get_text().strip():
            self.on_scan(None)
            return
        self.on_connect(None)

    def on_pair(self, _button: Gtk.Button) -> None:
        async def pair_flow():
            host = self.host_entry.get_text()
            await self.remote.prepare(host)
            device = await self.remote.device_name()
            await self.remote.start_pairing()
            return device

        self.run_task(pair_flow(), self.after_pairing_started)

    def on_finish_pairing(self, _button: Gtk.Button | None) -> None:
        code = self.code_input.code()
        if not self.code_input.is_complete():
            self.set_status(f"Enter all {self.code_input.length} characters from the TV.")
            self.code_input.focus_first()
            return

        self.run_task(
            self.remote.finish_pairing(code),
            lambda _result: self.after_pairing_finished(),
        )

    def on_connect(self, _button: Gtk.Button | None) -> None:
        self.spinner.start()
        self.spinner.show()
        self.primary_button.set_sensitive(False)
        self.primary_button.set_label("Connecting ...")
        self.set_status("Connecting to your TV ...")

        async def connect_flow():
            await self.remote.prepare(self.host_entry.get_text())
            try:
                return await self.remote.connect()
            except InvalidAuth:
                device = await self.remote.device_name()
                await self.remote.start_pairing()
                return ("pairing", device)

        self.run_task(connect_flow(), self.after_connect_result)

    def on_device_selected(self, combo: Gtk.ComboBoxText) -> None:
        host = combo.get_active_id()
        if host:
            self.host_entry.set_text(host)

    def on_forget_device(self, _button: Gtk.Button) -> None:
        host = self.device_combo.get_active_id() or self.host_entry.get_text().strip()
        if not host:
            self.set_status("No saved TV is selected.")
            return

        if not self.confirm("Forget this TV?", "TellyKeys will remove this TV and its pairing key."):
            return

        if self.remote.host == host:
            self.remote.disconnect()
            self.connected = False

        self.settings = forget_device(self.settings, host)
        save_settings(self.settings)
        self.host_entry.set_text(self.settings.last_host)
        self._refresh_device_combo()
        self.remote_panel.hide()
        self.primary_button.show()
        self.primary_button.set_sensitive(True)
        self.primary_button.set_label("Find TV")
        self.set_status("The TV has been removed.")
        self.schedule_window_fit()

    def on_reset_all(self, _button: Gtk.Button) -> None:
        if not self.confirm("Reset TellyKeys?", "This removes all saved TVs and pairing keys."):
            return

        self.remote.disconnect()
        self.connected = False
        self.settings = reset_all()
        self.host_entry.set_text("")
        self._refresh_device_combo()
        self.code_input.clear()
        self.code_row.hide()
        self.remote_panel.hide()
        self.primary_button.show()
        self.primary_button.set_sensitive(True)
        self.primary_button.set_label("Find TV")
        self.set_status("Reset complete. Ready for setup.")
        self.schedule_window_fit()

    def on_custom_button_selected(self, combo: Gtk.ComboBoxText) -> None:
        label = combo.get_active_id()
        if not label:
            return
        for button_label, target in self.shortcuts():
            if button_label == label:
                self.custom_label_entry.set_text(button_label)
                self.custom_target_entry.set_text(target)
                return

    def _save_apps_buttons(self, shortcuts: list[tuple[str, str]]) -> None:
        self.set_shortcuts(shortcuts)
        save_settings(self.settings)
        self._refresh_custom_button_combo()
        self.rebuild_shortcuts()

    def _replace_app_button(self, label: str, target: str) -> None:
        shortcuts = [(button_label, button_target) for button_label, button_target in self.shortcuts() if button_label != label]
        shortcuts.append((label, target))
        self._save_apps_buttons(shortcuts)

        for button in self.settings.app_buttons:
            if button.label == label:
                self.custom_button_combo.set_active_id(button.label)
                return

    def on_add_custom_button(self, _button: Gtk.Button) -> None:
        label = self.custom_label_entry.get_text().strip()
        target = self.custom_target_entry.get_text().strip()
        if not label or not target:
            self.set_status("Enter both a name and an app ID or URL.")
            return

        self._replace_app_button(label, target)
        self.set_status(f"Saved {label}.")

    def on_bluetooth_keyboard_toggled(self, check: Gtk.CheckButton) -> None:
        enabled = check.get_active()
        self.settings = set_bluetooth_keyboard_enabled(self.settings, enabled)
        save_settings(self.settings)
        self.remote.set_bluetooth_keyboard_enabled(enabled)
        self.refresh_bluetooth_text_status()
        if enabled:
            self.set_status("Bluetooth keyboard text is on. Set it up once if the TV is not paired yet.")
        else:
            self.set_status("Bluetooth keyboard mode disabled.")

    def refresh_bluetooth_text_status(self) -> bool:
        def wrapped() -> None:
            try:
                enabled, helper_running, keyboard_connected = self.remote.bluetooth_text_status()
                GLib.idle_add(self.show_bluetooth_text_status, enabled, helper_running, keyboard_connected)
            except Exception:
                return

        self.runner.call(wrapped)
        return True

    def show_bluetooth_text_status(self, enabled: bool, helper_running: bool, keyboard_connected: bool) -> bool:
        if not hasattr(self, "text_method_label"):
            return False
        if keyboard_connected and enabled:
            text = "Text: Bluetooth keyboard connected"
        elif helper_running and enabled:
            text = "Text: Bluetooth keyboard ready to pair"
        elif enabled:
            text = "Text: Bluetooth keyboard setup needed"
        else:
            text = "Text: automatic"
        self.text_method_label.set_text(text)
        return False

    def refresh_microphone_combo(self) -> None:
        if not hasattr(self, "microphone_combo"):
            return
        self.updating_microphone_combo = True
        self.microphone_combo.remove_all()
        self.microphone_combo.append(DEFAULT_MICROPHONE_ID, "Default microphone")
        sources, message = self.remote.microphone_sources_with_status()
        for source in sources:
            self.microphone_combo.append(source.name, source.description)
        active_id = self.settings.microphone_source or DEFAULT_MICROPHONE_ID
        if not self.settings.microphone_source and len(sources) == 1:
            active_id = sources[0].name
            self.settings = set_microphone_source(self.settings, active_id)
            save_settings(self.settings)
            self.remote.set_microphone_source(active_id)
            message = f"Using {sources[0].description}."
        self.microphone_combo.set_active_id(active_id)
        if self.microphone_combo.get_active_id() is None:
            self.microphone_combo.set_active_id(DEFAULT_MICROPHONE_ID)
        self.updating_microphone_combo = False
        if hasattr(self, "microphone_status_label"):
            self.microphone_status_label.set_text(message)

    def on_refresh_microphones(self, _button: Gtk.Button) -> None:
        self.refresh_microphone_combo()
        self.set_status("Microphone list refreshed.")

    def on_microphone_selected(self, combo: Gtk.ComboBoxText) -> None:
        if self.updating_microphone_combo:
            return
        source = combo.get_active_id() or DEFAULT_MICROPHONE_ID
        if source == DEFAULT_MICROPHONE_ID:
            source = ""
        self.settings = set_microphone_source(self.settings, source)
        save_settings(self.settings)
        self.remote.set_microphone_source(source)
        self.set_status("Microphone saved.")

    def on_test_microphone(self, _button: Gtk.Button) -> None:
        self.set_status("Testing microphone ...")
        if hasattr(self, "microphone_status_label"):
            self.microphone_status_label.set_text("Testing microphone. Speak now ...")

        def wrapped() -> None:
            try:
                heard_audio, peak = self.remote.test_microphone()
                if heard_audio:
                    message = f"Microphone works. Level: {peak}."
                else:
                    message = f"Microphone is quiet. Level: {peak}."
                GLib.idle_add(self.set_status, message)
                GLib.idle_add(self.microphone_status_label.set_text, message)
            except Exception as exc:  # noqa: BLE001
                message = f"Microphone test failed: {exc}"
                GLib.idle_add(self.set_status, message)
                GLib.idle_add(self.microphone_status_label.set_text, message)

        self.runner.call(wrapped)

    def on_setup_bluetooth_keyboard(self, _button: Gtk.Button) -> None:
        self.set_status("Setting up Bluetooth keyboard mode ...")

        def wrapped() -> None:
            try:
                result = self.remote.prepare_bluetooth_keyboard()
                GLib.idle_add(self.after_setup_bluetooth_keyboard, result)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.set_status, str(exc))

        self.runner.call(wrapped)

    def after_setup_bluetooth_keyboard(self, result: BluetoothKeyboardSetup) -> bool:
        if result.ok:
            self.bluetooth_keyboard_check.set_active(True)
            if result.keyboard_connected:
                self.set_status("Bluetooth keyboard is connected and ready.")
            else:
                self.set_status("Bluetooth keyboard is ready. Pair TellyKeys Keyboard from the TV's Bluetooth menu.")
            self.show_bluetooth_text_status(True, result.helper_running, result.keyboard_connected)
        else:
            self.set_status(result.message)
        return False

    def on_reset_bluetooth_keyboard(self, _button: Gtk.Button) -> None:
        remove_system_fix = self.confirm(
            "Reset Bluetooth keyboard?",
            "This stops the keyboard helper and removes its local setup. If TellyKeys changed the system Bluetooth service, it will ask for permission to restore it.",
        )
        if not remove_system_fix:
            return

        self.set_status("Resetting Bluetooth keyboard setup ...")

        def wrapped() -> None:
            try:
                result = self.remote.reset_bluetooth_keyboard(remove_system_fix=True)
                GLib.idle_add(self.after_reset_bluetooth_keyboard, result)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.set_status, str(exc))

        self.runner.call(wrapped)

    def after_reset_bluetooth_keyboard(self, result: BluetoothKeyboardSetup) -> bool:
        if result.ok:
            self.settings = set_bluetooth_keyboard_enabled(self.settings, False)
            save_settings(self.settings)
            self.bluetooth_keyboard_check.set_active(False)
        self.show_bluetooth_text_status(False, result.helper_running, result.keyboard_connected)
        self.set_status(result.message)
        return False

    def on_remove_custom_button(self, _button: Gtk.Button) -> None:
        label = self.custom_button_combo.get_active_id() or self.custom_label_entry.get_text().strip()
        if not label:
            self.set_status("No app button is selected.")
            return
        self.remove_app_button(label)

    def on_restore_default_app_buttons(self, _button: Gtk.Button) -> None:
        self._save_apps_buttons(self.default_shortcuts())
        self.set_status("Default app buttons restored.")

    def on_add_current_app_button(self, _button: Gtk.Button) -> None:
        app_id = self.current_status.current_app if self.current_status else None
        if not app_id:
            self.set_status("Open the app on your TV first, then try again.")
            return

        label = self.friendly_app_name(app_id)
        self._replace_app_button(label, app_id)
        self.custom_label_entry.set_text(label)
        self.custom_target_entry.set_text(app_id)
        self.set_status(f"Added {label}.")

    def on_text_input_help(self, _button: Gtk.Button) -> None:
        def wrapped() -> None:
            try:
                diagnostics = self.remote.text_diagnostics()
                GLib.idle_add(self.show_text_input_help, diagnostics)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.set_status, str(exc))

        self.runner.call(wrapped)

    def on_open_tv_keyboard_settings(self, _button: Gtk.Button) -> None:
        def wrapped() -> None:
            try:
                opened = self.remote.open_tv_keyboard_settings()
                if opened:
                    GLib.idle_add(self.set_status, "Opened keyboard settings on the TV.")
                else:
                    GLib.idle_add(
                        self.set_status,
                        "ADB is not installed or authorized. Open TV keyboard settings manually.",
                    )
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.set_status, str(exc))

        self.runner.call(wrapped)

    def show_text_input_help(self, diagnostics: TextInputDiagnostics) -> bool:
        ime_seen = diagnostics.ime_counter not in (None, 0) or diagnostics.ime_field_counter not in (None, 0)
        adb_status = "not installed"
        if diagnostics.adb_installed:
            adb_status = "authorized" if diagnostics.adb_authorized else "installed, not authorized"

        lines = [
            f"ADB: {adb_status}",
            f"ADB target: {diagnostics.adb_target or 'unknown'}",
            f"Bluetooth keyboard: {'on' if diagnostics.bluetooth_enabled else 'off'}",
            f"Bluetooth helper: {'running' if diagnostics.bluetooth_helper_running else 'not running'}",
            f"Bluetooth paired: {'yes' if diagnostics.bluetooth_keyboard_connected else 'no'}",
            f"Bluetooth system fix: {'installed' if diagnostics.bluetooth_system_fix_installed else 'not installed'}",
            f"Remote IME counters: {diagnostics.ime_counter}/{diagnostics.ime_field_counter}",
            f"Current app: {diagnostics.current_app or 'unknown'}",
            "",
        ]
        if diagnostics.adb_authorized:
            lines.append("Best path: ADB text input is available. Send should use it automatically.")
        elif not diagnostics.adb_installed:
            lines.append("Most reliable fix: install adb, enable network debugging on the TV, and approve this computer.")
        elif diagnostics.adb_installed:
            lines.append("ADB is installed but the TV has not approved this computer yet.")

        if not ime_seen:
            lines.extend(
                [
                    "",
                    "Remote IME has not reported an active text field yet. On Sony Bravia, try:",
                    "1. TV Settings > Apps > Show system apps > Android TV Remote Service > Storage > Clear data.",
                    "2. TV Settings > Keyboard and make Gboard/Leanback Keyboard the active keyboard, not Virtual Remote Keyboard.",
                    "3. Re-pair TellyKeys after clearing Android TV Remote Service.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Remote IME is active. If YouTube still ignores text, that app is likely rejecting Remote IME input on this TV.",
                ]
            )

        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Text input diagnostics",
        )
        dialog.format_secondary_text("\n".join(lines))
        dialog.run()
        dialog.destroy()
        return False

    def on_scan(self, _button: Gtk.Button | None) -> None:
        self.spinner.start()
        self.spinner.show()
        self.scan_button.set_sensitive(False)
        self.primary_button.set_sensitive(False)
        self.primary_button.set_label("Scanning ...")
        self.set_status("Looking for Google TV on your network ...")

        async def scan_flow():
            return await asyncio.to_thread(discover_android_tvs)

        self.run_task(scan_flow(), self.after_scan)

    def after_scan(self, devices: list) -> None:
        self.scan_button.set_sensitive(True)
        if not devices:
            self.spinner.stop()
            self.spinner.hide()
            self.primary_button.set_sensitive(True)
            self.primary_button.set_label("Try again")
            self.set_status("Could not find your TV automatically. Open Options and enter the IP address if needed.")
            return

        first = devices[0]
        self.host_entry.set_text(first.host)
        for device in reversed(devices):
            self.settings = remember_device(self.settings, device.host, device.name)
        save_settings(self.settings)
        self._refresh_device_combo()
        self.device_combo.set_active_id(first.host)
        self.set_status(f"Found {first.name}. Connecting ...")
        self.on_connect(None)

    def on_send_text(self, _button: Gtk.Button) -> None:
        text = self.text_entry.get_text()
        if not text:
            return
        self.set_status("Sending text ...")

        def wrapped() -> None:
            try:
                method = self.remote.text(text)
                GLib.idle_add(self.after_send_text, method)
            except BluetoothKeyboardNotConnected as exc:
                GLib.idle_add(self.set_status, str(exc))
                GLib.idle_add(self.refresh_bluetooth_text_status)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.set_status, str(exc))

        self.runner.call(wrapped)

    def after_send_text(self, method: str | None) -> bool:
        messages = {
            "bluetooth_keyboard": "Text sent with Bluetooth keyboard mode.",
            "youtube_search": "Opened YouTube search.",
            "adb": "Text sent with ADB.",
            "remote_ime": "Text sent. If nothing appears, the TV app is not accepting remote text input.",
        }
        labels = {
            "bluetooth_keyboard": "Text: used Bluetooth keyboard",
            "youtube_search": "Text: used YouTube search link",
            "adb": "Text: used ADB",
            "remote_ime": "Text: used Google TV text input",
        }
        if hasattr(self, "text_method_label") and method in labels:
            self.text_method_label.set_text(labels[method])
        self.set_status(messages.get(method, "No text was sent."))
        return False

    def on_launch_custom(self, _button: Gtk.Button) -> None:
        target = self.launch_entry.get_text().strip()
        if target:
            self.launch(target)

    def on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if isinstance(self.get_focus(), Gtk.Entry):
            return False

        shortcuts = {
            Gdk.KEY_Up: "DPAD_UP",
            Gdk.KEY_Down: "DPAD_DOWN",
            Gdk.KEY_Left: "DPAD_LEFT",
            Gdk.KEY_Right: "DPAD_RIGHT",
            Gdk.KEY_Return: "DPAD_CENTER",
            Gdk.KEY_KP_Enter: "DPAD_CENTER",
            Gdk.KEY_space: "MEDIA_PLAY_PAUSE",
            Gdk.KEY_BackSpace: "BACK",
            Gdk.KEY_Escape: "BACK",
            Gdk.KEY_Home: "HOME",
            Gdk.KEY_plus: "VOLUME_UP",
            Gdk.KEY_KP_Add: "VOLUME_UP",
            Gdk.KEY_minus: "VOLUME_DOWN",
            Gdk.KEY_KP_Subtract: "VOLUME_DOWN",
            Gdk.KEY_m: "MUTE",
            Gdk.KEY_M: "MUTE",
            Gdk.KEY_p: "POWER",
            Gdk.KEY_P: "POWER",
        }
        key_code = shortcuts.get(event.keyval)
        if key_code:
            self.send_key(key_code)
            return True
        return False

    def send_key(self, key_code: str) -> None:
        self.run_remote_call(self.remote.key, key_code)

    def on_voice_search(self, _button: Gtk.Button) -> None:
        label = self.voice_button.get_label()
        if label == "Stop listening":
            self.runner.call(self.remote.stop_voice_search)
            return
        self.voice_button.set_label("Stop listening")
        self.set_status("Opening voice search ...")

        def wrapped() -> None:
            try:
                self.remote.start_voice_search()
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.show_voice_status, f"Voice search failed: {exc}", False)

        self.runner.call(wrapped)

    def show_voice_status(self, message: str, active: bool) -> bool:
        if hasattr(self, "voice_button"):
            self.voice_button.set_label("Stop listening" if active else "Voice search")
        self.set_status(message)
        return False

    def open_tv_settings(self) -> None:
        def wrapped() -> None:
            method = self.remote.open_tv_settings()
            if method == "adb":
                GLib.idle_add(self.set_status, "Opened TV Settings.")
            else:
                GLib.idle_add(self.set_status, "Sent Settings key to the TV.")

        self.runner.call(wrapped)

    def launch(self, target: str) -> None:
        self.run_remote_call(self.remote.launch, target)

    def setup_tray(self) -> None:
        self.tray_icon = Gtk.StatusIcon.new_from_icon_name("tellykeys")
        self.tray_icon.set_title("TellyKeys")
        self.tray_icon.set_tooltip_text("TellyKeys")
        self.tray_icon.set_visible(True)
        self.tray_icon.connect("activate", self.on_tray_activate)
        self.tray_icon.connect("popup-menu", self.on_tray_popup)

    def on_tray_activate(self, _icon: Gtk.StatusIcon) -> None:
        self.toggle_window()

    def on_tray_popup(self, _icon: Gtk.StatusIcon, button: int, activate_time: int) -> None:
        menu = Gtk.Menu()
        items = [
            ("Show TellyKeys", lambda *_: self.show_window()),
            ("Hide window", lambda *_: self.hide()),
            ("Power", lambda *_: self.send_key("POWER")),
            ("Vol +", lambda *_: self.send_key("VOLUME_UP")),
            ("Vol -", lambda *_: self.send_key("VOLUME_DOWN")),
            ("Mute", lambda *_: self.send_key("MUTE")),
            ("Quit", lambda *_: self.quit_app()),
        ]
        for label, callback in items:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)
        menu.show_all()
        menu.popup(None, None, None, None, button, activate_time)

    def toggle_window(self) -> None:
        if self.get_visible():
            self.hide()
        else:
            self.show_window()

    def show_window(self) -> None:
        self.show_all()
        if self.connected:
            self.remote_panel.set_no_show_all(False)
            self.remote_panel.show_all()
        else:
            self.remote_panel.hide()
        self.schedule_window_fit()
        self.present()

    def after_pairing_finished(self) -> None:
        self.remember_current_host()
        self.code_input.clear()
        self.code_row.hide()
        self.primary_button.show()
        self.spinner.start()
        self.spinner.show()
        self.set_status("Pairing complete. Connecting ...")
        self.schedule_window_fit()
        self.on_connect(None)

    def after_pairing_started(self, device: str) -> None:
        self.remember_current_host(device)
        self.spinner.stop()
        self.spinner.hide()
        self.primary_button.hide()
        self.code_input.show()
        self.finish_button.show()
        self.code_row.show()
        self.code_input.clear()
        self.code_input.focus_first()
        self.set_status(f"Enter the code shown on {device}.")
        self.schedule_window_fit()

    def after_connect_result(self, result: object) -> None:
        if isinstance(result, tuple) and result[0] == "pairing":
            self.after_pairing_started(str(result[1]))
            return
        self.after_connected(result)  # type: ignore[arg-type]

    def after_connected(self, status: TvStatus) -> None:
        self.remember_current_host(status.name)
        self.spinner.stop()
        self.spinner.hide()
        self.connected = True
        self.primary_button.hide()
        self.remote_panel.set_no_show_all(False)
        self.remote_panel.show_all()
        self.show_tv_status(status)
        self.schedule_window_fit()

    def remember_current_host(self, label: str | None = None) -> None:
        self.settings = remember_device(self.settings, self.host_entry.get_text(), label)
        save_settings(self.settings)
        self._refresh_device_combo()

    def show_tv_status(self, status: TvStatus) -> None:
        self.current_status = status
        if self.connected:
            self.primary_button.hide()
            self.remote_panel.set_no_show_all(False)
            self.remote_panel.show_all()

        lines = ["Ready"]
        if status.name:
            lines.append(status.name)
        if status.is_on is not None:
            lines.append("TV is on" if status.is_on else "TV is off")
        if status.current_app:
            lines.append(f"App: {self.friendly_app_name(status.current_app)}")
        if status.volume:
            lines.append(f"Volume: {status.volume}")
        self.set_status("\n".join(lines))
        self.schedule_window_fit()

    def show_demo_state(self) -> bool:
        self.connected = True
        self.primary_button.hide()
        self.remote_panel.set_no_show_all(False)
        self.remote_panel.show_all()
        self.set_status("Ready\nLiving Room Google TV\nTV is on\nApp: YouTube")
        self.schedule_window_fit()
        return False

    def friendly_app_name(self, app_id: str) -> str:
        app_id = app_id.strip()
        names = {
            "com.google.android.youtube.tv": "YouTube",
            "com.netflix.ninja": "Netflix",
            "com.amazon.amazonvideo.livingroom": "Prime Video",
            "com.disney.disneyplus": "Disney+",
            "org.xbmc.kodi": "Kodi",
            "com.google.android.apps.tv.dreamx": "Google TV",
            "com.google.android.apps.tv.launcherx": "Google TV",
            "com.google.android.tvlauncher": "Google TV",
        }
        if app_id.startswith("com.google.android.apps.tv.dreamx"):
            return "Google TV"
        return names.get(app_id, app_id)

    def set_status(self, text: str) -> None:
        self.status_label.set_text(text)

    def confirm(self, title: str, body: str) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=title,
        )
        dialog.format_secondary_text(body)
        dialog.add_button("Continue", Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def _refresh_device_combo(self) -> None:
        self.device_combo.remove_all()
        for device in self.settings.devices:
            self.device_combo.append(device.host, device.display_name)
        if self.settings.last_host:
            self.device_combo.set_active_id(self.settings.last_host)

    def _refresh_custom_button_combo(self) -> None:
        self.custom_button_combo.remove_all()
        shortcuts = self.shortcuts()
        for label, _target in shortcuts:
            self.custom_button_combo.append(label, label)
        if shortcuts:
            self.custom_button_combo.set_active_id(shortcuts[0][0])

    def rebuild_shortcuts(self, keep_open: bool = False) -> None:
        if not hasattr(self, "apps_popover"):
            return
        child = self.apps_popover.get_child()
        if child:
            self.apps_popover.remove(child)
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        popover_box.get_style_context().add_class("settings-panel")

        header = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Apps", xalign=0)
        title.get_style_context().add_class("section-label")
        header.pack_start(title, True, True, 0)
        manage_button = Gtk.Button(label="Done" if self.apps_manage_mode else "Edit")
        manage_button.get_style_context().add_class("ghost-button")
        manage_button.connect("clicked", self.on_toggle_apps_manage)
        header.pack_start(manage_button, False, False, 0)
        popover_box.pack_start(header, False, False, 0)

        if self.apps_manage_mode:
            popover_box.pack_start(self._build_apps_manage_list(), False, False, 0)
            restore_button = Gtk.Button(label="Restore defaults")
            restore_button.connect("clicked", self.on_restore_default_app_buttons)
            popover_box.pack_start(restore_button, False, False, 0)
        else:
            self.shortcuts_grid = self._build_shortcuts_grid()
            popover_box.pack_start(self.shortcuts_grid, False, False, 0)
        self.apps_popover.add(popover_box)
        popover_box.show_all()
        if keep_open:
            self.apps_popover.popup()
        else:
            self.apps_popover.popdown()

    def run_remote_call(self, func: Callable, *args, on_success: Callable[[], None] | None = None) -> None:
        def wrapped() -> None:
            try:
                func(*args)
                if on_success:
                    GLib.idle_add(on_success)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.set_status, str(exc))

        self.runner.call(wrapped)

    def run_task(self, coro, on_success: Callable[[object], None]) -> None:
        def done(task: Future) -> None:
            def update_ui() -> bool:
                try:
                    result = task.result()
                    on_success(result)
                except (CannotConnect, ConnectionClosed, InvalidAuth, RuntimeError, ValueError) as exc:
                    self.spinner.stop()
                    self.spinner.hide()
                    self.scan_button.set_sensitive(True)
                    self.primary_button.show()
                    self.primary_button.set_sensitive(True)
                    self.primary_button.set_label("Try again")
                    self.set_status(str(exc))
                except Exception as exc:  # noqa: BLE001
                    self.spinner.stop()
                    self.spinner.hide()
                    self.scan_button.set_sensitive(True)
                    self.primary_button.show()
                    self.primary_button.set_sensitive(True)
                    self.primary_button.set_label("Try again")
                    self.set_status(f"Unexpected error: {exc}")
                return False

            GLib.idle_add(update_ui)

        self.runner.run(coro, done)

    def on_delete_event(self, *_args) -> bool:
        if self.use_tray:
            self.hide()
            return True
        return False

    def quit_app(self) -> None:
        self.remote.disconnect()
        if self.tray_icon:
            self.tray_icon.set_visible(False)
        self.runner.loop.call_soon_threadsafe(self.runner.loop.stop)
        Gtk.main_quit()

    def on_destroy(self, *_args) -> None:
        if not self.use_tray:
            self.quit_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="TellyKeys Google TV remote")
    parser.add_argument("--tray", action="store_true", help="show a tray/status icon")
    parser.add_argument("--start-hidden", action="store_true", help="start hidden; implies --tray")
    args = parser.parse_args()

    if not Gtk.init_check()[0]:
        raise SystemExit("GTK could not start. Run TellyKeys from an active desktop session with DISPLAY set.")
    TellyKeysWindow(use_tray=args.tray or args.start_hidden, start_hidden=args.start_hidden)
    Gtk.main()


if __name__ == "__main__":
    main()
