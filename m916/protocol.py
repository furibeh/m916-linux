# SPDX-License-Identifier: GPL-3.0-or-later
"""HID transport and flash access for the Redragon M916-PRO-1K.

Packet (17 bytes): 08 CMD 00 ADDR_H ADDR_L LEN DATA[10] CHK, CHK = 0x55 - sum(bytes 0..15).
See docs/PROTOCOL.md.
"""
import contextlib
import glob
import os
import select
import time

from .i18n import _

VID = 0x3554
PIDS = {0xF55E: "wired", 0xF55F: "dongle", 0xF5D5: "dongle"}

REPORT_ID = 0x08
FLASH_SIZE = 0x1B00  # DataParser.MAX_FLASH_SIZE
CHUNK = 10           # DataParser.USB_PACKET_DATA_SIZE
SLOTS = 4            # onboard profiles, each with its own flash image at the same addresses

CMD_DEVICE_ONLINE = 0x03
CMD_BATTERY = 0x04
CMD_WRITE_FLASH = 0x07
CMD_READ_FLASH = 0x08
CMD_STATUS_CHANGED = 0x0A
CMD_GET_CONFIG = 0x0E  # active slot (onboard profile)
CMD_SET_CONFIG = 0x0F
CMD_READ_VERSION = 0x12
CMD_SET_LONG_RANGE = 0x16  # kept by the mouse outside the slot images
CMD_GET_LONG_RANGE = 0x17

# Bits of the StatusChanged notification (DriverLib.DeviceStatusChanged)
STATUS_DPI = 0x01
STATUS_REPORT_RATE = 0x02
STATUS_CONFIG = 0x04
STATUS_DPI_LED = 0x08
STATUS_LOGO_LED = 0x10
STATUS_LED_BAR = 0x20
STATUS_BATTERY = 0x40
STATUS_NAMES = ["dpi", "report-rate", "config", "dpi-led", "logo-led", "led-bar", "battery"]


class MouseAsleep(Exception):
    """No answer from the mouse: asleep, off or out of range (the receiver still answers 0x03)."""


class ProtocolError(Exception):
    """The mouse answered, but did not acknowledge or store what was written."""


def checksum(data):
    return (0x55 - sum(data)) & 0xFF


def with_check(data):
    data = bytes(data)
    return data + bytes([checksum(data)])


def _hid_id(node):
    """(vendor, product) of a /sys/class/hidraw node, or None."""
    try:
        with open(os.path.join(node, "device/uevent")) as f:
            uevent = f.read()
    except OSError:
        return None
    hid_id = next((l.split("=")[1] for l in uevent.splitlines() if l.startswith("HID_ID=")), "")
    parts = hid_id.split(":")
    return (int(parts[1], 16), int(parts[2], 16)) if len(parts) == 3 else None


def device_link(path):
    """"wired" or "dongle" for a /dev/hidrawN node of the mouse, else None."""
    ids = _hid_id(os.path.join("/sys/class/hidraw", os.path.basename(path)))
    return PIDS.get(ids[1]) if ids and ids[0] == VID else None


def describe_link(link):
    """User-facing name of a connection ("wired"/"dongle")."""
    return {"wired": _("cable"), "dongle": _("receiver")}.get(link, _("unknown"))


