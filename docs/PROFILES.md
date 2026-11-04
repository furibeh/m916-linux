# JSON profiles

A profile is the mouse configuration in a readable JSON file: DPI, buttons (shortcuts and
macros included), polling rate and options. It lets you keep several configurations, for
example one for games and another for work, and switch between them. It does not replace the
full backup (`m916 dump`), which saves the whole memory byte for byte.

A profile does not belong to any of the four onboard slots (onboard profiles) of the mouse: it
applies to the active slot or, with `--slot N`, to slot N, and then the active one is selected
again. The same goes for `profile save --slot N`.

Long range mode (`m916 long-range`) is not included: the mouse keeps it apart from the slots,
so it is not part of a slot's configuration, and loading a profile never changes it.

```sh
m916 profile save games -m "For games"  # save the current configuration
m916 profile list                       # saved profiles, with their description
m916 profile load games --dry-run       # show how many fields would change
m916 profile load games                 # write only what changes and verify it
m916 profile load games --slot 3        # the same on slot 3
```

## Where they are saved

By default profiles go to `~/.config/m916/profiles/` (or to
`$XDG_CONFIG_HOME/m916/profiles/` if that variable is set), which is created when the first
one is saved. On the CLI:

- A name without `/` and without `.json` (`games`) is `games.json` in that folder.
- A path with `/` or ending in `.json` (`./games.json`, `~/profiles/work.json`) is used as
  is, so a profile can be anywhere.
- `m916 profile list` shows the profiles in the folder with their description
  (`description`, set with `profile save -m TEXT`) and does not need the mouse connected.

In the graphical interface they are in the **Backups and profiles** tab, and the dialogs
open in that folder. *Save profile…* saves what is on screen, unapplied changes included.
*Load profile…* fills in the controls as unapplied changes: review them and press **Apply**
or **Discard changes**.

The logic is in `m916/profile.py`.

## Full example

```json
{
  "format": "m916-profile",
  "version": 1,
  "model": "Redragon M916-PRO-1K",
  "description": "Games",
  "report_rate": 1000,
  "dpi": {
    "count": 5,
    "active": 2,
    "levels": [
      {"x": 1000, "y": 1000, "color": "#ff0000"},
      {"x": 2000, "y": 2000, "color": "#0000ff"},
      {"x": 4000, "y": 4000, "color": "#000000"},
      {"x": 8000, "y": 8000, "color": "#ff00ff"},
      {"x": 26000, "y": 26000, "color": "#ffff00"},
      {"x": 26000, "y": 26000, "color": "#ffff00"},
      {"x": 26000, "y": 26000, "color": "#ffff00"},
      {"x": 26000, "y": 26000, "color": "#ffff00"}
    ]
  },
  "options": {
    "lod": 1,
    "dpi-led-mode": 2,
    "dpi-led-brightness": 128,
    "dpi-led-speed": 3,
    "dpi-led": true,
    "debounce": 8,
    "motion-sync": true,
    "led-off-time": 6,
    "linear-correction": false,
    "ripple-control": false,
    "move-off-led": false,
    "full-performance": false,
    "full-performance-time": 6,
    "sensor-mode": 0
  },
  "buttons": {
    "1": "left",
    "2": "right",
    "3": "middle",
    "4": "back",
    "5": "forward",
    "6": "dpi-loop",
    "7": {"action": "shortcut", "keys": "ctrl+c"},
    "8": {
      "action": "macro",
      "name": "copy",
      "repeat": 1,
      "steps": [
        "+ctrl 10",
        "+c 10",
        "-c 10",
        "-ctrl"
      ]
    },
    "9": "report-rate",
    "10": "dpi-up",
    "11": "dpi-down",
    "12": {"action": "fire", "interval": 10, "shots": 3},
    "13": {"action": "dpi-lock", "dpi": 400},
    "14": {"action": "media", "key": "volume-up"},
    "15": {"raw": [8, 1, 0]},
    "16": "disabled"
  }
}
```

## Rules

### Header

`format` (`"m916-profile"`), `version` (`1`) and `model` (`"Redragon M916-PRO-1K"`) are
required. If the version or the model do not match, the profile is rejected. `description` is
optional: `m916 profile list` shows it and loading ignores it.

### Partial profiles

