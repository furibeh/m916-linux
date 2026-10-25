# SPDX-License-Identifier: GPL-3.0-or-later
"""Readable JSON profiles of the configuration (see docs/PROFILES.md).

Unlike a flash dump, a profile may leave sections out: loading it only changes what it has."""
import json
import re

from . import config as cfg, keys

FORMAT = "m916-profile"
VERSION = 1
MODEL = "Redragon M916-PRO-1K"
TOP_KEYS = {"format", "version", "model", "description", "report_rate", "dpi", "options",
            "buttons"}
WIDTH = 96  # values that fit are written on one line


class ProfileError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# --- saving ------------------------------------------------------------------------------

def button_dict(conf, k):
    """JSON value of button k: an action name, an object with "action", or {"raw": [...]}."""
    t, p1, p2 = value = conf.buttons[k]
    for name, action in cfg.BUTTON_ACTIONS.items():
        if action == value:
            return name
    if t == cfg.KEY_FIRE and p1 >= cfg.FIRE_INTERVAL_MIN and p2 <= cfg.FIRE_TIMES_MAX:
        return {"action": "fire", "interval": p1, "shots": p2}
    if t == cfg.KEY_DPI_LOCK and p2 == 0:
        return {"action": "dpi-lock", "dpi": (p1 + 1) * cfg.DPI_STEP}
    if t == cfg.KEY_SHORTCUT and (p1, p2) == (0, 0) and k in conf.shortcuts:
        events = conf.shortcuts[k]
        combo = cfg.format_combo(events)
        if combo is None:
            return {"action": "shortcut", "events": [str(e) for e in events]}
        if len(events) == 2 and events[0].kind == keys.MEDIA:
            return {"action": "media", "key": combo}
        return {"action": "shortcut", "keys": combo}
    if t == cfg.KEY_MACRO and p1 == k and p2 >= 1 and k in conf.macros:
        macro = conf.macros[k]
        return {"action": "macro", "name": macro.name, "repeat": cfg.REPEAT_NAMES.get(p2, p2),
                "steps": [str(e) for e in macro.steps]}
    return {"raw": list(value)}


def to_dict(conf):
    """Whole profile of `conf`; an unknown report rate code is left out."""
    data = {"format": FORMAT, "version": VERSION, "model": MODEL}
    if conf.report_rate in cfg.RATE_HZ:
        data["report_rate"] = cfg.RATE_HZ[conf.report_rate]
    data["dpi"] = {"count": conf.dpi_count, "active": conf.dpi_active + 1,
                   "levels": [{"x": lv.x, "y": lv.y, "color": "#%02x%02x%02x" % tuple(lv.color)}
                              for lv in conf.levels]}
    data["options"] = {name: bool(v) if name in cfg.BOOL_OPTIONS and v in (0, 1) else v
                       for name, v in conf.options.items()}
    data["buttons"] = {str(k + 1): button_dict(conf, k) for k in range(len(conf.buttons))}
    return data


def _encode(value, indent=0, col=0):
    flat = json.dumps(value, ensure_ascii=False)
    if col + len(flat) <= WIDTH or not isinstance(value, (dict, list)) or not value:
        return flat
    pad = " " * (indent + 2)
    if isinstance(value, list):
        items = [pad + _encode(v, indent + 2, indent + 2) for v in value]
        return "[\n%s\n%s]" % (",\n".join(items), " " * indent)
    items = []
    for key, v in value.items():
        head = "%s%s: " % (pad, json.dumps(key, ensure_ascii=False))
        items.append(head + _encode(v, indent + 2, len(head)))
    return "{\n%s\n%s}" % (",\n".join(items), " " * indent)


def dumps(conf):
    return _encode(to_dict(conf)) + "\n"


def save(path, conf):
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(conf))


# --- loading -----------------------------------------------------------------------------

