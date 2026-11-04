# m916-linux

[![tests](https://github.com/furibeh/m916-linux/actions/workflows/tests.yml/badge.svg)](https://github.com/furibeh/m916-linux/actions/workflows/tests.yml)

**English** · [Español](README.es.md)

A tool to configure the **Redragon M916-PRO-1K** mouse on Linux, without the official Windows
program. It talks to the mouse over `hidraw` with the same protocol as the original
configurator, reconstructed by reverse engineering (see [docs/PROTOCOL.md](docs/PROTOCOL.md)).

Tested with the `3554:F5D5` wireless receiver and over the USB cable (`3554:F55E`). While the
cable is plugged in the mouse only answers through it, so the tool uses the cable and goes back
to the receiver when it is pulled out; `-d /dev/hidrawN` forces a device.

## Requirements

- Python 3. The command line has no external dependencies; the graphical interface uses
  Qt 6 (PySide6): `sudo dnf install python3-pyside6` on Fedora.
- Access to the mouse `hidraw` node. Install the udev rule once:

```sh
sudo cp udev/70-redragon-m916.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Installation

On Fedora, from COPR. The package includes the udev rule, the menu entry and the icon:

```sh
sudo dnf copr enable furibeh/m916-linux
sudo dnf install m916-linux
```

From the repository, without installing anything:

```sh
python3 -m m916.gui                # graphical interface
python3 -m m916 info               # command line
```

Or installed for your user, with the `m916` and `m916-gui` commands and a menu entry:

```sh
pip install --user .
install -Dm644 data/io.github.furibeh.m916-linux.desktop \
    ~/.local/share/applications/io.github.furibeh.m916-linux.desktop
install -Dm644 m916/icons/io.github.furibeh.m916-linux.svg \
    ~/.local/share/icons/hicolor/scalable/apps/io.github.furibeh.m916-linux.svg
```

## Graphical interface

Tabs for DPI (levels, values and colors), buttons, performance (polling rate, debounce, LOD,
sensor mode and options), lights (effect, brightness and speed of the DPI LED, turning off
when idle) and backups and profiles. Each button can be a click, a DPI or scroll function,
rapid fire, DPI lock, a keyboard shortcut (typed or captured by pressing it), a media key or a
macro, with an editor that records keyboard and mouse presses with their timing. Changes are
edited locally and **Apply** writes only the modified fields, verifying each one. It shows the
battery and follows changes made on the mouse itself, such as the DPI level, as they happen.
The **Onboard slot** selector in the header switches between the mouse's four slots (see
below). JSON profiles are saved and loaded from the **Backups and profiles** tab: when you
load one, its values appear as unapplied changes.

To try it without the mouse: `python3 -m m916.gui --fake tests/fixtures/m916_flash.bin`.

## Language

The graphical interface and the command line follow the system language: there is a Spanish
translation, and every other language shows English. To choose a language, set `LANGUAGE`:

```sh
LANGUAGE=en m916-gui        # English
LANGUAGE=es m916 --help     # Spanish
```

The fixed words of the CLI help ("usage:", "options") come from Python and may stay in
English. Action, option and key names (`dpi-loop`, `debounce`, `ctrl`…) are not translated:
they are the values typed in commands and profiles.

## Command line

```sh
m916 info                     # status, battery, active slot, DPI, options and buttons
m916 dump backup.bin          # do this before changing anything
m916 slot                     # active onboard slot (1-4); "m916 slot 2" selects slot 2
m916 dpi 3 3200               # DPI of a level (multiples of 50, up to 26000; --y for Y)
m916 dpi-color 3 00ff00       # LED color of a level
m916 dpi-levels 5             # enabled DPI levels
m916 dpi-active 2             # level in use
m916 rate 1000                # 125 / 250 / 500 / 1000 Hz
m916 button 7 middle          # left|right|middle|back|forward|dpi-loop|dpi-up|dpi-down|
                              # scroll-up|scroll-down|scroll-left|scroll-right|report-rate|disabled
m916 button 7 key:ctrl+c      # shortcut of up to 3 keys, or a media key: key:volume-up
m916 button 7 fire:20:3       # rapid fire: 3 clicks of 20 ms down + 20 ms up (0 = while held)
m916 button 7 dpi-lock:400    # fixed DPI while held down
m916 macro 8 copy "+ctrl 10 +c 10 -c 10 -ctrl" --repeat 1
                              # +KEY presses, -KEY releases, a number = delay in ms;
                              # --repeat N (1-252), while-held, toggle or until-any
m916 keys                     # available key names
m916 option debounce 4        # single-byte options: info shows their meaning and
                              # option --help their values (lod, sensor-mode, dpi-led…)
m916 restore backup.bin       # rewrites only what changed and verifies
m916 dump --slot 2 s2.bin     # --slot N: works on slot N and goes back to the active one
m916 profile save games       # saves the configuration as a JSON profile
m916 profile load games       # applies a profile (--dry-run to see what would change)
m916 profile list             # profiles saved in ~/.config/m916/profiles
m916 watch                    # live notifications (DPI, battery…)
```

Every write is verified by reading it back. If the mouse is asleep, the tool asks you to move
it and waits up to 10 s.

## Onboard slots

The mouse keeps four independent configurations, called *slots* here ("configs" in the
official app's protocol). Each one is a whole copy of the memory: DPI, buttons, macros,
lights and options. The mouse uses the active one; a button set to "switch config" moves to
the next slot.
`info`, the configuration commands and the graphical interface work on the active slot.

- `m916 slot` shows the active slot and `m916 slot N` selects it.
- `dump`, `restore`, `profile save` and `profile load` accept `--slot N`: they switch to slot
  N, do their work and go back to the slot that was active, also if something fails.
- A backup (`.bin`) does not record its slot; the graphical interface suggests a name like
  `m916-slot2-20261103-1200.bin`. It can be restored to any slot, so this copies slot 2 to 3:
  `m916 dump --slot 2 s2.bin && m916 restore --slot 3 s2.bin`.
- In the graphical interface, choosing a slot makes it the active one in the mouse and loads
  it (it asks first if there are unapplied changes). If the mouse switches slots by itself,
  the window follows it at once.

## Profiles

Besides the full backup, the configuration can be saved as a readable and editable JSON
profile, for example one for games and another for work. Loading a profile writes only the
fields that change, and whatever the profile leaves out stays as it is. A profile does not
belong to any slot: it applies to the active one, or to the one given with `--slot`. This
partial profile only touches the polling rate, two DPI levels and three buttons:

```json
{
  "format": "m916-profile",
  "version": 1,
  "model": "Redragon M916-PRO-1K",
  "report_rate": 1000,
  "dpi": {
    "count": 2,
    "active": 1,
    "levels": [
      {"x": 800, "y": 800, "color": "#00ff00"},
      {"x": 1600, "y": 1600, "color": "#ff0000"}
    ]
  },
  "buttons": {
    "6": {"action": "dpi-lock", "dpi": 400},
    "7": {"action": "shortcut", "keys": "ctrl+c"},
    "8": {"action": "macro", "name": "copy", "repeat": 1,
          "steps": ["+ctrl 10", "+c 10", "-c 10", "-ctrl"]}
  }
}
```

Profiles are saved in `~/.config/m916/profiles/`: on the CLI the name is enough
(`m916 profile load games`), although any path ending in `.json` works too. `m916 profile save`
writes a complete profile. The full format (options, rapid fire, media keys, macros and
validation) is described in [docs/PROFILES.md](docs/PROFILES.md).

## Translations

Translations use gettext. The strings in the code are English and marked with `_()` or
`ngettext()`; `po/LANG.po` holds each translation and `m916/locale/LANG/LC_MESSAGES/m916.mo` the
compiled catalog, which is committed so the program runs from the repository without a build
step. To add a language you need the GNU gettext tools (`sudo dnf install gettext`):

```sh
msginit --no-translator --locale=de --input=po/m916.pot --output-file=po/de.po
# translate every msgstr in po/de.po (a PO editor such as Poedit or Lokalize helps)
tools/i18n.sh                     # merges new strings and compiles the .mo files
python3 -m unittest tests.test_i18n
```

When the text in the code changes, run `tools/i18n.sh` again and complete the new entries in
each `po/*.po`: the tests fail while a string is untranslated or marked fuzzy, or while a
compiled catalog is out of date.

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests simulate the mouse with a real flash dump (`tests/fixtures/m916_flash.bin`), so no
hardware is needed. The interface tests are skipped if PySide6 is not installed.
`tests/test_i18n.py` checks that every string is translated and that the compiled catalogs
are up to date.

## Layout

- `m916/protocol.py`: HID transport and flash access.
- `m916/config.py`: configuration model (decode, encode, validate and diff), including the
  shortcut and macro blocks.
- `m916/profile.py`: JSON profiles (save, validate and apply on top of the model).
- `m916/keys.py`: key names, HID codes and their mapping to Linux key codes.
- `m916/cli.py` and `m916/gui.py`: the two interfaces, on the same model.
- `m916/fake.py`: simulated mouse for tests and development.
- `m916/i18n.py`, `po/` and `m916/locale/`: translations (gettext); `tools/i18n.sh` updates
  them.

## Reverse engineering

`tools/` contains what is needed to repeat the analysis on the official installer, which is
**not** included in this repository:

- `extract_native.py`: extracts `hidusb.dll` / `hidusb32.dll` embedded in `Mouse Drive Beta.exe`.
- `dotnet_constants.py`: dumps enums and constants from the .NET assembly.
- `ghidra/make_flatpak_shim.sh`: lets the flatpak Ghidra run in headless mode.
- `ghidra/DecompileAll.java`: decompiles every function of the DLL into a single `.c` file.

```sh
tools/extract_native.py "Mouse Drive Beta.exe" /tmp/m916-re
tools/ghidra/make_flatpak_shim.sh
~/.local/share/ghidra-flatpak-shim/support/analyzeHeadless /tmp/m916-re/proj m916 \
    -import /tmp/m916-re/hidusb.dll -scriptPath tools/ghidra \
    -postScript DecompileAll.java /tmp/m916-re/hidusb.c
```

## Status

Working: full read-out, DPI, colors, number of levels and active level, report rate, every
button function (including shortcuts, media keys and macros), single-byte options,
dump/restore, the four onboard slots, JSON profiles, notifications and the graphical
interface.

Pending: the receiver functions (long range, pairing) and confirming the sensor mode and idle
times on the hardware.

## License

[GPL-3.0-or-later](LICENSE). Independent project, not affiliated with Redragon. Product names
are used only to identify the supported hardware.
