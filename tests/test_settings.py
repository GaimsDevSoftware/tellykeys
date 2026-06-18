import unittest

from tellykeys.settings import (
    Settings,
    ShortcutButton,
    add_button,
    forget_device,
    remember_device,
    remove_button,
    set_app_buttons,
    set_microphone_source,
    set_shows,
)
from androidtvremote2.remotemessage_pb2 import RemoteMessage

from tellykeys.remote import TellyKeysRemote, TextFieldState, _adb_input_text, _youtube_search_url


class SettingsTests(unittest.TestCase):
    def test_remember_device_moves_existing_host_to_front(self) -> None:
        settings = Settings()

        settings = remember_device(settings, "10.0.0.2", "Google TV")
        settings = remember_device(settings, "10.0.0.3", "Bedroom")
        settings = remember_device(settings, "10.0.0.2", "Living Room")

        self.assertEqual(settings.last_host, "10.0.0.2")
        self.assertEqual(
            [(device.host, device.label) for device in settings.devices],
            [
                ("10.0.0.2", "Living Room"),
                ("10.0.0.3", "Bedroom"),
            ],
        )

    def test_forget_device_removes_host_and_picks_next_last_host(self) -> None:
        settings = Settings()
        settings = remember_device(settings, "10.0.0.2", "Google TV")
        settings = remember_device(settings, "10.0.0.3", "Bedroom")

        settings = forget_device(settings, "10.0.0.3")

        self.assertEqual(settings.last_host, "10.0.0.2")
        self.assertEqual([(device.host, device.label) for device in settings.devices], [("10.0.0.2", "Google TV")])

    def test_add_button_replaces_same_label(self) -> None:
        settings = Settings()

        settings = add_button(settings, "Plex", "com.plexapp.android")
        settings = add_button(settings, "Plex", "plex://")

        self.assertEqual([(button.label, button.target) for button in settings.buttons], [("Plex", "plex://")])

    def test_remove_button(self) -> None:
        settings = add_button(Settings(), "Plex", "plex://")

        settings = remove_button(settings, "Plex")

        self.assertEqual(settings.buttons, [])

    def test_set_app_buttons_marks_app_buttons_configured(self) -> None:
        settings = set_app_buttons(Settings(), [ShortcutButton("YouTube", "https://www.youtube.com")])

        self.assertTrue(settings.app_buttons_configured)
        self.assertEqual([(button.label, button.target) for button in settings.app_buttons], [("YouTube", "https://www.youtube.com")])

    def test_set_microphone_source(self) -> None:
        settings = set_microphone_source(Settings(), " alsa_input.test ")

        self.assertEqual(settings.microphone_source, "alsa_input.test")

    def test_set_shows_replaces_show_list(self) -> None:
        settings = set_shows(Settings(), [ShortcutButton("Gabby", "https://www.netflix.com/title/81009946")])

        self.assertEqual(
            [(button.label, button.target) for button in settings.shows],
            [("Gabby", "https://www.netflix.com/title/81009946")],
        )

    def test_shows_survive_other_mutations(self) -> None:
        settings = set_shows(Settings(), [ShortcutButton("Gabby", "https://www.netflix.com/title/81009946")])
        settings = remember_device(settings, "10.0.0.2", "Living Room")
        settings = set_microphone_source(settings, "alsa_input.test")
        settings = set_app_buttons(settings, [ShortcutButton("YouTube", "https://www.youtube.com")])

        self.assertEqual(
            [(button.label, button.target) for button in settings.shows],
            [("Gabby", "https://www.netflix.com/title/81009946")],
        )

    def test_send_text_with_adb_throttles_per_character(self) -> None:
        from unittest import mock
        from tellykeys import remote as remote_module

        calls: list[list[str]] = []

        class FakeCompleted:
            returncode = 0

        def fake_run(args, **kwargs):
            calls.append(args)
            return FakeCompleted()

        remote = TellyKeysRemote()
        remote.host = "10.0.0.2"
        remote._is_adb_authorized = lambda: True

        with mock.patch.object(remote_module.shutil, "which", return_value="/usr/bin/adb"), \
             mock.patch.object(remote_module.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(remote_module.time, "sleep", return_value=None) as sleep_mock:
            ok = remote._send_text_with_adb("hei")

        self.assertTrue(ok)
        input_calls = [c for c in calls if "input" in c and "text" in c]
        # one `input text` invocation per character (no single fast burst)
        self.assertEqual(len(input_calls), 3)
        self.assertEqual("".join(c[-1] for c in input_calls), "hei")
        # a pause between characters, but not a trailing one after the last
        self.assertEqual(sleep_mock.call_count, 2)

    def test_global_search_launches_query_and_opens_result(self) -> None:
        from unittest import mock
        from tellykeys import remote as remote_module

        calls: list[list[str]] = []

        class FakeCompleted:
            returncode = 0

        def fake_run(args, **kwargs):
            calls.append(args)
            return FakeCompleted()

        remote = TellyKeysRemote()
        remote.host = "10.0.0.2"
        remote._is_adb_authorized = lambda: True

        with mock.patch.object(remote_module.shutil, "which", return_value="/usr/bin/adb"), \
             mock.patch.object(remote_module.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(remote_module.time, "sleep", return_value=None):
            ok = remote.global_search("gabbys dukkehus")

        self.assertTrue(ok)
        joined = [" ".join(c) for c in calls]
        # launches Google TV universal search with the quoted multi-word query
        self.assertTrue(any("GLOBAL_SEARCH" in j and "'gabbys dukkehus'" in j for j in joined))
        # then presses OK to open the focused play button
        self.assertTrue(any("keyevent" in c and "DPAD_CENTER" in c for c in calls))

    def test_adb_input_text_escapes_spaces(self) -> None:
        self.assertEqual(_adb_input_text("hei der"), "hei%sder")

    def test_youtube_search_url_encodes_query(self) -> None:
        self.assertEqual(
            _youtube_search_url("hei der"),
            "https://www.youtube.com/results?search_query=hei+der",
        )

    def test_ime_show_request_emits_text_field_state(self) -> None:
        remote = TellyKeysRemote()
        states: list[TextFieldState] = []
        remote.set_text_field_callback(states.append)

        message = RemoteMessage()
        status = message.remote_ime_show_request.remote_text_field_status
        status.value = "abc"
        status.start = 3
        status.end = 3
        status.label = "Search"

        remote._observe_text_field_message(message)

        self.assertEqual(states, [TextFieldState(value="abc", start=3, end=3, label="Search", current_app=None)])

    def test_active_text_field_prefers_remote_ime_before_contextual_search(self) -> None:
        class FakeProtocolRemote:
            current_app = "com.google.android.youtube.tv"

            def __init__(self) -> None:
                self.sent_text: list[str] = []

            def send_text(self, text: str) -> None:
                self.sent_text.append(text)

        fake_remote = FakeProtocolRemote()
        remote = TellyKeysRemote()
        remote._remote = fake_remote
        remote._last_text_field = TextFieldState(value="", start=0, end=0, label=None, current_app=None)
        remote._last_text_field_seen_at = __import__("time").monotonic()
        remote._send_text_with_adb = lambda text: False

        result = remote.text("hei")

        self.assertEqual(result.method, "remote_ime")
        self.assertEqual(result.attempts, ("adb", "remote_ime"))
        self.assertEqual(fake_remote.sent_text, ["hei"])

    def test_clear_text_deletes_detected_text_length(self) -> None:
        deleted: list[str] = []
        remote = TellyKeysRemote()
        remote._last_text_field = TextFieldState(value="abc", start=3, end=3, label=None, current_app=None)
        remote._last_text_field_seen_at = __import__("time").monotonic()
        remote._send_key_with_adb = lambda key_code, count=1: False
        remote.key = deleted.append

        method = remote.clear_text()

        self.assertEqual(method, "remote_key")
        self.assertEqual(deleted, ["DEL", "DEL", "DEL"])


if __name__ == "__main__":
    unittest.main()
