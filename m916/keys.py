# SPDX-License-Identifier: GPL-3.0-or-later
"""Key names for shortcut and macro events (HID usages, see docs/PROTOCOL.md)."""
import re

# Event kinds, the low nibble of the first byte of an event (DriverLib.HID_CODE_TYPE)
MODIFIER, KEY, MEDIA, POWER, MOUSE, MOVE = range(6)
KIND_NAMES = {MODIFIER: "modifier", KEY: "key", MEDIA: "media", POWER: "power", MOUSE: "mouse",
              MOVE: "move"}

MODIFIERS = {
    "ctrl": 0x01, "shift": 0x02, "alt": 0x04, "super": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "rsuper": 0x80,
}
MODIFIER_ALIASES = {"lctrl": "ctrl", "control": "ctrl", "lshift": "shift", "lalt": "alt",
                    "meta": "super", "win": "super", "lsuper": "super", "altgr": "ralt"}

# Keyboard page (0x07) usages
KEYS = {chr(ord("a") + i): 0x04 + i for i in range(26)}
KEYS.update({str((i + 1) % 10): 0x1E + i for i in range(10)})
KEYS.update({
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, "minus": 0x2D,
    "equal": 0x2E, "leftbrace": 0x2F, "rightbrace": 0x30, "backslash": 0x31, "semicolon": 0x33,
    "apostrophe": 0x34, "grave": 0x35, "comma": 0x36, "dot": 0x37, "slash": 0x38,
    "capslock": 0x39, "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48, "insert": 0x49,
    "home": 0x4A, "pageup": 0x4B, "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E, "right": 0x4F,
    "left": 0x50, "down": 0x51, "up": 0x52, "numlock": 0x53, "kpslash": 0x54,
    "kpasterisk": 0x55, "kpminus": 0x56, "kpplus": 0x57, "kpenter": 0x58, "kp0": 0x62,
    "kpdot": 0x63, "102nd": 0x64, "menu": 0x65,
})
KEYS.update({"f%d" % (i + 1): 0x3A + i for i in range(12)})
KEYS.update({"f%d" % (i + 13): 0x68 + i for i in range(12)})
KEYS.update({"kp%d" % (i + 1): 0x59 + i for i in range(9)})
KEY_ALIASES = {"return": "enter", "escape": "esc", "del": "delete", "ins": "insert",
               "pgup": "pageup", "pgdn": "pagedown", "print": "printscreen", "period": "dot"}

# Consumer page (0x0C) usages offered by the original app (Language/*.xml, SubKeyFunction)
MEDIA_KEYS = {
    "play-pause": 0x00CD, "next": 0x00B5, "previous": 0x00B6, "stop": 0x00B7, "mute": 0x00E2,
    "volume-up": 0x00E9, "volume-down": 0x00EA, "media-player": 0x0183, "email": 0x018A,
    "calculator": 0x0192, "my-computer": 0x0194, "www-search": 0x0221, "www-home": 0x0223,
    "www-back": 0x0224, "www-forward": 0x0225, "www-stop": 0x0226, "www-refresh": 0x0227,
    "www-favorites": 0x022A,
}

MOUSE_BUTTONS = {"mouse-left": 0x01, "mouse-right": 0x02, "mouse-middle": 0x04,
                 "mouse-back": 0x08, "mouse-forward": 0x10}

# Linux evdev key codes (Qt nativeScanCode() - 8 on X11 and Wayland) -> (kind, code)
EVDEV = {
    1: "esc", 12: "minus", 13: "equal", 14: "backspace", 15: "tab", 26: "leftbrace",
    27: "rightbrace", 28: "enter", 29: "ctrl", 39: "semicolon", 40: "apostrophe", 41: "grave",
    42: "shift", 43: "backslash", 51: "comma", 52: "dot", 53: "slash", 54: "rshift",
    55: "kpasterisk", 56: "alt", 57: "space", 58: "capslock", 69: "numlock", 70: "scrolllock",
    71: "kp7", 72: "kp8", 73: "kp9", 74: "kpminus", 75: "kp4", 76: "kp5", 77: "kp6",
    78: "kpplus", 79: "kp1", 80: "kp2", 81: "kp3", 82: "kp0", 83: "kpdot", 86: "102nd",
    87: "f11", 88: "f12", 96: "kpenter", 97: "rctrl", 98: "kpslash", 99: "printscreen",
    100: "ralt", 102: "home", 103: "up", 104: "pageup", 105: "left", 106: "right", 107: "end",
    108: "down", 109: "pagedown", 110: "insert", 111: "delete", 119: "pause", 125: "super",
    126: "rsuper", 127: "menu",
}
EVDEV.update({2 + i: str((i + 1) % 10) for i in range(10)})
EVDEV.update({16 + i: c for i, c in enumerate("qwertyuiop")})
EVDEV.update({30 + i: c for i, c in enumerate("asdfghjkl")})
EVDEV.update({44 + i: c for i, c in enumerate("zxcvbnm")})
EVDEV.update({59 + i: "f%d" % (i + 1) for i in range(10)})
EVDEV.update({183 + i: "f%d" % (i + 13) for i in range(12)})
EVDEV.update({113: "mute", 114: "volume-down", 115: "volume-up", 163: "next", 164: "play-pause",
              165: "previous", 166: "stop", 140: "calculator"})


def lookup(name):
    """(kind, code) for a key name such as "ctrl", "c", "f5", "mouse-left" or "mute"."""
    name = name.strip().lower()
    name = MODIFIER_ALIASES.get(name, KEY_ALIASES.get(name, name))
    if name in MODIFIERS:
        return MODIFIER, MODIFIERS[name]
    if name in KEYS:
        return KEY, KEYS[name]
    if name in MEDIA_KEYS:
        return MEDIA, MEDIA_KEYS[name]
    if name in MOUSE_BUTTONS:
        return MOUSE, MOUSE_BUTTONS[name]
    kind, _, code = name.partition(":")  # name() of a code without a name, e.g. "key:0x99"
    if kind in _KINDS and re.fullmatch(r"0x[0-9a-f]{1,4}", code):
        return _KINDS[kind], int(code, 16)
    raise KeyError("unknown key name %r" % name)


_KINDS = {n: k for k, n in KIND_NAMES.items() if k != MOVE}
_NAMES = {}
for _table, _kind in ((MODIFIERS, MODIFIER), (KEYS, KEY), (MEDIA_KEYS, MEDIA),
                      (MOUSE_BUTTONS, MOUSE)):
    for _name, _code in _table.items():
        _NAMES.setdefault((_kind, _code), _name)


def name(kind, code):
    """Inverse of lookup(); unknown codes are shown as kind:0xNN."""
    return _NAMES.get((kind, code)) or "%s:0x%02x" % (KIND_NAMES.get(kind, kind), code)
