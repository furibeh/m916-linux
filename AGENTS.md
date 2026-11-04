# AGENTS.md

Guide for coding agents working in this repository. For usage, see `README.md`. The full
protocol is in `docs/PROTOCOL.md`, which is the reference: update it when you discover
something.

## What it is

A Linux configurator for the Redragon M916-PRO-1K mouse (receiver `3554:F5D5`). It talks over
`hidraw` with the protocol of the official Windows configurator ("Mouse Drive Beta"),
reconstructed by reverse engineering. Python 3.9 or later. The CLI has no dependencies and the
GUI uses PySide6 (Qt 6).

## Layout

- `m916/protocol.py`: HID transport (17-byte packets with checksum), flash reads and writes,
  `MouseAsleep` / `ProtocolError`.
- `m916/config.py`: configuration model. It decodes and encodes the flash image, validates,
  and works out which fields changed (`changed_fields`) and what to write on restore
  (`restore_writes`). Both interfaces rely on it: the logic goes here, not in the CLI or the
  GUI.
- `m916/profile.py`: JSON profiles (`docs/PROFILES.md`) on top of the `config.py` model:
  `dumps`/`save` and `apply`, which validates and accepts partial profiles; names without a
  path go to `profiles_dir()` (XDG). In tests, point `XDG_CONFIG_HOME` to a temporary
  directory. If you add a field to the model, add it here too and in the format docs.
- `m916/keys.py`: key names, HID usages and Linux evdev codes.
- `m916/cli.py`: `python3 -m m916 …`.
- `m916/gui.py`: `python3 -m m916.gui`. A single thread (`DeviceWorker`) owns the device.
  The window works on an in-memory copy and **Apply** writes the difference.
- `m916/fake.py`: simulated mouse on top of a flash dump, with the receiver and the cable
  (`plug_cable()`). It must imitate the real firmware, quirks included (see below).
- `tests/`: `unittest`, with `tests/fixtures/m916_flash.bin` (a real dump).
- `tools/`: reverse engineering utilities. The official installer is **not** in the repo.
- `m916/icons/`: application icon (SVG). `APP_ID` in `gui.py`, the `.desktop` file in `data/`
  and the SVG name must match.
- `packaging/m916-linux.spec` + `.copr/Makefile`: RPM for COPR
  (`make -f .copr/Makefile srpm outdir=DIR` packages `HEAD`, not the working tree).
- `.github/workflows/tests.yml`: tests on Fedora with PySide6 and on Python 3.9 without
  PySide6.
- `m916/i18n.py`: translations with gettext (`_`, `ngettext`). The strings in the code are in
  English; `po/es.po` is the Spanish translation and `m916/locale/*/LC_MESSAGES/m916.mo` the
  compiled catalog, which is committed. `tools/i18n.sh` regenerates `po/m916.pot`, updates
  the `.po` files and compiles the `.mo` files. The tests set `LANGUAGE=en` and compare the
  English strings; `tests/test_i18n.py` checks that everything is translated and that the
  `.mo` files are up to date.

## Commands

```sh
python3 -m unittest discover -s tests       # all tests (~40 s; the GUI runs offscreen)
python3 -m m916.gui --fake tests/fixtures/m916_flash.bin   # GUI without hardware
python3 -m m916 info                         # status of the real mouse (read only)
tools/i18n.sh                                # after changing strings: update po/ and the .mo
LANGUAGE=en python3 -m m916.gui --fake …     # the GUI in another language (es, en)
```

For GUI screenshots without a display: `QT_QPA_PLATFORM=offscreen` and
`python3 -m m916.gui --fake … --screenshot DIR`.

## Working with the real mouse

- Reading (`info`, `dump`) is safe. **Writing modifies the flash of the user's mouse**: ask
  for confirmation first, unless the user asked for it explicitly, and make a backup with
  `m916 dump` beforehand.
- If the mouse is asleep, only the receiver answers (command 0x03). Ask the user to move it
  and retry.
- Every write is verified by reading it back. Do not remove that verification.

## Firmware quirks (checked on the hardware)

- Single-byte values are stored as `[v, 0x55 - v]` and blocks end in `0x55 - sum`. If a
  checksum does not match, the mouse uses a default value.
- **Macro blocks (0x300 + 0x180·k):** the firmware only saves the block if it is written from
  its start as a valid macro with checksum. Any other write is acknowledged but discarded. To
  clear a macro, write `EMPTY_MACRO`. That is why restore compares macros by content and not
  byte for byte.
- Write order (`changed_fields`): first the shortcut or macro block and then the function of
  the button that uses it. When the number of DPI levels changes, the active level can never
  point outside the enabled levels.
- Shortcuts store physical keys (HID usages), not characters: the keyboard layout is applied
  by the computer.
- With the USB cable plugged in (`3554:F55E`), the mouse only answers through the cable and the
  receiver says it is not linked (0x03 = 0). Over the cable it does not sleep.
  `protocol.open_mouse` picks the device that answers "online", the cable first.
- There are 4 slots (onboard profiles, 0x0E/0x0F), each with its own flash image at the same
  addresses and its own buttons. Switching slots (with 0x0F or with a button set to function
  9, which cycles through them) is notified with 0x0A `isConfigChanged`. In the code and the
  interface they are called "slot" ("ranura" in the Spanish translation); "profile" always
  means the JSON profile.
- Only the shortcut and macro blocks of the buttons that use them are read; the rest of the
  image stays at `0xFF` (unknown).

If you discover another firmware rule, implement it in `m916/fake.py` too and add a test.

## Reverse engineering

- Write `docs/PROTOCOL.md` in English, with the terms of the GUI and the CLI (slot, receiver,
  rapid fire…). Tag each fact with its confidence level: **[HW]** tested on the mouse,
  **[RE]** read in the code, **[?]** hypothesis. Also give the source (.NET method or DLL
  function).
- The interface code is in the .NET IL (`Mouse Drive Beta.exe`, classes
  `Mouse_Drive_Beta.*`, `DriverLib.*`). The low-level protocol is in the embedded native DLL
  (`tools/extract_native.py` + Ghidra).
- `Language/*.xml` in the installer has the lists of functions and options with their values.
  It is worth looking at before disassembling.
- Some methods of the .NET DLL are not used (e.g. `KeyFunMapDriver.Set*Key`). Before trusting
  a value, check who actually writes the field.

## Conventions

- Code, identifiers, comments, GUI and CLI strings, commit messages and documentation in
  **English**. Every user-visible string is marked with `_()` (or `ngettext()` if it has a
  number) and translated in `po/es.po`; then run `tools/i18n.sh`. If a string has more than
  one placeholder, use named placeholders (`%(file)s`) so the translator can reorder them.
  Do not use `_` as a throwaway variable: a function that assigns it cannot call `_()`.
  The names typed in commands and profiles (actions, options, keys) are not translated.
- Documentation: `README.md` and `docs/` in English. The only Spanish files are
  `README.es.md` (keep it in step with `README.md`) and `po/es.po`.
- `# SPDX-License-Identifier: GPL-3.0-or-later` header in every Python file.
- Style of the existing code: small functions, short docstrings, no new dependencies without
  a reason. The CLI must keep working without PySide6.
- Every behavior change comes with its test, against the simulated mouse.
- When bumping the version: `__version__` in `m916/__init__.py`, `Version:` and `%changelog`
  in the spec, and the `vX.Y.Z` tag (the spec downloads the tarball of that tag).
- Small, focused commits: an imperative title and a list of points in the body.
  Commit or push only when the user asks.