def find_devices():
    """hidraw nodes of supported receivers/mice that expose the vendor collection (report ID 8),
    the cable first: while it is plugged in the mouse stops answering through the receiver."""
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ids = _hid_id(node)
        try:
            with open(os.path.join(node, "device/report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if ids and ids[0] == VID and ids[1] in PIDS and b"\x85\x08" in desc:  # Report ID (8)
            found.append("/dev/" + os.path.basename(node))
    return sorted(found, key=lambda path: device_link(path) != "wired")


def device_openers():
    """(path, opener) for each find_devices() node, the cable first."""
    return [(path, lambda path=path: Mouse(path, link=device_link(path)))
            for path in find_devices()]


def open_mouse(openers=None, fallback=True):
    """Open the connection the mouse answers on: the first candidate whose 0x03 says it is
    linked (the cable comes first), else, with `fallback`, the receiver so the caller can wait
    for the mouse to wake up. `openers` are callables returning a Mouse (default: from
    device_openers()). Returns None when there is nothing to use; raises PermissionError if
    nothing could be opened for lack of permission."""
    if openers is None:
        openers = [opener for _path, opener in device_openers()]
    opened, denied = [], None
    try:
        for opener in openers:
            try:
                m = opener()
            except PermissionError as e:
                denied = denied or e
                continue
            except OSError:
                continue
            try:
                if m.online(retries=1, timeout=0.5):
                    return m
                opened.append(m)
            except (OSError, MouseAsleep):
                m.close()
        if not opened:
            if denied:
                raise denied
            return None
        if not fallback:
            return None
        fallback = next((m for m in opened if m.link == "dongle"), opened[0])
        opened.remove(fallback)
        return fallback
    finally:
        for m in opened:
            m.close()


class Mouse:
    def __init__(self, path, fd=None, link=None):
        self.path = path
        self.link = link  # "wired", "dongle" or None (unknown)
        self.fd = os.open(path, os.O_RDWR) if fd is None else fd
        self.notifications = []
        self._last_write = 0.0

    def close(self):
        os.close(self.fd)

    def read_report(self, timeout):
        """One raw input report, or None after `timeout` seconds (None blocks)."""
        r, _w, _x = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        return os.read(self.fd, 64)

    def poll_notifications(self):
        """Collect pending StatusChanged notifications without blocking."""
        while True:
            resp = self.read_report(0)
            if resp is None:
                break
            if len(resp) >= 17 and resp[0] == REPORT_ID and resp[1] == CMD_STATUS_CHANGED:
                self.notifications.append(resp[6])
        flags, self.notifications = self.notifications, []
        return flags

    def transact(self, cmd, addr=0, data=b"", length=None, retries=5, timeout=1.0):
        data = bytes(data)[:CHUNK]
        pkt = bytes([REPORT_ID, cmd, 0, (addr >> 8) & 0xFF, addr & 0xFF,
                     len(data) if length is None else length]) + data.ljust(CHUNK, b"\0")
        pkt += bytes([checksum(pkt)])
        for _attempt in range(retries):
            # The original driver keeps at least 8 ms between writes
            wait = 0.008 - (time.monotonic() - self._last_write)
            if wait > 0:
                time.sleep(wait)
            os.write(self.fd, pkt)
            self._last_write = time.monotonic()
            deadline = time.monotonic() + timeout
            while True:
                resp = self.read_report(max(0.0, deadline - time.monotonic()))
                if resp is None:
                    break
                if len(resp) < 17 or resp[0] != REPORT_ID or resp[16] != checksum(resp[:16]):
                    continue
                resp = resp[:17]
                if resp[1] == cmd:
                    return pkt, resp
                if resp[1] == CMD_STATUS_CHANGED:
                    self.notifications.append(resp[6])
        raise MouseAsleep(_("no response to command 0x%(cmd)02x at 0x%(addr)04x (mouse asleep or "
                            "off? move it and try again)") % {"cmd": cmd, "addr": addr})

    def read_flash(self, addr, size, progress=None):
        """Read `size` bytes; `progress(done, total)` is called after each packet."""
        out = bytearray()
        while len(out) < size:
            n = min(CHUNK, size - len(out))
            _pkt, resp = self.transact(CMD_READ_FLASH, addr + len(out), length=n)
            out += resp[6:6 + n]
            if progress:
                progress(len(out), size)
        return bytes(out)

    def write_flash(self, addr, data):
        for i in range(0, len(data), CHUNK):
            pkt, resp = self.transact(CMD_WRITE_FLASH, addr + i, data[i:i + CHUNK])
            if resp != pkt:
                raise ProtocolError(_("write at 0x%(addr)04x not acknowledged (got %(reply)s)")
                                    % {"addr": addr + i, "reply": resp.hex(" ")})

    def write_verified(self, addr, data):
        self.write_flash(addr, data)
        if self.read_flash(addr, len(data)) != bytes(data):
            raise ProtocolError(_("verification failed at 0x%04x") % addr)

    def write_fields(self, fields, image):
        """Write the (addr, length) ranges of `image`, verifying each one."""
        for addr, length in fields:
            self.write_verified(addr, image[addr:addr + length])

    def version(self):
        _pkt, r = self.transact(CMD_READ_VERSION)
        return r[6:6 + r[5]]

    def online(self, retries=5, timeout=1.0):
        """Answered by the receiver itself: True when the mouse is awake and linked (always
        True on the cable)."""
        _pkt, r = self.transact(CMD_DEVICE_ONLINE, retries=retries, timeout=timeout)
        return r[6] == 1

    def wait_online(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not self.online():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.2)
        return True

    def slot(self, retries=5, timeout=1.0):
        """Active slot, 0-based (answered by the mouse, not by the receiver)."""
        _pkt, r = self.transact(CMD_GET_CONFIG, retries=retries, timeout=timeout)
        return r[6]

    def set_slot(self, slot):
        """Select the active slot (0-based): flash reads and writes go to its image at once."""
        if not 0 <= slot < SLOTS:
            raise ValueError(_("slot must be 1..%d") % SLOTS)
        pkt, resp = self.transact(CMD_SET_CONFIG, data=[slot])
        if resp != pkt:
            raise ProtocolError(_("slot change not acknowledged (got %s)") % resp.hex(" "))
        if self.slot() != slot:
            raise ProtocolError(_("the mouse did not switch to slot %d") % (slot + 1))

    def long_range(self):
        """True if long range mode is on. Answered by the mouse, not by the receiver; the
        setting is not in the flash image of any slot."""
        _pkt, r = self.transact(CMD_GET_LONG_RANGE)
        return bool(r[6])

    def set_long_range(self, on):
        """Turn long range mode on or off and verify it by reading it back."""
        on = bool(on)
        pkt, resp = self.transact(CMD_SET_LONG_RANGE, data=bytes([on]) + bytes(CHUNK - 1))
        if resp != pkt:
            raise ProtocolError(_("long range change not acknowledged (got %s)") % resp.hex(" "))
        if self.long_range() != on:
            raise ProtocolError(_("the mouse did not store the long range mode"))

    def battery(self):
        """(level %, charging, millivolts)"""
        _pkt, r = self.transact(CMD_BATTERY)
        return r[6], bool(r[7]), r[8] << 8 | r[9]


@contextlib.contextmanager
def on_slot(m, slot):
    """Run the block on `slot` (0-based) and go back to the active slot afterwards, also when
    the block fails. With None, or the slot already active, it just runs the block."""
    if slot is None:
        yield
        return
    original = m.slot()
    if original == slot:
        yield
        return
    m.set_slot(slot)
    error = None
    try:
        yield
    except BaseException as e:
        error = e
        raise
    finally:
        try:
            m.set_slot(original)
        except (MouseAsleep, ProtocolError, OSError) as e:
            msg = _("could not go back to slot %(original)d (%(error)s); the mouse stayed on "
                    "slot %(slot)d") % {"original": original + 1, "error": e, "slot": slot + 1}
            if error is not None:
                msg = "%s; %s" % (error, msg)
            raise ProtocolError(msg) from (error or e)
