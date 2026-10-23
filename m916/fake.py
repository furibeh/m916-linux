# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated receiver + mouse backed by a flash image, for tests and GUI development."""
import socket
import threading

from .protocol import (CMD_BATTERY, CMD_DEVICE_ONLINE, CMD_READ_FLASH, CMD_READ_VERSION,
                       CMD_STATUS_CHANGED, CMD_WRITE_FLASH, REPORT_ID, Mouse, checksum)


class FakeMouse:
    """Answers the vendor protocol like the real hardware (verified on 3554:F5D5)."""

    def __init__(self, flash):
        self.flash = bytearray(flash)
        self.asleep = False
        self._host, self._dev = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        threading.Thread(target=self._serve, daemon=True).start()

    def mouse(self):
        return Mouse("fake", fd=self._host.fileno())

    def notify(self, flags):
        pkt = bytearray([REPORT_ID, CMD_STATUS_CHANGED, 0, 0, 0, 0x0A, flags]).ljust(16, b"\0")
        self._dev.send(bytes(pkt) + bytes([checksum(pkt)]))

    def _serve(self):
        while True:
            try:
                pkt = self._dev.recv(64)
            except OSError:
                return
            if not pkt:
                return
            if len(pkt) != 17 or pkt[16] != checksum(pkt[:16]):
                continue
            cmd, addr, n = pkt[1], pkt[3] << 8 | pkt[4], pkt[5]
            if self.asleep and cmd != CMD_DEVICE_ONLINE:
                continue  # only the receiver answers while the mouse sleeps
            resp = bytearray(pkt)
            if cmd == CMD_READ_FLASH:
                resp[6:16] = bytes(self.flash[addr:addr + n]).ljust(10, b"\0")
            elif cmd == CMD_WRITE_FLASH:
                self.flash[addr:addr + n] = pkt[6:6 + n]
            elif cmd == CMD_READ_VERSION:
                resp[5:8] = b"\x02\x02\x00"
            elif cmd == CMD_DEVICE_ONLINE:
                resp[5:10] = b"\x01" + (b"\x00" if self.asleep else b"\x01") + b"\x25\x81\x06"
            elif cmd == CMD_BATTERY:
                resp[5:10] = b"\x02\x64\x00\x10\x48"
            resp[16] = checksum(resp[:16])
            self._dev.send(bytes(resp))

    def close(self):
        self._host.close()
        self._dev.close()
