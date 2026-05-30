# TellyKeys

TellyKeys is a small Linux desktop remote for Google TV and Android TV.

It uses Android TV Remote Protocol v2 through `androidtvremote2`, the same family of network remote protocol used by the official Google TV mobile app. It does not require ADB or developer mode on the TV.

## Install and run

```bash
cd /home/robert/Dokumenter/Kode-prosjekter/tellykeys
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e .
tellykeys
```

System dependency on Linux Mint:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 avahi-utils
```

`python3-gi`, GTK 3 and `avahi-browse` are already present on Robert's machine as of 2026-05-30.

## Usage

1. Open TellyKeys.
2. Wait while it finds the TV and connects.
3. If the TV shows a pairing code, type it into TellyKeys and click `Done`.
4. Use the big remote buttons.

The app remembers the TV after pairing. Next launch should go straight to connecting.

Use `Options` only if auto-discovery fails or you want to enter an app-id / URL manually.
Options opens as a settings page inside the same window, not as a separate popup.

Under `Options` you can also:

- delete the selected TV, including its pairing key
- reset TellyKeys back to first-run state

Connected devices are remembered in:

```text
~/.config/tellykeys/settings.json
```

Pairing certificates are stored under:

```text
~/.config/tellykeys/devices/
```

## Keyboard shortcuts

When the main window has focus and you are not typing in a text field:

- Arrow keys: D-pad
- Enter: OK
- Backspace or Escape: Back
- Home: Home
- Space: Play/Pause
- `+` / `-`: Volume
- `m`: Mute
- `p`: Power

## Text input

When YouTube is the active TV app, TellyKeys opens a YouTube search link directly instead of trying to type through the on-screen keyboard. This avoids a known Sony/YouTube remote-keyboard failure mode.

For other apps, TellyKeys first tries Android Debug Bridge text input if `adb` is installed and the TV is authorized on port `5555`. If that is not available, it falls back to Google TV Remote Protocol IME text input.

ADB text input is optional, but it is more reliable on some Google TV on-screen keyboards and apps:

```bash
sudo apt install adb
adb connect TV_IP_ADDRESS:5555
```

The TV must have developer/network debugging enabled and must accept the authorization prompt.

If text does not appear on the TV, open `Options` and click `Text input help`.
The dialog shows whether ADB is installed/authorized and whether the TV has sent
Remote IME text-field counters to TellyKeys.

Known Sony Bravia checks:

- Set the TV keyboard to Gboard or Leanback Keyboard, not Virtual Remote Keyboard.
- Clear `Android TV Remote Service` storage on the TV, then re-pair TellyKeys.
- Some Sony Bravia models/apps, including YouTube on some models, reject remote software-keyboard text even when the field is active.

### Bluetooth keyboard mode

TellyKeys can also try to act as a Bluetooth keyboard. Open `Options`, click
`Set up Bluetooth keyboard`, then pair `TellyKeys Keyboard` from the TV's
Bluetooth accessory menu. After pairing, enable `Use Bluetooth keyboard for text`.
The text box shows whether the keyboard is ready to pair, connected, or whether
setup is still needed.

This creates and starts a user-level systemd service:

```text
~/.config/systemd/user/tellykeys-bluetooth-keyboard.service
```

No sudo is used by TellyKeys for this setup. Some Bluetooth stacks may still
require deeper system configuration, so this mode is intentionally marked
experimental.

Use `Reset Bluetooth keyboard` to stop the helper and remove the local setup.
If TellyKeys had to apply the BlueZ system fix, reset will ask for permission
to restore the normal Bluetooth service.

Norwegian `æ`, `ø`, and `å` are sent using Norwegian keyboard positions. If the
TV is set to a non-Norwegian physical keyboard layout, those keys may appear as
`'`, `;`, and `[`.

## Voice search

TellyKeys has an experimental `Voice search` button. It opens a Google TV voice
session through Android TV Remote Protocol v2 and streams audio from the default
Linux microphone using `parec` as 16-bit PCM, mono, 8000 Hz.

On Linux Mint this normally works through PipeWire/PulseAudio compatibility. If
voice search cannot start, check that `parec` exists and that the default system
microphone works in the sound settings.

If the TV opens voice search but does not hear you, open `Options > Text` and
choose the microphone explicitly. TellyKeys filters out monitor sources because
those record speaker output rather than your microphone.

## Next engineering plan

1. Test voice search on Sony Bravia, including Google TV home search and YouTube.
2. Test Bluetooth text in Netflix, Prime Video, Disney+, and Google TV search.
3. Move reliable setup flows into calmer first-run screens.
4. Add a package build for Linux Mint Cinnamon with app launcher, tray launcher, icon, helper scripts, and Cinnamon applet.
5. Add a safer packaging-time prompt for the BlueZ system fix, because it changes host Bluetooth behavior.
6. Improve the connected-state visual design before packaging.

## Desktop launcher

Install the launcher without sudo:

```bash
cd /home/robert/Dokumenter/Kode-prosjekter/tellykeys
scripts/install-desktop-launcher
```

This installs both:

- `TellyKeys`
- `TellyKeys Tray`

## Tray mode

Run TellyKeys with a tray/status icon:

```bash
tellykeys --tray
```

Start it hidden in the tray:

```bash
tellykeys --start-hidden
```

The tray menu can show/hide the app, send power/volume/mute, and quit TellyKeys.

## Cinnamon applet

Install the optional Cinnamon panel applet:

```bash
cd /home/robert/Dokumenter/Kode-prosjekter/tellykeys
scripts/install-cinnamon-applet
```

Then add it from Cinnamon's applet settings. The applet can open TellyKeys, start tray mode, or quit TellyKeys.

## Similar Linux tools found

- `gtv-remote`: Python CLI for Google TV / Android TV Remote Control Protocol v2.
- `androidtv-remote-cli`: Node.js terminal remote with pairing and D-pad mode.
- `atvremote`: Go CLI/library supporting Android TV / Google TV remote protocol v2.
- ADB wrappers such as `android-tv-remote`: work differently and require ADB/developer mode.

I did not find a mature native Linux GTK-style desktop GUI that does exactly this.
