#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Print the field constants (enum values, const ints/strings) of a .NET assembly by reading its
ECMA-335 Constant table directly. Used to recover UsbCommandID, KEY_CLASS, REPORT_RATE, etc.

Usage: python3 dotnet_constants.py "Mouse Drive Beta.exe" [TypeSuffix ...]
   e.g. python3 dotnet_constants.py "Mouse Drive Beta.exe" DriverLib.UsbCommandID DriverLib.KEY_CLASS
"""
import struct
import sys


def main():
    data = open(sys.argv[1], "rb").read()
    wanted = sys.argv[2:]
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    secs = [struct.unpack_from("<8sIIII", data, opt + opt_size + 40 * i) for i in range(nsec)]

    def r2o(rva):
        for _, vs, va, rs, raw in secs:
            if va <= rva < va + max(vs, rs):
                return rva - va + raw

    dd = opt + (112 if struct.unpack_from("<H", data, opt)[0] == 0x20B else 96)
    cli = r2o(struct.unpack_from("<I", data, dd + 14 * 8)[0])
    md = r2o(struct.unpack_from("<I", data, cli + 8)[0])

    vlen = struct.unpack_from("<I", data, md + 12)[0]
    p = md + 16 + vlen
    nstreams = struct.unpack_from("<H", data, p + 2)[0]
    p += 4
    streams = {}
    for _ in range(nstreams):
        off, _size = struct.unpack_from("<II", data, p)
        p += 8
        end = data.index(b"\0", p)
        streams[data[p:end].decode()] = md + off
        p = (end + 4) & ~3

    t = streams["#~"]
    heap_sizes = data[t + 6]
    valid = struct.unpack_from("<Q", data, t + 8)[0]
    rows, q = {}, t + 24
    for i in range(64):
        if valid >> i & 1:
            rows[i] = struct.unpack_from("<I", data, q)[0]
            q += 4
    si = 4 if heap_sizes & 1 else 2
    gi = 4 if heap_sizes & 2 else 2
    bi = 4 if heap_sizes & 4 else 2

    def idx(table):
        return 4 if rows.get(table, 0) > 0xFFFF else 2

    def coded(tables, bits):
        return 4 if max(rows.get(x, 0) for x in tables) >= (1 << (16 - bits)) else 2

    tdr = coded([2, 1, 0x1B], 2)
    rs = coded([0, 0x1A, 0x23, 1], 2)
    mrp = coded([2, 1, 0x1A, 6, 0x1B], 3)
    hc = coded([4, 8, 0x17], 2)
    sizes = {0: 2 + si + gi * 3, 1: rs + si * 2, 2: 4 + si * 2 + tdr + idx(4) + idx(6), 3: idx(4),
             4: 2 + si + bi, 5: idx(6), 6: 4 + 2 + 2 + si + bi + idx(8), 7: idx(8), 8: 2 + 2 + si,
             9: idx(2) + tdr, 0xA: mrp + si + bi, 0xB: 2 + hc + bi}
    base = {}
    for i in range(0xC):
        base[i] = q
        q += sizes[i] * rows.get(i, 0)

    strings, blobs = streams["#Strings"], streams["#Blob"]

    def rd(o, n):
        return struct.unpack_from("<I" if n == 4 else "<H", data, o)[0]

    def string(i):
        return data[strings + i:data.index(b"\0", strings + i)].decode()

    def blob(i):
        o = blobs + i
        b = data[o]
        if b & 0x80 == 0:
            return data[o + 1:o + 1 + b]
        if b & 0xC0 == 0x80:
            n = ((b & 0x3F) << 8) | data[o + 1]
            return data[o + 2:o + 2 + n]
        n = ((b & 0x1F) << 24) | (data[o + 1] << 16) | (data[o + 2] << 8) | data[o + 3]
        return data[o + 4:o + 4 + n]

    typedefs = []
    for r in range(rows[2]):
        o = base[2] + r * sizes[2]
        name, ns = string(rd(o + 4, si)), string(rd(o + 4 + si, si))
        typedefs.append((ns + "." + name if ns else name, rd(o + 4 + 2 * si + tdr, idx(4))))
    owner = {}
    for k, (name, first) in enumerate(typedefs):
        end = typedefs[k + 1][1] if k + 1 < len(typedefs) else rows[4] + 1
        for f in range(first, end):
            owner[f] = name

    fmt = {2: "<?", 3: "<H", 4: "<b", 5: "<B", 6: "<h", 7: "<H", 8: "<i", 9: "<I", 0xA: "<q", 0xB: "<Q"}
    for r in range(rows[0xB]):
        o = base[0xB] + r * sizes[0xB]
        etype, parent, value = data[o], rd(o + 2, hc), blob(rd(o + 2 + hc, bi))
        if parent & 3 != 0:  # only Field parents
            continue
        field = parent >> 2
        fname = string(rd(base[4] + (field - 1) * sizes[4] + 2, si))
        own = owner.get(field, "?")
        if wanted and not any(own.endswith(w) for w in wanted):
            continue
        if etype in fmt:
            v = struct.unpack(fmt[etype], value)[0]
            v = hex(v) if isinstance(v, int) and not isinstance(v, bool) else v
        elif etype == 0xE:
            v = repr(value.decode("utf-16le"))
        else:
            v = value.hex()
        print("%s.%s = %s" % (own, fname, v))


if __name__ == "__main__":
    main()
