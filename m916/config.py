# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings stored in the flash (ProtocolDataParser layout): the first 0xC0 bytes, plus one
shortcut block and one macro block per button."""
import re
from dataclasses import dataclass, field

from . import keys
from .protocol import CHUNK, FLASH_SIZE, checksum, with_check

CONFIG_SIZE = 0xC0
MAX_DPI_LEVELS = 8
KEY_COUNT = 16
DPI_MIN, DPI_MAX, DPI_STEP = 50, 26000, 50

OFF_REPORT_RATE = 0x00
OFF_DPI_COUNT = 0x02
OFF_DPI_ACTIVE = 0x04
OFF_DPI = 0x0C        # 8 x [x, y, ex, chk]
OFF_DPI_COLOR = 0x2C  # 8 x [r, g, b, chk]
OFF_KEYS = 0x60       # 16 x [type, p1, p2, chk]
OFF_SHORTCUTS = 0x100  # 16 x 0x20: [n] n x [state|kind, lo, hi] chk
SHORTCUT_SIZE = 0x20
OFF_MACROS = 0x300     # 16 x 0x180: [len] name[30] [n] n x [state|kind, lo, hi, delay_hi, delay_lo] chk
MACRO_SIZE = 0x180
MACRO_NAME_MAX = 30
MACRO_STEPS_MAX = 70
SHORTCUT_KEYS_MAX = 3
PRESS, RELEASE = 0x80, 0x40

# Single-byte settings stored as [value, 0x55 - value] (MouseConfigParser)
OPTIONS = {
    "lod": 0x0A,                    # silenceHeight
    "dpi-led-mode": 0x4C,           # DPILed.mode
    "dpi-led-brightness": 0x4E,     # DPILed.brightness
    "dpi-led-speed": 0x50,          # DPILed.breathSpeed
    "dpi-led": 0x52,                # DPILed.enable
    "debounce": 0xA9,               # keyDebounceTime, ms
    "motion-sync": 0xAB,
    "led-off-time": 0xAD,           # allLedOffTime, x 10 s
    "linear-correction": 0xAF,
    "ripple-control": 0xB1,
    "move-off-led": 0xB3,
    "full-performance": 0xB5,       # sensorCustomSleepTimeEnable
    "full-performance-time": 0xB7,  # sensorSleepTime, x 10 s
    "sensor-mode": 0xB9,            # sensorPowerSavingModeEnable: 0 LP, 1 HP
}
BOOL_OPTIONS = {"dpi-led", "motion-sync", "linear-correction", "ripple-control", "move-off-led",
                "full-performance"}

# Choices offered by the original app (Language/*.xml combo boxes and _3In1/FormMain
# handlers); other stored values are kept as they are
LOD_CHOICES = {1: "1 mm", 2: "2 mm"}
SENSOR_MODES = {0: "LP", 1: "HP"}
DPI_LED_MODES = {1: "steady", 2: "flicker"}
FULL_PERFORMANCE_TIMES = [3, 6, 12, 30, 60, 90]                    # x 10 s
LED_OFF_TIMES = [1, 3, 6, 30, 60, 90, 120, 150, 180, 210, 240]   # x 10 s
DPI_LED_BRIGHTNESS = [16, 30, 60, 90, 128, 150, 180, 210, 230, 255]  # levels 1-10
DPI_LED_SPEEDS = range(1, 6)


def format_time(tens):
    """Duration stored in units of 10 s."""
    s = tens * 10
    return "%d min" % (s // 60) if s >= 60 and s % 60 == 0 else "%d s" % s


def describe_option(name, value):
    text = {"lod": LOD_CHOICES.get(value), "sensor-mode": SENSOR_MODES.get(value),
            "dpi-led-mode": DPI_LED_MODES.get(value), "debounce": "%d ms" % value,
            "led-off-time": format_time(value), "full-performance-time": format_time(value),
            }.get(name)
    if name == "dpi-led-brightness" and value in DPI_LED_BRIGHTNESS:
        text = "level %d" % (DPI_LED_BRIGHTNESS.index(value) + 1)
    return "%d (%s)" % (value, text) if text else str(value)

RATES = {1000: 0x01, 500: 0x02, 250: 0x04, 125: 0x08, 2000: 0x10, 4000: 0x20}
RATE_HZ = {v: k for k, v in RATES.items()}

# Button functions (KEY_CLASS + params) that do not need extra flash blocks
BUTTON_ACTIONS = {
    "disabled": (0x00, 0, 0),
    "left": (0x01, 0x01, 0),
    "right": (0x01, 0x02, 0),
    "middle": (0x01, 0x04, 0),
    "back": (0x01, 0x08, 0),
    "forward": (0x01, 0x10, 0),
    "dpi-loop": (0x02, 0x01, 0),
    "dpi-up": (0x02, 0x02, 0),
    "dpi-down": (0x02, 0x03, 0),
    "scroll-left": (0x03, 0x01, 0),
    "scroll-right": (0x03, 0x02, 0),
    "scroll-up": (0x0B, 0x01, 0),
    "scroll-down": (0x0B, 0x02, 0),
    "report-rate": (0x07, 0, 0),
}
KEY_FIRE = 0x04       # p1 = interval in ms (10-255), p2 = shots (0-3, 0 = while held)
KEY_SHORTCUT = 0x05   # p1 = p2 = 0, events in the button's shortcut block
KEY_MACRO = 0x06      # p1 = button index (its own macro block), p2 = repeat
KEY_DPI_LOCK = 0x0A   # p1 = dpi/50 - 1
KEY_CLASS_NAMES = {0: "disabled", 1: "mouse", 2: "dpi", 3: "h-scroll", 4: "fire", 5: "shortcut",
                   6: "macro", 7: "report-rate", 8: "lights", 9: "config", 10: "dpi-lock",
                   11: "v-scroll"}
FIRE_INTERVAL_MIN, FIRE_TIMES_MAX = 10, 3

# Macro repeat modes in p2; 1-252 is a repeat count (FormMain.customButton_Save_Click)
REPEAT_TOGGLE = 253        # until the button is pressed again
REPEAT_WHILE_HELD = 254    # until the button is released
REPEAT_UNTIL_ANY = 255     # until any button is pressed
REPEAT_MODES = {"toggle": REPEAT_TOGGLE, "while-held": REPEAT_WHILE_HELD,
                "until-any": REPEAT_UNTIL_ANY}
REPEAT_NAMES = {v: k for k, v in REPEAT_MODES.items()}
REPEAT_COUNT_MAX = 252

# What the original app writes to delete a macro (empty name, no steps)
EMPTY_MACRO = bytes([0]) + b"\xff" * MACRO_NAME_MAX + with_check([0])


def dpi_decode(lo, ex):
    """PAW3395 (DPIControl.ValueToDPI): value = dpi/50 - 1; high bits in DPIex bits 2-3/6-7."""
    return (((ex >> 2) & 0x3) << 8 | lo) * 50 + 50


def dpi_encode(dpi):
    value = dpi // 50 - 1
    hi = value >> 8
    return value & 0xFF, (hi << 2) | (hi << 6)


def dpi_band(dpi):
    """X and Y share the DPIex byte, so both must have the same high bits."""
    return (dpi // 50 - 1) >> 8


def shortcut_addr(k):
    return OFF_SHORTCUTS + SHORTCUT_SIZE * k


def macro_addr(k):
    return OFF_MACROS + MACRO_SIZE * k


@dataclass(frozen=True)
class Event:
    """One step of a shortcut or macro. `code` is 16 bits: low byte first in the flash
    (media keys use both bytes, MOVE events store x and y)."""
    press: bool
    kind: int
    code: int
    delay: int = 0  # ms after this step; macros only

    def raw(self):
        return bytes([(PRESS if self.press else RELEASE) | self.kind, self.code & 0xFF,
                      self.code >> 8])

    @classmethod
    def from_raw(cls, b, delay=0):
        return cls(bool(b[0] & PRESS), b[0] & 0x0F, b[1] | b[2] << 8, delay)

    def __str__(self):
        if self.kind == keys.MOVE:
            x, y = (self.code & 0xFF), self.code >> 8
            text = "move(%d,%d)" % (x - 256 * (x > 127), y - 256 * (y > 127))
        else:
            text = keys.name(self.kind, self.code)
        return ("+" if self.press else "-") + text + (" %d" % self.delay if self.delay else "")


def combo_events(codes):
    """Shortcut events for a key combination of (kind, code): press all, then release in
    reverse order (ValueConvert.App_AddShortCutKey)."""
    if not 1 <= len(codes) <= SHORTCUT_KEYS_MAX:
        raise ValueError("a shortcut has 1 to %d keys" % SHORTCUT_KEYS_MAX)
    return ([Event(True, kind, code) for kind, code in codes]
            + [Event(False, kind, code) for kind, code in reversed(codes)])


def parse_combo(text):
    """"ctrl+shift+t" -> events; a single media key name gives a media shortcut."""
    return combo_events([keys.lookup(n) for n in text.replace(" ", "").split("+") if n])


def format_combo(events):
    """Inverse of parse_combo() when the events are a plain combination, else None."""
    codes = [(e.kind, e.code) for e in events if e.press]
    if not 1 <= len(codes) <= SHORTCUT_KEYS_MAX or events != combo_events(codes):
        return None
    return "+".join(keys.name(kind, code) for kind, code in codes)


MOVE_STEP = re.compile(r"([+-]?)move\((-?\d+),(-?\d+)\)")


def parse_steps(text):
    """Macro steps from "+ctrl 10 +c 10 -c 10 -ctrl": +KEY press, -KEY release, and a number
    is the delay in ms after the previous step. Also reads what format_steps() writes for
    pointer moves ("+move(5,-3)", -128..127) and unnamed codes ("+key:0x99")."""
    steps = []
    for token in re.findall(r"[+-]?move\([^)]*\)|[^\s,]+", text):
        move = MOVE_STEP.fullmatch(re.sub(r"\s", "", token))
        if move:
            x, y = int(move.group(2)), int(move.group(3))
            if not (-128 <= x <= 127 and -128 <= y <= 127):
                raise ValueError("bad macro step %r (moves are -128..127)" % token)
            steps.append(Event(move.group(1) != "-", keys.MOVE, (x & 0xFF) | (y & 0xFF) << 8))
        elif token[0] in "+-" and len(token) > 1:
            kind, code = keys.lookup(token[1:])
            steps.append(Event(token[0] == "+", kind, code))
        elif token.isdigit() and steps:
            last = steps[-1]
            steps[-1] = Event(last.press, last.kind, last.code, last.delay + int(token))
        else:
            raise ValueError("bad macro step %r (use +KEY, -KEY or a delay in ms)" % token)
    return steps


def format_steps(steps):
    return " ".join(str(e) for e in steps)


def decode_shortcut(block):
    n = block[0]
    if not 1 <= n <= 2 * SHORTCUT_KEYS_MAX or block[1 + 3 * n] != checksum(block[:1 + 3 * n]):
        return None
    return [Event.from_raw(block[1 + 3 * i:4 + 3 * i]) for i in range(n)]


def encode_shortcut(events):
    data = bytes([len(events)]) + b"".join(e.raw() for e in events)
    return with_check(data)


@dataclass
class Macro:
    name: str
    steps: list

    @classmethod
    def decode(cls, block):
        length, n = block[0], block[0x1F]
        if not 1 <= length <= MACRO_NAME_MAX or not 1 <= n <= MACRO_STEPS_MAX \
                or block[0x20 + 5 * n] != checksum(block[0x1F:0x20 + 5 * n]):
            return None
        steps = [Event.from_raw(block[0x20 + 5 * i:0x23 + 5 * i],
                                block[0x23 + 5 * i] << 8 | block[0x24 + 5 * i]) for i in range(n)]
        return cls(block[1:1 + length].decode("latin-1"), steps)

    def encode(self):
        name = self.name.encode("latin-1")
        body = bytes([len(self.steps)]) + b"".join(
            e.raw() + bytes([e.delay >> 8, e.delay & 0xFF]) for e in self.steps)
        return bytes([len(name)]) + name.ljust(MACRO_NAME_MAX, b"\xff") + with_check(body)

    def errors(self):
        errors = []
        try:
            name = self.name.encode("latin-1")
        except UnicodeEncodeError:
            name = b""
            errors.append("macro name must use Latin-1 characters")
        if not 1 <= len(name) <= MACRO_NAME_MAX:
            errors.append("macro name must have 1..%d characters" % MACRO_NAME_MAX)
        if not 1 <= len(self.steps) <= MACRO_STEPS_MAX:
            errors.append("a macro has 1..%d steps" % MACRO_STEPS_MAX)
        if any(not 0 <= e.delay <= 0xFFFF for e in self.steps):
            errors.append("macro delays must be 0..65535 ms")
        return errors


def describe_repeat(p2):
    return {REPEAT_TOGGLE: "until pressed again", REPEAT_WHILE_HELD: "while held",
            REPEAT_UNTIL_ANY: "until any button"}.get(p2, "%d time%s" % (p2, "s" * (p2 != 1)))


def describe_key(t, p1, p2):
    for name, val in BUTTON_ACTIONS.items():
        if val == (t, p1, p2):
            return name
    if t == KEY_FIRE:
        return "fire (every %d ms, %s)" % (p1, "%d shots" % p2 if p2 else "while held")
    if t == KEY_DPI_LOCK:
        return "dpi-lock (%d DPI)" % ((p1 + 1) * 50)
    return "%s (p1=0x%02x, p2=0x%02x)" % (KEY_CLASS_NAMES.get(t, "type 0x%02x" % t), p1, p2)


@dataclass
class DpiLevel:
    x: int
    y: int
    color: tuple


@dataclass
class Config:
    report_rate: int                       # raw code, see RATES
    dpi_count: int
    dpi_active: int                        # 0-based
    levels: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    buttons: list = field(default_factory=list)  # (type, p1, p2)
    shortcuts: dict = field(default_factory=dict)  # button -> [Event], for KEY_SHORTCUT
    macros: dict = field(default_factory=dict)     # button -> Macro, for KEY_MACRO

    @classmethod
    def decode(cls, image):
        """`image` is the CONFIG_SIZE header or a FLASH_SIZE image (see read_image())."""
        levels = []
        for i in range(MAX_DPI_LEVELS):
            x, y, ex = image[OFF_DPI + 4 * i:OFF_DPI + 4 * i + 3]
            levels.append(DpiLevel(dpi_decode(x, ex), dpi_decode(y, ex),
                                   tuple(image[OFF_DPI_COLOR + 4 * i:OFF_DPI_COLOR + 4 * i + 3])))
        conf = cls(
            report_rate=image[OFF_REPORT_RATE],
            dpi_count=image[OFF_DPI_COUNT],
            dpi_active=image[OFF_DPI_ACTIVE],
            levels=levels,
            options={name: image[off] for name, off in OPTIONS.items()},
            buttons=[tuple(image[OFF_KEYS + 4 * k:OFF_KEYS + 4 * k + 3]) for k in range(KEY_COUNT)],
        )
        if len(image) >= FLASH_SIZE:
            for k, (t, _, _) in enumerate(conf.buttons):
                if t == KEY_SHORTCUT:
                    events = decode_shortcut(image[shortcut_addr(k):shortcut_addr(k) + SHORTCUT_SIZE])
                    if events is not None:
                        conf.shortcuts[k] = events
                elif t == KEY_MACRO:
                    macro = Macro.decode(image[macro_addr(k):macro_addr(k) + MACRO_SIZE])
                    if macro is not None:
                        conf.macros[k] = macro
        return conf

    def describe_button(self, k):
        t, p1, p2 = self.buttons[k]
        if t == KEY_SHORTCUT and k in self.shortcuts:
            events = self.shortcuts[k]
            return "shortcut %s" % (format_combo(events) or format_steps(events))
        if t == KEY_MACRO and k in self.macros:
            return "macro %r, %s: %s" % (self.macros[k].name, describe_repeat(p2),
                                        format_steps(self.macros[k].steps))
        return describe_key(t, p1, p2)

    def set_shortcut(self, k, events):
        self.buttons[k] = (KEY_SHORTCUT, 0, 0)
        self.shortcuts[k] = list(events)

    def set_macro(self, k, macro, repeat=1):
        self.buttons[k] = (KEY_MACRO, k, repeat)
        self.macros[k] = macro

    def validate(self):
        """List of problems that would make the configuration invalid."""
        errors = []
        if self.report_rate not in RATE_HZ:
            errors.append("unknown report rate code 0x%02x" % self.report_rate)
        if not 1 <= self.dpi_count <= MAX_DPI_LEVELS:
            errors.append("DPI level count must be 1..%d" % MAX_DPI_LEVELS)
        if not 0 <= self.dpi_active < self.dpi_count:
            errors.append("active DPI level must be one of the enabled levels")
        for i, level in enumerate(self.levels):
            for dpi in (level.x, level.y):
                if not DPI_MIN <= dpi <= DPI_MAX or dpi % DPI_STEP:
                    errors.append("level %d: DPI must be a multiple of %d in %d..%d"
                                  % (i + 1, DPI_STEP, DPI_MIN, DPI_MAX))
            if dpi_band(level.x) != dpi_band(level.y):
                errors.append("level %d: X and Y must be in the same 12800-DPI band" % (i + 1))
        for name, value in self.options.items():
            if not 0 <= value <= 255:
                errors.append("%s must be 0..255" % name)
        for k, (t, p1, p2) in enumerate(self.buttons):
            where = "button %d: " % (k + 1)
            if not all(0 <= v <= 255 for v in (t, p1, p2)):
                errors.append(where + "values must be 0..255 (dpi-lock: 50..12800 DPI)")
            elif t == KEY_FIRE and (p1 < FIRE_INTERVAL_MIN or p2 > FIRE_TIMES_MAX):
                errors.append(where + "fire interval must be %d..255 ms and shots 0..%d"
                              % (FIRE_INTERVAL_MIN, FIRE_TIMES_MAX))
            elif t == KEY_SHORTCUT and k in self.shortcuts:
                n = len(self.shortcuts[k])
                if not 1 <= n <= 2 * SHORTCUT_KEYS_MAX:
                    errors.append(where + "a shortcut has 1..%d events" % (2 * SHORTCUT_KEYS_MAX))
            elif t == KEY_MACRO and k in self.macros:
                if p1 != k:
                    errors.append(where + "a macro must use its own block (p1=%d)" % k)
                if not 1 <= p2 <= 255:
                    errors.append(where + "macro repeat must be 1..255")
                errors += [where + e for e in self.macros[k].errors()]
        return errors

    def encode(self, base):
        """Return `base` (an image as given to decode()) with this configuration applied."""
        img = bytearray(base)

        def pair(off, value):
            img[off:off + 2] = with_check([value])

        pair(OFF_REPORT_RATE, self.report_rate)
        pair(OFF_DPI_COUNT, self.dpi_count)
        pair(OFF_DPI_ACTIVE, self.dpi_active)
        for i, level in enumerate(self.levels):
            lo_x, ex = dpi_encode(level.x)
            lo_y, _ = dpi_encode(level.y)
            img[OFF_DPI + 4 * i:OFF_DPI + 4 * i + 4] = with_check([lo_x, lo_y, ex])
            img[OFF_DPI_COLOR + 4 * i:OFF_DPI_COLOR + 4 * i + 4] = with_check(level.color)
        for name, value in self.options.items():
            pair(OPTIONS[name], value)
        for k, value in enumerate(self.buttons):
            img[OFF_KEYS + 4 * k:OFF_KEYS + 4 * k + 4] = with_check(value)
        if len(img) >= FLASH_SIZE:
            for k, events in self.shortcuts.items():
                if self.buttons[k][0] == KEY_SHORTCUT:
                    data = encode_shortcut(events)
                    img[shortcut_addr(k):shortcut_addr(k) + len(data)] = data
            for k, macro in self.macros.items():
                if self.buttons[k][0] == KEY_MACRO:
                    data = macro.encode()
                    img[macro_addr(k):macro_addr(k) + len(data)] = data
        return bytes(img)


# Writable fields as (address, length)
FIELDS = ([(OFF_REPORT_RATE, 2), (OFF_DPI_ACTIVE, 2), (OFF_DPI_COUNT, 2)]
          + [(OFF_DPI + 4 * i, 4) for i in range(MAX_DPI_LEVELS)]
          + [(OFF_DPI_COLOR + 4 * i, 4) for i in range(MAX_DPI_LEVELS)]
          + [(OFF_KEYS + 4 * k, 4) for k in range(KEY_COUNT)]
          + [(off, 2) for off in OPTIONS.values()])


def block_fields(image):
    """(address, used length) of the valid shortcut and macro blocks of the buttons using them."""
    fields = []
    if len(image) < FLASH_SIZE:
        return fields
    for k in range(KEY_COUNT):
        t = image[OFF_KEYS + 4 * k]
        if t == KEY_SHORTCUT:
            a = shortcut_addr(k)
            if decode_shortcut(image[a:a + SHORTCUT_SIZE]) is not None:
                fields.append((a, 2 + 3 * image[a]))
        elif t == KEY_MACRO:
            a = macro_addr(k)
            if Macro.decode(image[a:a + MACRO_SIZE]) is not None:
                fields.append((a, 0x21 + 5 * image[a + 0x1F]))
    return fields


def changed_fields(old, new):
    """Fields that differ, ordered so the active level never points past the enabled levels
    and a button is only switched to a shortcut or macro once its block is written."""
    blocks = [(a, n) for a, n in block_fields(new) if old[a:a + n] != new[a:a + n]]
    fields = [(a, n) for a, n in FIELDS if old[a:a + n] != new[a:a + n]]
    if new[OFF_DPI_ACTIVE] >= old[OFF_DPI_COUNT]:
        # Growing to reach the new active level: write the count first
        fields.sort(key=lambda f: f[0] != OFF_DPI_COUNT)
    return blocks + fields


def restore_writes(current, image):
    """(address, data) writes that turn the `current` flash into the full `image`.

    The mouse only stores a macro block written from its start as a valid macro, or as the
    empty form the original app uses to delete one: other writes there are acknowledged and
    dropped. So macro blocks are compared by the macro they hold, not byte by byte (an erased
    block in a backup and a deleted macro are the same), and rewritten whole."""
    writes = []
    for a in range(0, OFF_MACROS, CHUNK):
        end = min(a + CHUNK, OFF_MACROS)
        if current[a:end] != image[a:end]:
            writes.append((a, image[a:end]))
    for k in range(KEY_COUNT):
        a = macro_addr(k)
        macro = Macro.decode(image[a:a + MACRO_SIZE])
        if Macro.decode(current[a:a + MACRO_SIZE]) != macro:
            writes.append((a, macro.encode() if macro else EMPTY_MACRO))
    return writes


def read_image(m, progress=None):
    """FLASH_SIZE image with the settings and the blocks the buttons use; other bytes are 0xFF
    (unknown), so encoding over it only writes what the buttons need."""
    img = bytearray(b"\xff" * FLASH_SIZE)
    img[:CONFIG_SIZE] = m.read_flash(0, CONFIG_SIZE)
    for k in range(KEY_COUNT):
        t = img[OFF_KEYS + 4 * k]
        if t == KEY_SHORTCUT:
            a = shortcut_addr(k)
            img[a:a + 20] = m.read_flash(a, 20)  # 1 + 3 x 6 events + chk
        elif t == KEY_MACRO:
            a = macro_addr(k)
            img[a:a + 0x20] = m.read_flash(a, 0x20)
            n = min(img[a + 0x1F], MACRO_STEPS_MAX)
            img[a + 0x20:a + 0x21 + 5 * n] = m.read_flash(a + 0x20, 5 * n + 1)
        if progress:
            progress(k + 1, KEY_COUNT)
    return bytes(img)


def bad_checks(image):
    """Offsets of single-byte settings whose check byte does not match."""
    offsets = [OFF_REPORT_RATE, OFF_DPI_COUNT, OFF_DPI_ACTIVE, *OPTIONS.values()]
    return [o for o in offsets if image[o + 1] != checksum(image[o:o + 1])]