def load(path):
    """Parsed JSON of a profile file (checked when applied)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ProfileError(["%s: invalid JSON: %s" % (path, e)])
    except (OSError, UnicodeDecodeError) as e:
        raise ProfileError(["%s: %s" % (path, e)])


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class _Loader:
    """Applies a profile to a Config, collecting every problem with its place in the file."""

    def __init__(self, conf):
        self.conf = conf
        self.errors = []

    def error(self, where, msg):
        self.errors.append("%s: %s" % (where, msg) if where else msg)

    def obj(self, where, value, allowed, required=()):
        """True if `value` is an object with only `allowed` keys and all `required` ones."""
        if not isinstance(value, dict):
            self.error(where, "must be an object")
            return False
        ok = True
        for key in value:
            if key not in allowed:
                self.error(where, "unknown field %r (expected %s)"
                           % (key, ", ".join(sorted(allowed))))
                ok = False
        for key in required:
            if key not in value:
                self.error(where, "missing field %r" % key)
                ok = False
        return ok

    def int(self, where, value, lo=None, hi=None):
        if not _is_int(value):
            self.error(where, "must be an integer, not %s" % json.dumps(value))
            return None
        if lo is not None and not lo <= value <= hi:
            self.error(where, "must be %d..%d" % (lo, hi))
            return None
        return value

    def header(self, data):
        if data.get("format") != FORMAT:
            self.error("", "not an M916 profile (\"format\" must be %r)" % FORMAT)
            return False
        if data.get("version") != VERSION:
            self.error("version", "unsupported profile version %s (this program reads %d)"
                       % (json.dumps(data.get("version")), VERSION))
            return False
        if data.get("model") != MODEL:
            self.error("model", "profile is for %s, not %s" % (json.dumps(data.get("model")), MODEL))
            return False
        return True

    def run(self, data):
        if not self.obj("", data, TOP_KEYS, ("format", "version", "model")) \
                or not self.header(data):
            return
        if "description" in data and not isinstance(data["description"], str):
            self.error("description", "must be a string")
        if "report_rate" in data:
            self.report_rate(data["report_rate"])
        if "dpi" in data:
            self.dpi(data["dpi"])
        if "options" in data:
            self.options(data["options"])
        if "buttons" in data:
            self.buttons(data["buttons"])

    def report_rate(self, hz):
        if _is_int(hz) and hz in cfg.RATES:
            self.conf.report_rate = cfg.RATES[hz]
        else:
            self.error("report_rate", "must be one of %s Hz" % ", ".join(map(str, sorted(cfg.RATES))))

    def dpi(self, dpi):
        conf = self.conf
        if not self.obj("dpi", dpi, {"count", "active", "levels"}):
            return
        if "levels" in dpi:
            levels = dpi["levels"]
            if not isinstance(levels, list) or len(levels) > cfg.MAX_DPI_LEVELS:
                self.error("dpi.levels", "must be a list of up to %d levels" % cfg.MAX_DPI_LEVELS)
            else:
                for i, level in enumerate(levels):
                    if level is not None:
                        self.level("dpi.levels.%d" % (i + 1), conf.levels[i], level)
        if "count" in dpi:
            count = self.int("dpi.count", dpi["count"], 1, cfg.MAX_DPI_LEVELS)
            if count is not None:
                conf.dpi_count = count
                if "active" not in dpi:
                    conf.dpi_active = min(conf.dpi_active, count - 1)
        if "active" in dpi:
            active = self.int("dpi.active", dpi["active"], 1, cfg.MAX_DPI_LEVELS)
            if active is not None:
                conf.dpi_active = active - 1

    def level(self, where, level, value):
        if not self.obj(where, value, {"x", "y", "color"}, ("x",)):
            return
        x = self.int(where + ".x", value["x"])
        y = self.int(where + ".y", value.get("y", value["x"]))
        if x is not None and y is not None:
            level.x, level.y = x, y
        if "color" in value:
            color = value["color"]
            if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                level.color = tuple(bytes.fromhex(color[1:]))
            else:
                self.error(where + ".color", "must be \"#rrggbb\", not %s" % json.dumps(color))

    def options(self, options):
        if not isinstance(options, dict):
            self.error("options", "must be an object")
            return
        for name, value in options.items():
            where = "options.%s" % name
            if name not in cfg.OPTIONS:
                self.error(where, "unknown option (expected %s)" % ", ".join(sorted(cfg.OPTIONS)))
            elif isinstance(value, bool) and name in cfg.BOOL_OPTIONS:
                self.conf.options[name] = int(value)
            else:
                value = self.int(where, value, 0, 255)
                if value is not None:
                    self.conf.options[name] = value

    def buttons(self, buttons):
        if not isinstance(buttons, dict):
            self.error("buttons", "must be an object")
            return
        for key, value in buttons.items():
            where = "buttons.%s" % key
            if not (key.isdigit() and 1 <= int(key) <= cfg.KEY_COUNT):
                self.error(where, "buttons are numbered 1..%d" % cfg.KEY_COUNT)
                continue
            try:
                self.button(where, int(key) - 1, value)
            except (ValueError, KeyError) as e:
                self.error(where, e.args[0])

    def button(self, where, k, value):
        conf = self.conf
        if isinstance(value, str):
            if value not in cfg.BUTTON_ACTIONS:
                self.error(where, "unknown action %r (expected %s, or an object for fire, "
                                  "dpi-lock, shortcut, media and macro)"
                           % (value, ", ".join(cfg.BUTTON_ACTIONS)))
                return
            conf.buttons[k] = cfg.BUTTON_ACTIONS[value]
            return
        if isinstance(value, dict) and "raw" in value:
            raw = value["raw"]
            if self.obj(where, value, {"raw"}):
                if isinstance(raw, list) and len(raw) == 3 and all(
                        _is_int(v) and 0 <= v <= 255 for v in raw):
                    conf.buttons[k] = tuple(raw)
                else:
                    self.error(where + ".raw", "must be [type, p1, p2] with values 0..255")
            return
        if not self.obj(where, value, {"action", "interval", "shots", "dpi", "keys", "events",
                                       "key", "name", "repeat", "steps"}, ("action",)):
            return
        action = value["action"]
        fields = {"fire": ({"interval", "shots"}, {"interval", "shots"}),
                  "dpi-lock": ({"dpi"}, {"dpi"}),
                  "shortcut": ({"keys", "events"}, ()),
                  "media": ({"key"}, {"key"}),
                  "macro": ({"name", "repeat", "steps"}, {"name", "steps"})}
        if action not in fields:
            self.error(where + ".action", "unknown action %s (expected %s)"
                       % (json.dumps(action), ", ".join(fields)))
            return
        allowed, required = fields[action]
        if not self.obj(where, value, allowed | {"action"}, required):
            return
        getattr(self, "button_" + action.replace("-", "_"))(where, k, value)

    def button_fire(self, where, k, value):
        interval = self.int(where + ".interval", value["interval"], cfg.FIRE_INTERVAL_MIN, 255)
        shots = self.int(where + ".shots", value["shots"], 0, cfg.FIRE_TIMES_MAX)
        if interval is not None and shots is not None:
            self.conf.buttons[k] = (cfg.KEY_FIRE, interval, shots)

    def button_dpi_lock(self, where, k, value):
        dpi = self.int(where + ".dpi", value["dpi"], cfg.DPI_MIN, 256 * cfg.DPI_STEP)
        if dpi is not None:
            if dpi % cfg.DPI_STEP:
                self.error(where + ".dpi", "must be a multiple of %d" % cfg.DPI_STEP)
            else:
                self.conf.buttons[k] = (cfg.KEY_DPI_LOCK, dpi // cfg.DPI_STEP - 1, 0)

    def button_shortcut(self, where, k, value):
        if ("keys" in value) == ("events" in value):
            self.error(where, "a shortcut has either \"keys\" or \"events\"")
            return
        if "keys" in value:
            if not isinstance(value["keys"], str):
                self.error(where + ".keys", "must be a string such as \"ctrl+c\"")
                return
            events = cfg.parse_combo(value["keys"])
        else:
            events = self.steps(where + ".events", value["events"])
            if events is None:
                return
            if any(e.delay for e in events):
                self.error(where + ".events", "shortcut events have no delays")
                return
            if not 1 <= len(events) <= 2 * cfg.SHORTCUT_KEYS_MAX:
                self.error(where + ".events", "a shortcut has 1..%d events"
                           % (2 * cfg.SHORTCUT_KEYS_MAX))
                return
        self.conf.set_shortcut(k, events)

    def button_media(self, where, k, value):
        name = value["key"]
        if not isinstance(name, str) or keys.lookup(name)[0] != keys.MEDIA:
            self.error(where + ".key", "%s is not a media key (expected %s)"
                       % (json.dumps(name), ", ".join(keys.MEDIA_KEYS)))
            return
        self.conf.set_shortcut(k, cfg.combo_events([keys.lookup(name)]))

    def button_macro(self, where, k, value):
        repeat = value.get("repeat", 1)
        if repeat in cfg.REPEAT_MODES:
            repeat = cfg.REPEAT_MODES[repeat]
        elif not (_is_int(repeat) and 1 <= repeat <= cfg.REPEAT_COUNT_MAX):
            self.error(where + ".repeat", "must be 1..%d or one of %s"
                       % (cfg.REPEAT_COUNT_MAX, ", ".join(cfg.REPEAT_MODES)))
            return
        if not isinstance(value["name"], str):
            self.error(where + ".name", "must be a string")
            return
        steps = self.steps(where + ".steps", value["steps"])
        if steps is None:
            return
        macro = cfg.Macro(value["name"], steps)
        errors = macro.errors()
        for e in errors:
            self.error(where, e)
        if not errors:
            self.conf.set_macro(k, macro, repeat)

    def steps(self, where, value):
        """Events from a list of steps such as ["+ctrl 10", "+c", "-c", "-ctrl"]."""
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            self.error(where, "must be a list of steps such as [\"+ctrl 10\", \"-ctrl\"]")
            return None
        try:
            return cfg.parse_steps(" ".join(value))
        except (ValueError, KeyError) as e:
            self.error(where, e.args[0])
            return None


def apply(conf, data):
    """Change `conf` (decoded from read_image()) with what the profile `data` has.

    Raises ProfileError listing every problem, including those found by Config.validate()."""
    loader = _Loader(conf)
    loader.run(data)
    if loader.errors:
        raise ProfileError(loader.errors)
    errors = conf.validate()
    if errors:
        raise ProfileError(errors)


def check(data):
    """Check the profile without a mouse: format, names and types. Ranges that depend on the
    rest of the configuration are only checked by apply()."""
    conf = cfg.Config(report_rate=cfg.RATES[1000], dpi_count=1, dpi_active=0,
                      levels=[cfg.DpiLevel(800, 800, (0, 0, 0)) for _ in range(cfg.MAX_DPI_LEVELS)],
                      options={name: 0 for name in cfg.OPTIONS},
                      buttons=[(0, 0, 0)] * cfg.KEY_COUNT)
    loader = _Loader(conf)
    loader.run(data)
    if loader.errors:
        raise ProfileError(loader.errors)
