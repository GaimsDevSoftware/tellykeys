import unittest

from tellykeys.discovery import _parse_resolved_line


class DiscoveryTests(unittest.TestCase):
    def test_parse_android_tv_avahi_line(self) -> None:
        line = '=;enp4s0;IPv4;Google\\032TV;_androidtvremote2._tcp;local;Android.local;10.0.0.2;6466;"bt=90"'

        device = _parse_resolved_line(line)

        self.assertIsNotNone(device)
        assert device is not None
        self.assertEqual(device.name, "Google TV")
        self.assertEqual(device.host, "10.0.0.2")
        self.assertEqual(device.port, 6466)
        self.assertEqual(device.interface, "enp4s0")

    def test_ignores_non_ipv4_lines(self) -> None:
        line = '=;enp4s0;IPv6;Google\\032TV;_androidtvremote2._tcp;local;Android.local;fe80::1;6466;"bt=90"'

        self.assertIsNone(_parse_resolved_line(line))


if __name__ == "__main__":
    unittest.main()
