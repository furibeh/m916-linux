# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated receiver + mouse backed by a flash image, for tests and GUI development."""
import os
import socket
import threading

from .protocol import (CMD_BATTERY, CMD_DEVICE_ONLINE, CMD_GET_CONFIG, CMD_GET_LONG_RANGE,
                       CMD_READ_FLASH, CMD_READ_VERSION, CMD_SET_CONFIG, CMD_SET_LONG_RANGE,
                       CMD_STATUS_CHANGED, CMD_WRITE_FLASH, REPORT_ID, SLOTS, STATUS_CONFIG, STATUS_DPI, STATUS_DPI_LED, Mouse, checksum,
                       with_check)


# Macro area (config.OFF_MACROS, 16 blocks of config.MACRO_SIZE)
MACRO_START, MACRO_SIZE = 0x300, 0x180

CMD_PAIR_STATE, CMD_CLEAR_SETTING = 0x06, 0x09
SETTINGS_SIZE = 0xC0  # what ClearSetting resets (config.CONFIG_SIZE)
# Answered by the receiver itself, also while the mouse sleeps
RECEIVER_COMMANDS = {CMD_DEVICE_ONLINE, CMD_PAIR_STATE}

# Flash offsets the mouse itself rewrites when a button is pressed (config.OFF_*)
OFF_DPI_COUNT, OFF_DPI_ACTIVE, OFF_KEYS, OFF_DPI_LED = 0x02, 0x04, 0x60, 0x52


