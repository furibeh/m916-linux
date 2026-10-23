# SPDX-License-Identifier: GPL-3.0-or-later
"""HID transport and flash access for the Redragon M916-PRO-1K.

Packet (17 bytes): 08 CMD 00 ADDR_H ADDR_L LEN DATA[10] CHK, CHK = 0x55 - sum(bytes 0..15).
See docs/PROTOCOL.md.
"""
import glob
import os
import select
import time

VID = 0x3554
PIDS = {0xF55E: "wired", 0xF55F: "dongle", 0xF5D5: "dongle"}

REPORT_ID = 0x08
FLASH_SIZE = 0x1B00  # DataParser.MAX_FLASH_SIZE
CHUNK = 10           # DataParser.USB_PACKET_DATA_SIZE

CMD_DEVICE_ONLINE = 0x03
CMD_BATTERY = 0x04
CMD_WRITE_FLASH = 0x07
CMD_READ_FLASH = 0x08
CMD_STATUS_CHANGED = 0x0A
CMD_READ_VERSION = 0x12

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


def find_devices():
    """hidraw nodes of supported receivers/mice that expose the vendor collection (report ID 8)."""
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(node, "device/uevent")) as f:
                uevent = f.read()
            with open(os.path.join(node, "device/report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        hid_id = next((l.split("=")[1] for l in uevent.splitlines() if l.startswith("HID_ID=")), "")
        parts = hid_id.split(":")
        if len(parts) == 3 and int(parts[1], 16) == VID and int(parts[2], 16) in PIDS \
                and b"\x85\x08" in desc:  # Report ID (8)
            found.append("/dev/" + os.path.basename(node))
    return found


class Mouse:
    def __init__(self, path, fd=None):
        self.path = path
        self.fd = os.open(path, os.O_RDWR) if fd is None else fd
        self.notifications = []
        self._last_write = 0.0

    def close(self):
        os.close(self.fd)

    def read_report(self, timeout):
        """One raw input report, or None after `timeout` seconds (None blocks)."""
        r, _, _ = select.select([self.fd], [], [], timeout)
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
        for _ in range(retries):
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
        raise MouseAsleep("no response to command 0x%02x at 0x%04x (mouse asleep or off? "
                          "move it and try again)" % (cmd, addr))

    def read_flash(self, addr, size, progress=None):
        """Read `size` bytes; `progress(done, total)` is called after each packet."""
        out = bytearray()
        while len(out) < size:
            n = min(CHUNK, size - len(out))
            _, resp = self.transact(CMD_READ_FLASH, addr + len(out), length=n)
            out += resp[6:6 + n]
            if progress:
                progress(len(out), size)
        return bytes(out)

    def write_flash(self, addr, data):
        for i in range(0, len(data), CHUNK):
            pkt, resp = self.transact(CMD_WRITE_FLASH, addr + i, data[i:i + CHUNK])
            if resp != pkt:
                raise ProtocolError("write at 0x%04x not acknowledged (got %s)" % (addr + i, resp.hex(" ")))

    def write_verified(self, addr, data):
        self.write_flash(addr, data)
        if self.read_flash(addr, len(data)) != bytes(data):
            raise ProtocolError("verification failed at 0x%04x" % addr)

    def write_fields(self, fields, image):
        """Write the (addr, length) ranges of `image`, verifying each one."""
        for addr, length in fields:
            self.write_verified(addr, image[addr:addr + length])

    def restore_chunks(self, current, image):
        """10-byte chunks where a full flash image differs from the current flash."""
        return [a for a in range(0, FLASH_SIZE, CHUNK) if current[a:a + CHUNK] != image[a:a + CHUNK]]

    def version(self):
        _, r = self.transact(CMD_READ_VERSION)
        return r[6:6 + r[5]]

    def online(self):
        """Answered by the receiver itself: True when the mouse is awake and linked."""
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
        """(level %, charging, millivolts)"""
        _, r = self.transact(CMD_BATTERY)
        return r[6], bool(r[7]), r[8] << 8 | r[9]
