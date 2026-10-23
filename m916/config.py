# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings stored in the first 0xC0 bytes of the flash (ProtocolDataParser layout)."""
from dataclasses import dataclass, field

from .protocol import checksum, with_check

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

# Single-byte settings stored as [value, 0x55 - value] (MouseConfigParser)
OPTIONS = {
    "lod": 0x0A,
    "debounce": 0xA9,
    "motion-sync": 0xAB,
    "led-off-time": 0xAD,
    "linear-correction": 0xAF,
    "ripple-control": 0xB1,
    "move-off-led": 0xB3,
    "sleep-custom": 0xB5,
    "sleep-time": 0xB7,
    "power-saving": 0xB9,
}
BOOL_OPTIONS = {"motion-sync", "linear-correction", "ripple-control", "move-off-led",
                "sleep-custom", "power-saving"}

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
    "report-rate": (0x07, 0, 0),
}
KEY_FIRE = 0x04
KEY_CLASS_NAMES = {0: "disabled", 1: "mouse", 2: "dpi", 3: "h-scroll", 4: "fire", 5: "shortcut",
                   6: "macro", 7: "report-rate", 8: "decorative-lamp", 9: "config", 10: "dpi-lock"}


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


def describe_key(t, p1, p2):
    for name, val in BUTTON_ACTIONS.items():
        if val == (t, p1, p2):
            return name
    if t == KEY_FIRE:
        return "fire (p1=%d, p2=%d)" % (p1, p2)
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

    @classmethod
    def decode(cls, image):
        levels = []
        for i in range(MAX_DPI_LEVELS):
            x, y, ex = image[OFF_DPI + 4 * i:OFF_DPI + 4 * i + 3]
            levels.append(DpiLevel(dpi_decode(x, ex), dpi_decode(y, ex),
                                   tuple(image[OFF_DPI_COLOR + 4 * i:OFF_DPI_COLOR + 4 * i + 3])))
        return cls(
            report_rate=image[OFF_REPORT_RATE],
            dpi_count=image[OFF_DPI_COUNT],
            dpi_active=image[OFF_DPI_ACTIVE],
            levels=levels,
            options={name: image[off] for name, off in OPTIONS.items()},
            buttons=[tuple(image[OFF_KEYS + 4 * k:OFF_KEYS + 4 * k + 3]) for k in range(KEY_COUNT)],
        )

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
        return errors

    def encode(self, base):
        """Return `base` (a CONFIG_SIZE image) with this configuration applied."""
        img = bytearray(base[:CONFIG_SIZE])

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
        return bytes(img)


# Writable fields as (address, length)
FIELDS = ([(OFF_REPORT_RATE, 2), (OFF_DPI_ACTIVE, 2), (OFF_DPI_COUNT, 2)]
          + [(OFF_DPI + 4 * i, 4) for i in range(MAX_DPI_LEVELS)]
          + [(OFF_DPI_COLOR + 4 * i, 4) for i in range(MAX_DPI_LEVELS)]
          + [(OFF_KEYS + 4 * k, 4) for k in range(KEY_COUNT)]
          + [(off, 2) for off in OPTIONS.values()])


def changed_fields(old, new):
    """Fields that differ, ordered so the active level never points past the enabled levels."""
    fields = [(a, n) for a, n in FIELDS if old[a:a + n] != new[a:a + n]]
    if new[OFF_DPI_ACTIVE] >= old[OFF_DPI_COUNT]:
        # Growing to reach the new active level: write the count first
        fields.sort(key=lambda f: f[0] != OFF_DPI_COUNT)
    return fields


def bad_checks(image):
    """Offsets of single-byte settings whose check byte does not match."""
    offsets = [OFF_REPORT_RATE, OFF_DPI_COUNT, OFF_DPI_ACTIVE, *OPTIONS.values()]
    return [o for o in offsets if image[o + 1] != checksum(image[o:o + 1])]
