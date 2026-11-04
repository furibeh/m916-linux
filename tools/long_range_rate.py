#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure whether long range mode (0x16) changes the real report rate over the receiver.

It alternates long range off and on, and in each window counts the movement reports
(report ID 1) that arrive on the same hidraw node as the vendor protocol while you move the
mouse in circles without stopping. At the end it puts long range back as it was.

The reports are timestamped when Python reads them, so single intervals carry some jitter;
the reports per second of movement and the share of long gaps are the figures to compare.

Usage: python3 tools/long_range_rate.py [--seconds 10] [--rounds 2] [-d /dev/hidrawN]
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from m916 import config as cfg  # noqa: E402
from m916.protocol import Mouse, device_link, open_mouse  # noqa: E402

MOVE_REPORT_ID = 1
PAUSE = 0.05     # longer intervals count as "the hand stopped" and are left out
SETTLE = 1.0     # seconds ignored after switching the mode


def record(m, seconds):
    """Read timestamps (s) of the movement reports for `seconds`."""
    stamps = []
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return stamps
        resp = m.read_report(left)
        if resp and resp[0] == MOVE_REPORT_ID:
            stamps.append(time.monotonic())


def summarize(stamps, nominal_hz):
    """Figures of one window: reports, moving time, reports/s, median and p99 interval (ms)
    and the share of intervals over 1.5 times the nominal one (a report missing)."""
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    moving = [g for g in gaps if g < PAUSE]
    if not moving:
        return None
    total = sum(moving)
    moving.sort()
    long_gap = 1.5 / nominal_hz  # at least one report missing
    return {
        "reports": len(stamps),
        "moving_s": total,
        "rate": len(moving) / total,
        "median_ms": statistics.median(moving) * 1000,
        "p99_ms": moving[min(len(moving) - 1, int(len(moving) * 0.99))] * 1000,
        "long_gaps_pct": 100.0 * sum(g > long_gap for g in moving) / len(moving),
    }


def countdown(text):
    print(text, flush=True)
    for n in (3, 2, 1):
        print("  %d..." % n, flush=True)
        time.sleep(1)
    print("  GO: move the mouse in circles, without stopping", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-d", "--device", help="hidraw node (default: auto-detect)")
    ap.add_argument("--seconds", type=float, default=10, help="length of each window")
    ap.add_argument("--rounds", type=int, default=2, help="off/on pairs")
    ap.add_argument("--allow-cable", action="store_true",
                    help="also measure over the cable (long range is a radio setting)")
    args = ap.parse_args()

    m = Mouse(args.device, link=device_link(args.device)) if args.device else open_mouse()
    if m is None:
        sys.exit("mouse not found")
    try:
        if m.link == "wired" and not args.allow_cable:
            sys.exit("the mouse is on the cable: unplug it to measure over the receiver "
                     "(or pass --allow-cable)")
        if not m.online():
            sys.exit("the mouse is asleep: move it and try again")
        code = m.read_flash(cfg.OFF_REPORT_RATE, 1)[0]
        nominal = cfg.RATE_HZ.get(code)
        if nominal is None:
            sys.exit("unknown report rate code 0x%02x" % code)
        original = m.long_range()
        print("connection: %s, polling rate %d Hz, long range now %s"
              % (m.link, nominal, "on" if original else "off"))
        print("Each window lasts %g s. Keep the mouse moving the same way in all of them."
              % args.seconds)
        results = []
        try:
            for r in range(args.rounds):
                for on in (False, True):
                    m.set_long_range(on)
                    m.poll_notifications()  # drop whatever arrived meanwhile
                    countdown("\nround %d, long range %s" % (r + 1, "ON" if on else "OFF"))
                    record(m, SETTLE)
                    stats = summarize(record(m, args.seconds), nominal)
                    results.append((r + 1, on, stats))
                    print("  stop" + ("" if stats else " (no movement seen in this window)"))
        finally:
            m.set_long_range(original)
            print("\nlong range back to %s (verified)" % ("on" if original else "off"))
    finally:
        m.close()

    print("\nround  long range  reports  moving s  reports/s  median ms  p99 ms  gaps >%.1f ms"
          % (1500.0 / nominal))
    for r, on, s in results:
        if s is None:
            print("%5d  %-10s  no movement" % (r, "on" if on else "off"))
            continue
        print("%5d  %-10s  %7d  %8.2f  %9.1f  %9.3f  %6.3f  %10.2f %%"
              % (r, "on" if on else "off", s["reports"], s["moving_s"], s["rate"],
                 s["median_ms"], s["p99_ms"], s["long_gaps_pct"]))


if __name__ == "__main__":
    main()
