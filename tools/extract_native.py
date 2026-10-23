#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract the native HID libraries (hidusb.dll x64, hidusb32.dll x86) that "Mouse Drive Beta.exe"
embeds as Costura resources. The vendor binaries are not part of this repository.

Offsets are for Mouse Drive Beta 1.0.0.4
(sha256 234e8d894f94c2a4ee444a0187a8c68c5c7863cc0727ae561523a0904699e67c); other versions
need the manifest-resource offsets re-read (e.g. with REA inspect_managed_artifact).

Usage: python3 extract_native.py "Mouse Drive Beta.exe" OUTPUT_DIR
"""
import hashlib
import os
import struct
import sys

RESOURCES_RVA = 266744  # CLI header: resources directory
EMBEDDED = {
    "hidusb32.dll": (2215867, "56c9ed1b2fa5ef9bda42ca9b33dd23fff2d05218cd9bf0e9597cf4361dcf15a5"),
    "hidusb.dll": (4023723, "a64dab55235916b10f76432b926589a820bcbb59c1adff585b4204dd9da8c021"),
}


def rva_to_offset(data, rva):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    for i in range(nsec):
        _, vsize, va, rsize, raw = struct.unpack_from("<8sIIII", data, pe + 24 + opt_size + 40 * i)
        if va <= rva < va + max(vsize, rsize):
            return rva - va + raw
    raise ValueError("RVA 0x%x not mapped" % rva)


def main():
    exe, out_dir = sys.argv[1], sys.argv[2]
    with open(exe, "rb") as f:
        data = f.read()
    base = rva_to_offset(data, RESOURCES_RVA)
    os.makedirs(out_dir, exist_ok=True)
    for name, (offset, sha) in EMBEDDED.items():
        o = base + offset
        length = struct.unpack_from("<I", data, o)[0]
        blob = data[o + 4:o + 4 + length]
        ok = hashlib.sha256(blob).hexdigest() == sha
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(blob)
        print("%-13s %7d bytes  sha256 %s" % (name, length, "OK" if ok else "MISMATCH (other version?)"))


if __name__ == "__main__":
    main()
