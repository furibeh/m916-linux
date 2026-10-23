"""Tests for m916.py against a simulated mouse backed by a real flash dump.

Run: python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import m916  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "m916_flash.bin")


class FakeMouse:
    """Answers the vendor protocol like the real receiver + mouse (verified on 3554:F5D5)."""

    def __init__(self, flash):
        self.flash = bytearray(flash)
        self.host, self.dev = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                pkt = self.dev.recv(64)
            except OSError:
                return
            if not pkt:
                return
            assert len(pkt) == 17 and pkt[16] == m916.checksum(pkt[:16]), pkt.hex()
            cmd, addr, n = pkt[1], pkt[3] << 8 | pkt[4], pkt[5]
            resp = bytearray(pkt)
            if cmd == m916.CMD_READ_FLASH:
                resp[6:16] = bytes(self.flash[addr:addr + n]).ljust(10, b"\0")
            elif cmd == m916.CMD_WRITE_FLASH:
                self.flash[addr:addr + n] = pkt[6:6 + n]
            elif cmd == m916.CMD_READ_VERSION:
                resp[5:8] = b"\x02\x02\x00"
            elif cmd == m916.CMD_DEVICE_ONLINE:
                resp[5:10] = b"\x01\x01\x25\x81\x06"
            elif cmd == m916.CMD_BATTERY:
                resp[5:10] = b"\x02\x64\x00\x10\x48"
            resp[16] = m916.checksum(resp[:16])
            self.dev.send(bytes(resp))

    def close(self):
        self.host.close()
        self.dev.close()


class M916Test(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.mouse = m916.Mouse("fake", fd=self.fake.host.fileno())

    def tearDown(self):
        self.fake.close()

    def run_cmd(self, func, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            func(self.mouse, argparse.Namespace(**kwargs))
        return out.getvalue()

    def test_dpi_codec(self):
        for dpi, raw in [(50, (0x00, 0x00)), (1000, (0x13, 0x00)), (26000, (0x07, 0x88))]:
            self.assertEqual(m916.dpi_encode(dpi), raw)
            self.assertEqual(m916.dpi_decode(*raw), dpi)

    def test_packet_checksum(self):
        # SetPCDriverStatus(1) as built by hidusb.dll
        _, resp = self.mouse.transact(0x02, data=b"\x01")
        self.assertEqual(resp.hex(" "), "08 02 00 00 00 01 01 00 00 00 00 00 00 00 00 00 49")

    def test_info_decodes_fixture(self):
        out = self.run_cmd(m916.cmd_info)
        self.assertIn("report rate  : 1000 Hz", out)
        self.assertIn("* 2:  2000 DPI  colour #0000ff", out)
        self.assertIn("5: 26000 DPI", out)
        self.assertIn("12: fire (p1=10, p2=3)", out)
        self.assertNotIn("warning", out)

    def test_writes_touch_only_expected_bytes(self):
        self.run_cmd(m916.cmd_dpi, level=3, dpi=3200, y=None)
        self.run_cmd(m916.cmd_dpi_color, level=3, color="00ff00")
        self.run_cmd(m916.cmd_rate, hz=500)
        self.run_cmd(m916.cmd_button, button=7, action="middle")
        self.run_cmd(m916.cmd_option, name="debounce", value=4)
        changed = [i for i in range(m916.FLASH_SIZE) if self.fake.flash[i] != self.original[i]]
        self.assertEqual(changed, [0x00, 0x01, 0x14, 0x15, 0x17, 0x35, 0x37,
                                   0x78, 0x79, 0x7B, 0xA9, 0xAA])

    def test_restore(self):
        self.run_cmd(m916.cmd_dpi_color, level=1, color="123456")
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(self.original)
            f.flush()
            self.run_cmd(m916.cmd_restore, file=f.name, yes=True)
        self.assertEqual(bytes(self.fake.flash), self.original)


if __name__ == "__main__":
    unittest.main()