class FakeMouse:
    """Answers the vendor protocol like the real hardware (verified on 3554:F5D5 and, over
    the cable, 3554:F55E). While the cable is plugged in the mouse talks only through it: the
    receiver still answers its own commands but reports the mouse as not linked."""

    def __init__(self, flash):
        self.flashes = [bytearray(flash) for _s in range(SLOTS)]
        self.factory = bytes(flash[:SETTINGS_SIZE])  # stands in for the firmware defaults
        self.asleep = False
        self.slot = 0         # Get/SetCurrentConfig: which image the flash commands see
        self.long_range = 0   # Get/SetLongRangeMode: one value outside the slot images
        self._macro = None  # (block address, bytes received so far)
        self._host, self._dev = self._endpoint(wired=False)
        self._cable = None  # (host, device) sockets of the cable while it is plugged in

    @property
    def flash(self):
        """Flash image of the active slot."""
        return self.flashes[self.slot]

    @flash.setter
    def flash(self, data):
        self.flashes[self.slot] = bytearray(data)

    def _endpoint(self, wired):
        host, dev = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        threading.Thread(target=self._serve, args=(dev, wired), daemon=True).start()
        return host, dev

    @property
    def cable(self):
        return self._cable is not None

    def plug_cable(self):
        if not self._cable:
            self._cable = self._endpoint(wired=True)

    def unplug_cable(self):
        """Like pulling the cable: writes on its open connections fail with EPIPE."""
        if self._cable:
            for sock in self._cable:
                sock.shutdown(socket.SHUT_RDWR)  # wakes up the thread blocked in recv()
                sock.close()
            self._cable = None

    def mouse(self):
        """Connection through the receiver."""
        return Mouse("fake", fd=os.dup(self._host.fileno()), link="dongle")

    def wired(self):
        """Connection through the cable (plug_cable() first)."""
        return Mouse("fake-cable", fd=os.dup(self._cable[0].fileno()), link="wired")

    def openers(self):
        """(path, opener) like protocol.device_openers(): the cable first."""
        return ([("fake-cable", self.wired)] if self.cable else []) + [("fake", self.mouse)]

    def press(self, k):
        """Press button k (0-based). Like the firmware, "DPI loop" stores the next level
        and "Lights (all)" toggles the DPI LED enable byte in the flash, then notify it;
        "Switch config" (type 9) selects the next slot (cycling through the 4) and notifies it."""
        action = tuple(self.flash[OFF_KEYS + 4 * k:OFF_KEYS + 4 * k + 3])
        if action == (0x02, 0x01, 0x00):
            level = (self.flash[OFF_DPI_ACTIVE] + 1) % self.flash[OFF_DPI_COUNT]
            self.flash[OFF_DPI_ACTIVE:OFF_DPI_ACTIVE + 2] = with_check([level])
            self.notify(STATUS_DPI)
        elif action == (0x08, 0x00, 0x00):
            self.flash[OFF_DPI_LED:OFF_DPI_LED + 2] = with_check([int(not self.flash[OFF_DPI_LED])])
            self.notify(STATUS_DPI_LED)
        elif action[0] == 0x09:
            self.slot = (self.slot + 1) % SLOTS
            self.notify(STATUS_CONFIG)

    def notify(self, flags):
        pkt = bytearray([REPORT_ID, CMD_STATUS_CHANGED, 0, 0, 0, 0x0A, flags]).ljust(16, b"\0")
        dev = self._cable[1] if self._cable else self._dev  # the connection the mouse uses (verified)
        dev.send(bytes(pkt) + bytes([checksum(pkt)]))

    def _serve(self, dev, wired):
        while True:
            try:
                pkt = dev.recv(64)
            except OSError:
                return
            if not pkt:
                return
            if len(pkt) != 17 or pkt[16] != checksum(pkt[:16]):
                continue
            cmd, addr, n = pkt[1], pkt[3] << 8 | pkt[4], pkt[5]
            linked = wired or not (self.asleep or self.cable)
            if not linked and cmd not in RECEIVER_COMMANDS:
                continue  # only the receiver answers while the mouse sleeps or is on the cable
            resp = bytearray(pkt)
            notify = 0
            if cmd == CMD_READ_FLASH:
                resp[6:16] = bytes(self.flash[addr:addr + n]).ljust(10, b"\0")
            elif cmd == CMD_WRITE_FLASH:
                if addr >= MACRO_START:
                    self._write_macro(addr, pkt[6:6 + n])
                else:
                    self.flash[addr:addr + n] = pkt[6:6 + n]
            elif cmd == CMD_READ_VERSION:
                resp[5:8] = b"\x02\x02\x00"
            elif cmd == CMD_DEVICE_ONLINE:
                resp[5:10] = b"\x01" + bytes([linked]) + b"\x25\x81\x06"
            elif cmd == CMD_BATTERY:
                resp[5:10] = b"\x02\x64\x00\x10\x48"
            elif cmd == CMD_PAIR_STATE and wired:
                resp[2] = 1  # the mouse rejects it over the cable (LEN stays 0)
            elif cmd == CMD_PAIR_STATE:
                # 0 = no pairing since plugged in, 1 = pairing (+ seconds left), 2 = paired
                resp[5:8] = b"\x02\x00\x00"
            elif cmd == CMD_GET_CONFIG:
                resp[5:7] = bytes([1, self.slot])
            elif cmd == CMD_SET_CONFIG and pkt[6] < SLOTS:
                self.slot = pkt[6]
                notify = STATUS_CONFIG  # verified through the receiver
            elif cmd == CMD_CLEAR_SETTING:
                # Only the settings of the active slot; shortcut and macro blocks stay
                self.flash[:SETTINGS_SIZE] = self.factory
            elif cmd == CMD_SET_LONG_RANGE:
                self.long_range = pkt[6]  # the reply echoes the packet (verified)
            elif cmd == CMD_GET_LONG_RANGE:
                resp[5:16] = bytes([10, self.long_range]) + bytes(9)
            resp[16] = checksum(resp[:16])
            try:
                dev.send(bytes(resp))
                if notify:
                    self.notify(notify)
            except OSError:
                return

    def _write_macro(self, addr, data):
        """Like the firmware: a macro block is only stored once it has been written from its
        start as a whole, checksummed block; any other write is acknowledged and dropped."""
        if (addr - MACRO_START) % MACRO_SIZE == 0:
            self._macro = (addr, bytearray())
        if not self._macro or addr != self._macro[0] + len(self._macro[1]):
            self._macro = None
            return
        start, block = self._macro
        block += data
        if len(block) >= 0x21:
            n = block[0x1F]
            end = 0x20 + 5 * n
            if block[0] > 30 or n > 70:
                self._macro = None
            elif len(block) > end:
                if block[end] == checksum(block[0x1F:end]):
                    self.flash[start:start + end + 1] = block[:end + 1]
                self._macro = None

    def close(self):
        self.unplug_cable()
        self._host.close()
        self._dev.close()
