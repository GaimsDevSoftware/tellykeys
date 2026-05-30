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
)
from tellykeys.remote import _adb_input_text, _youtube_search_url


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

    def test_adb_input_text_escapes_spaces(self) -> None:
        self.assertEqual(_adb_input_text("hei der"), "hei%sder")

    def test_youtube_search_url_encodes_query(self) -> None:
        self.assertEqual(
            _youtube_search_url("hei der"),
            "https://www.youtube.com/results?search_query=hei+der",
        )


if __name__ == "__main__":
    unittest.main()
