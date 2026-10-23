#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux configuration tool for the Redragon M916-PRO-1K mouse.

Protocol reconstructed from hidusb.dll / "Mouse Drive Beta.exe" and verified on hardware
(receiver 3554:F5D5). Talks to the vendor HID collection (report ID 8) through /dev/hidraw.

Packet (17 bytes): 08 CMD 00 ADDR_H ADDR_L LEN DATA[10] CHK, CHK = 0x55 - sum(bytes 0..15).
Settings live in a 0x1B00-byte flash; every value is followed by a check byte.
"""
import argparse
import glob
import os
import select
import sys
import time

VID = 0x3554
PIDS = {0xF55E: "wired", 0xF55F: "dongle", 0xF5D5: "dongle"}

REPORT_ID = 0x08
FLASH_SIZE = 0x1B00

CMD_DEVICE_ONLINE = 0x03
CMD_BATTERY = 0x04
CMD_WRITE_FLASH = 0x07
CMD_READ_FLASH = 0x08
CMD_STATUS_CHANGED = 0x0A
CMD_READ_VERSION = 0x12

# Flash layout (ProtocolDataParser)
OFF_REPORT_RATE = 0x00
OFF_DPI_COUNT = 0x02
OFF_DPI_ACTIVE = 0x04
OFF_DPI = 0x0C        # 8 x [x, y, ex, chk]
OFF_DPI_COLOR = 0x2C  # 8 x [r, g, b, chk]
OFF_DPI_LED = 0x4C    # pairs: mode, brightness, speed, enable
OFF_KEYS = 0x60       # 16 x [type, p1, p2, chk]
OFF_LEDBAR = 0xA0     # [mode, r, g, b, speed, brightness, chk] + pair enable
OFF_SHORTCUT = 0x100  # 16 x 0x20
OFF_MACRO = 0x300     # 16 x 0x180
CONFIG_SIZE = 0xC0
MAX_DPI_LEVELS = 8
KEY_COUNT = 16

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

RATES = {1000: 0x01, 500: 0x02, 250: 0x04, 125: 0x08, 2000: 0x10, 4000: 0x20}

STATUS_BITS = ["dpi", "report-rate", "config", "dpi-led", "logo-led", "led-bar", "battery"]

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
KEY_CLASS_NAMES = {0: "disabled", 1: "mouse", 2: "dpi", 3: "h-scroll", 4: "fire", 5: "shortcut",
                   6: "macro", 7: "report-rate", 8: "decorative-lamp", 9: "config", 10: "dpi-lock"}


def checksum(data):
    return (0x55 - sum(data)) & 0xFF


def with_check(data):
    data = bytes(data)
    return data + bytes([checksum(data)])


def dpi_decode(lo, ex):
    """PAW3395 (DPIControl.ValueToDPI): value = dpi/50 - 1; high bits in DPIex bits 2-3/6-7."""
    return (((ex >> 2) & 0x3) << 8 | lo) * 50 + 50


def dpi_encode(dpi):
    value = dpi // 50 - 1
    hi = value >> 8
    return value & 0xFF, (hi << 2) | (hi << 6)


def describe_key(t, p1, p2):
    for name, val in BUTTON_ACTIONS.items():
        if val == (t, p1, p2):
            return name
    if t == 4:
        return "fire (p1=%d, p2=%d)" % (p1, p2)
    return "%s (p1=0x%02x, p2=0x%02x)" % (KEY_CLASS_NAMES.get(t, "type 0x%02x" % t), p1, p2)


class Mouse:
    def __init__(self, path, fd=None):
        self.path = path
        self.fd = os.open(path, os.O_RDWR) if fd is None else fd
        self.notifications = []
        self._last_write = 0.0

    def close(self):
        os.close(self.fd)

    @staticmethod
    def find():
        found = []
        for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            try:
                with open(os.path.join(node, "device/uevent")) as f:
                    ue = f.read()
                with open(os.path.join(node, "device/report_descriptor"), "rb") as f:
                    desc = f.read()
            except OSError:
                continue
            hid_id = next((l.split("=")[1] for l in ue.splitlines() if l.startswith("HID_ID=")), "")
            parts = hid_id.split(":")
            if len(parts) == 3 and int(parts[1], 16) == VID and int(parts[2], 16) in PIDS \
                    and b"\x85\x08" in desc:  # Report ID (8)
                found.append("/dev/" + os.path.basename(node))
        return found

    def _read_report(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        return os.read(self.fd, 64)

    def _handle_unsolicited(self, resp):
        if resp[1] == CMD_STATUS_CHANGED:
            self.notifications.append(resp[6])

    def transact(self, cmd, addr=0, data=b"", length=None, retries=5, timeout=1.0):
        data = bytes(data)[:10]
        pkt = bytes([REPORT_ID, cmd, 0, (addr >> 8) & 0xFF, addr & 0xFF,
                     len(data) if length is None else length]) + data.ljust(10, b"\0")
        pkt += bytes([checksum(pkt)])
        for _ in range(retries):
            # The original driver keeps at least 8 ms between writes
            wait = 0.008 - (time.monotonic() - self._last_write)
            if wait > 0:
                time.sleep(wait)
            os.write(self.fd, pkt)
            self._last_write = time.monotonic()
            deadline = time.monotonic() + timeout
            while True:
                resp = self._read_report(max(0.0, deadline - time.monotonic()))
                if resp is None:
                    break
                if len(resp) < 17 or resp[0] != REPORT_ID or resp[16] != checksum(resp[:16]):
                    continue
                resp = resp[:17]
                if resp[1] == cmd:
                    return pkt, resp
                self._handle_unsolicited(resp)
        raise TimeoutError("no response to command 0x%02x at 0x%04x (mouse asleep or off? "
                           "move it and try again)" % (cmd, addr))

    def read_flash(self, addr, size, progress=False):
        out = bytearray()
        total = size
        while size > 0:
            n = min(10, size)
            _, resp = self.transact(CMD_READ_FLASH, addr, length=n)
            out += resp[6:6 + n]
            addr += n
            size -= n
            if progress and (len(out) % 500 < 10 or size == 0):
                print("\r  read %d/%d bytes" % (len(out), total), end="", file=sys.stderr)
        if progress:
            print(file=sys.stderr)
        return bytes(out)

    def write_flash(self, addr, data):
        for i in range(0, len(data), 10):
            pkt, resp = self.transact(CMD_WRITE_FLASH, addr + i, data[i:i + 10])
            if resp != pkt:
                raise IOError("write at 0x%04x not acknowledged (got %s)" % (addr + i, resp.hex(" ")))

    def write_verified(self, addr, data):
        self.write_flash(addr, data)
        if self.read_flash(addr, len(data)) != bytes(data):
            raise IOError("verification failed at 0x%04x" % addr)

    def write_option(self, offset, value):
        self.write_verified(offset, with_check([value]))

    def version(self):
        _, r = self.transact(CMD_READ_VERSION)
        return r[6:6 + r[5]]

    def online(self):
        """Answered by the receiver itself: 1 when the mouse is awake and linked."""
        _, r = self.transact(CMD_DEVICE_ONLINE)
        return r[6] == 1

    def wait_online(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not self.online():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.2)
        return True

    def battery(self):
        _, r = self.transact(CMD_BATTERY)
        return r[6], bool(r[7]), r[8] << 8 | r[9]


def cmd_info(m, args):
    print("device   :", m.path)
    print("firmware :", m.version().hex(" "))
    level, charging, mv = m.battery()
    print("battery  : %d%%%s, %d mV" % (level, " (charging)" if charging else "", mv))
    cfg = m.read_flash(0, CONFIG_SIZE)
    rate = {v: k for k, v in RATES.items()}.get(cfg[OFF_REPORT_RATE])
    print("report rate  : %s Hz" % (rate or "raw 0x%02x" % cfg[OFF_REPORT_RATE]))
    count, active = cfg[OFF_DPI_COUNT], cfg[OFF_DPI_ACTIVE]
    print("DPI levels   : %d (active: %d)" % (count, active + 1))
    for i in range(MAX_DPI_LEVELS):
        x, y, ex = cfg[OFF_DPI + 4 * i:OFF_DPI + 4 * i + 3]
        r, g, b = cfg[OFF_DPI_COLOR + 4 * i:OFF_DPI_COLOR + 4 * i + 3]
        mark = "*" if i == active else (" " if i < count else "-")
        print("  %s %d: %5d DPI%s  colour #%02x%02x%02x"
              % (mark, i + 1, dpi_decode(x, ex),
                 "" if x == y else " (Y %d)" % dpi_decode(y, ex), r, g, b))
    print("options:")
    for name, off in OPTIONS.items():
        print("  %-18s %d" % (name, cfg[off]))
    print("buttons:")
    for k in range(KEY_COUNT):
        t, p1, p2 = cfg[OFF_KEYS + 4 * k:OFF_KEYS + 4 * k + 3]
        print("  %2d: %s" % (k + 1, describe_key(t, p1, p2)))
    bad = [hex(o) for o in [OFF_REPORT_RATE, OFF_DPI_COUNT, OFF_DPI_ACTIVE, *OPTIONS.values()]
           if cfg[o + 1] != checksum(cfg[o:o + 1])]
    if bad:
        print("warning: bad check bytes at", ", ".join(bad))


def cmd_dump(m, args):
    data = m.read_flash(0, FLASH_SIZE, progress=True)
    with open(args.file, "wb") as f:
        f.write(data)
    print("saved %d bytes to %s" % (len(data), args.file))


def cmd_restore(m, args):
    with open(args.file, "rb") as f:
        image = f.read()
    if len(image) != FLASH_SIZE:
        sys.exit("%s is %d bytes, expected %d" % (args.file, len(image), FLASH_SIZE))
    print("reading current flash to compare...")
    current = m.read_flash(0, FLASH_SIZE, progress=True)
    chunks = [a for a in range(0, FLASH_SIZE, 10) if current[a:a + 10] != image[a:a + 10]]
    if not chunks:
        print("flash already matches the image, nothing to do")
        return
    print("%d chunk(s) differ: %s" % (len(chunks), ", ".join("0x%04x" % a for a in chunks[:20])
                                     + (" ..." if len(chunks) > 20 else "")))
    if not args.yes and input("write them? [y/N] ").strip().lower() != "y":
        return
    for a in chunks:
        m.write_flash(a, image[a:a + 10])
    if m.read_flash(0, FLASH_SIZE, progress=True) != image:
        sys.exit("verification failed after restore")
    print("restored and verified")


def cmd_rate(m, args):
    if args.hz > 1000:
        print("note: %d Hz may need the 4K dongle; this is a 1K model" % args.hz)
    m.write_option(OFF_REPORT_RATE, RATES[args.hz])
    print("report rate set to %d Hz" % args.hz)


def level_index(level):
    if not 1 <= level <= MAX_DPI_LEVELS:
        sys.exit("DPI level must be 1..%d" % MAX_DPI_LEVELS)
    return level - 1


def cmd_dpi(m, args):
    i = level_index(args.level)
    dpi = args.dpi
    if not 50 <= dpi <= 26000 or dpi % 50:
        sys.exit("DPI must be a multiple of 50 between 50 and 26000")
    lo, ex = dpi_encode(dpi)
    y_lo = lo
    if args.y is not None:
        if not 50 <= args.y <= 26000 or args.y % 50 or dpi_encode(args.y)[1] != ex:
            sys.exit("Y DPI must be a multiple of 50 in the same 12800-DPI band as X")
        y_lo = dpi_encode(args.y)[0]
    m.write_verified(OFF_DPI + 4 * i, with_check([lo, y_lo, ex]))
    print("DPI level %d set to %d" % (args.level, dpi))


def cmd_dpi_color(m, args):
    i = level_index(args.level)
    hexstr = args.color.lstrip("#")
    if len(hexstr) != 6:
        sys.exit("colour must be RRGGBB")
    rgb = bytes.fromhex(hexstr)
    m.write_verified(OFF_DPI_COLOR + 4 * i, with_check(rgb))
    print("DPI level %d colour set to #%s" % (args.level, hexstr.lower()))


def cmd_dpi_levels(m, args):
    if not 1 <= args.count <= MAX_DPI_LEVELS:
        sys.exit("count must be 1..%d" % MAX_DPI_LEVELS)
    active = m.read_flash(OFF_DPI_ACTIVE, 1)[0]
    if active >= args.count:
        m.write_option(OFF_DPI_ACTIVE, args.count - 1)
    m.write_option(OFF_DPI_COUNT, args.count)
    print("%d DPI levels enabled" % args.count)


def cmd_dpi_active(m, args):
    i = level_index(args.level)
    if i >= m.read_flash(OFF_DPI_COUNT, 1)[0]:
        sys.exit("level %d is not enabled (see dpi-levels)" % args.level)
    m.write_option(OFF_DPI_ACTIVE, i)
    print("active DPI level set to %d" % args.level)


def cmd_option(m, args):
    if not 0 <= args.value <= 255:
        sys.exit("value must be 0..255")
    m.write_option(OPTIONS[args.name], args.value)
    print("%s set to %d" % (args.name, args.value))


def cmd_button(m, args):
    if not 1 <= args.button <= KEY_COUNT:
        sys.exit("button must be 1..%d" % KEY_COUNT)
    if args.action.startswith("fire:"):
        try:
            _, p1, p2 = args.action.split(":")
            value = (0x04, int(p1), int(p2))
        except ValueError:
            sys.exit("use fire:P1:P2, e.g. fire:10:3")
    else:
        value = BUTTON_ACTIONS[args.action]
    m.write_verified(OFF_KEYS + 4 * (args.button - 1), with_check(value))
    print("button %d set to %s" % (args.button, describe_key(*value)))


def cmd_watch(m, args):
    print("listening for status notifications (Ctrl+C to stop)")
    try:
        while True:
            resp = m._read_report(None)
            if resp and len(resp) >= 17 and resp[0] == REPORT_ID and resp[1] == CMD_STATUS_CHANGED:
                flags = [n for b, n in enumerate(STATUS_BITS) if resp[6] >> b & 1]
                print(time.strftime("%H:%M:%S"), "changed:", ", ".join(flags) or "none",
                      "| raw", resp[6:16].hex(" "))
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-d", "--device", help="hidraw node (default: auto-detect)")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="show device status and configuration").set_defaults(func=cmd_info)
    p = sub.add_parser("dump", help="save the whole flash to a file")
    p.add_argument("file")
    p.set_defaults(func=cmd_dump)
    p = sub.add_parser("restore", help="write back a flash image (only changed chunks)")
    p.add_argument("file")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_restore)
    p = sub.add_parser("rate", help="set the report rate")
    p.add_argument("hz", type=int, choices=sorted(RATES))
    p.set_defaults(func=cmd_rate)
    p = sub.add_parser("dpi", help="set the DPI of a level")
    p.add_argument("level", type=int)
    p.add_argument("dpi", type=int)
    p.add_argument("--y", type=int, help="separate Y DPI")
    p.set_defaults(func=cmd_dpi)
    p = sub.add_parser("dpi-color", help="set the LED colour of a DPI level")
    p.add_argument("level", type=int)
    p.add_argument("color", help="RRGGBB")
    p.set_defaults(func=cmd_dpi_color)
    p = sub.add_parser("dpi-levels", help="number of enabled DPI levels")
    p.add_argument("count", type=int)
    p.set_defaults(func=cmd_dpi_levels)
    p = sub.add_parser("dpi-active", help="select the active DPI level")
    p.add_argument("level", type=int)
    p.set_defaults(func=cmd_dpi_active)
    p = sub.add_parser("option", help="set a raw single-byte option")
    p.add_argument("name", choices=sorted(OPTIONS))
    p.add_argument("value", type=int)
    p.set_defaults(func=cmd_option)
    p = sub.add_parser("button", help="assign a function to a button slot")
    p.add_argument("button", type=int, help="slot 1..16 (see info)")
    p.add_argument("action", help="|".join(BUTTON_ACTIONS) + "|fire:P1:P2")
    p.set_defaults(func=cmd_button)
    sub.add_parser("watch", help="print status notifications").set_defaults(func=cmd_watch)

    args = ap.parse_args()
    if args.command == "button" and args.action not in BUTTON_ACTIONS \
            and not args.action.startswith("fire:"):
        ap.error("unknown action %r" % args.action)

    path = args.device
    if not path:
        devices = Mouse.find()
        if not devices:
            sys.exit("Mouse not found (VID 3554, vendor collection with report ID 8).")
        path = devices[0]
    try:
        m = Mouse(path)
    except PermissionError:
        sys.exit("Permission denied on %s: install 70-redragon-m916.rules or run with sudo." % path)
    try:
        # The receiver answers DeviceOnLine itself; everything else needs the mouse awake
        if args.command != "watch" and not m.online():
            print("mouse is asleep or off: move it or click a button...", file=sys.stderr)
            if not m.wait_online():
                sys.exit("error: the mouse did not wake up")
        args.func(m, args)
    except (TimeoutError, IOError) as e:
        sys.exit("error: %s" % e)
    finally:
        m.close()


if __name__ == "__main__":
    main()