All the other sections are optional: **whatever is missing is left untouched**. This holds for
`report_rate`, `dpi`, `options` and `buttons`; inside `dpi`, for `count`, `active` and
`levels`; and for each key of `options` and `buttons`. For example, this profile only changes
the DPI of level 1 and button 7:

```json
{
  "format": "m916-profile", "version": 1, "model": "Redragon M916-PRO-1K",
  "dpi": {"levels": [{"x": 800}]},
  "buttons": {"7": {"action": "shortcut", "keys": "ctrl+shift+t"}}
}
```

`levels` can have fewer than 8 entries (they apply from level 1), and a `null` entry leaves
that level as it is.

### Fields

| Field | Value |
|---|---|
| `report_rate` | Hz: 125, 250, 500, 1000, 2000 or 4000. If the mouse has an unknown code, saving leaves it out (with a warning). |
| `dpi.count` | Enabled levels, 1–8. If it changes without `active` and the active level falls outside, it moves to the last enabled one. |
| `dpi.active` | Active level, starting at 1 (as in `m916 info`). |
| `dpi.levels[].x`, `.y` | DPI in multiples of 50, from 50 to 26000. `y` is optional (same as `x`). X and Y must both be below or both above 12800 (and 25600). |
| `dpi.levels[].color` | `"#rrggbb"`, optional. |
| `options` | Keys of `m916 option` with the raw value (times in units of 10 s, brightness 16–255, etc.; see `m916 option --help`). The yes/no options (`dpi-led`, `motion-sync`, `linear-correction`, `ripple-control`, `move-off-led`, `full-performance`) use `true`/`false`. |
| `buttons` | Keys `"1"`…`"16"`, the positions in `m916 info`. |

### Buttons

| Value | Function |
|---|---|
| `"left"`, `"right"`, `"middle"`, `"back"`, `"forward"`, `"dpi-loop"`, `"dpi-up"`, `"dpi-down"`, `"scroll-up"`, `"scroll-down"`, `"scroll-left"`, `"scroll-right"`, `"report-rate"`, `"lights"`, `"next-slot"`, `"disabled"` | Simple action. `"lights"` turns the DPI LED on or off; `"next-slot"` moves to the next onboard slot (each slot has its own buttons). |
| `{"action": "fire", "interval": 20, "shots": 3}` | Rapid fire: each click stays down for `interval` ms and up for as long (10 to 255 ms, so a shot takes twice that); 0 to 3 shots (0 = while held). |
| `{"action": "dpi-lock", "dpi": 400}` | Fixed DPI while held: a multiple of 50, from 50 to 12800. |
| `{"action": "shortcut", "keys": "ctrl+shift+t"}` | Shortcut of up to 3 keys (names from `m916 keys`). |
| `{"action": "shortcut", "events": ["+ctrl", "+a", "-ctrl", "-a"]}` | Shortcut whose events are not a simple combination (up to 6, without delays). |
| `{"action": "media", "key": "volume-up"}` | Media key. |
| `{"action": "macro", "name": "…", "repeat": 1, "steps": […]}` | Macro (see below). |
| `{"raw": [type, p1, p2]}` | A function the program cannot interpret. It is saved raw so it is not lost, and on loading it is written as is. |

Shortcuts store physical keys: the keyboard layout is applied by the computer.

### Macros

- `name`: 1 to 30 Latin-1 characters.
- `repeat`: number of times (1–252), `"while-held"` (while held), `"toggle"` (until pressed
  again) or `"until-any"` (until another button is pressed). Default 1.
- `steps`: 1 to 70 steps, one per string, with the same syntax as `m916 macro`: `+KEY`
  presses, `-KEY` releases and a number after it is the delay in ms after that step
  (`"+c 10"`). The pointer movements recorded by the original program are also accepted
  (`"+move(5,-3)"`, from -128 to 127), as are unnamed codes (`"+key:0x99"`,
  `"-media:0x0123"`).

Each macro takes the block of its own button. The firmware only saves a macro block if it is
written whole from the start, so on loading the whole block is rewritten and then the button
function.

## Validation

On loading, the whole profile is checked and all problems are shown at once, with their
position in the file:

```
error: dpi.levels.3.color: must be "#rrggbb", not "red"
  buttons.7.steps: unknown key name 'ctlr'
```

Unknown fields are also an error, to catch typos like `"repot_rate"`. The CLI checks the
format before opening the mouse. If there are errors, nothing is written.
