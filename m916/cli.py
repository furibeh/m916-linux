# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface: python3 -m m916 <command>"""
import argparse
import sys
import time

from . import config as cfg
from .protocol import FLASH_SIZE, STATUS_NAMES, CMD_STATUS_CHANGED, REPORT_ID, Mouse, \
    MouseAsleep, ProtocolError, find_devices


def progress_printer(label):
    def report(done, total):
        if done % 500 < 10 or done == total:
            print("\r  %s %d/%d bytes" % (label, done, total), end="", file=sys.stderr)
        if done == total:
            print(file=sys.stderr)
    return report


def load(m):
    image = m.read_flash(0, cfg.CONFIG_SIZE)
    return image, cfg.Config.decode(image)


def apply(m, image, conf):
    errors = conf.validate()
    if errors:
        sys.exit("error: " + "; ".join(errors))
    new = conf.encode(image)
    m.write_fields(cfg.changed_fields(image, new), new)


def level_index(conf, level):
    if not 1 <= level <= cfg.MAX_DPI_LEVELS:
        sys.exit("DPI level must be 1..%d" % cfg.MAX_DPI_LEVELS)
    return level - 1


def cmd_info(m, args):
    print("device   :", m.path)
    print("firmware :", m.version().hex(" "))
    level, charging, mv = m.battery()
    print("battery  : %d%%%s, %d mV" % (level, " (charging)" if charging else "", mv))
    image, conf = load(m)
    rate = cfg.RATE_HZ.get(conf.report_rate)
    print("report rate  : %s Hz" % (rate or "raw 0x%02x" % conf.report_rate))
    print("DPI levels   : %d (active: %d)" % (conf.dpi_count, conf.dpi_active + 1))
    for i, lv in enumerate(conf.levels):
        mark = "*" if i == conf.dpi_active else (" " if i < conf.dpi_count else "-")
        print("  %s %d: %5d DPI%s  colour #%02x%02x%02x"
              % (mark, i + 1, lv.x, "" if lv.x == lv.y else " (Y %d)" % lv.y, *lv.color))
    print("options:")
    for name, value in conf.options.items():
        print("  %-18s %d" % (name, value))
    print("buttons:")
    for k, key in enumerate(conf.buttons):
        print("  %2d: %s" % (k + 1, cfg.describe_key(*key)))
    bad = cfg.bad_checks(image)
    if bad:
        print("warning: bad check bytes at", ", ".join(hex(o) for o in bad))


def cmd_dump(m, args):
    data = m.read_flash(0, FLASH_SIZE, progress_printer("read"))
    with open(args.file, "wb") as f:
        f.write(data)
    print("saved %d bytes to %s" % (len(data), args.file))


def cmd_restore(m, args):
    with open(args.file, "rb") as f:
        image = f.read()
    if len(image) != FLASH_SIZE:
        sys.exit("%s is %d bytes, expected %d" % (args.file, len(image), FLASH_SIZE))
    print("reading current flash to compare...")
    current = m.read_flash(0, FLASH_SIZE, progress_printer("read"))
    chunks = m.restore_chunks(current, image)
    if not chunks:
        print("flash already matches the image, nothing to do")
        return
    print("%d chunk(s) differ: %s" % (len(chunks), ", ".join("0x%04x" % a for a in chunks[:20])
                                     + (" ..." if len(chunks) > 20 else "")))
    if not args.yes and input("write them? [y/N] ").strip().lower() != "y":
        return
    for a in chunks:
        m.write_flash(a, image[a:a + 10])
    if m.read_flash(0, FLASH_SIZE, progress_printer("verify")) != image:
        sys.exit("verification failed after restore")
    print("restored and verified")


def cmd_rate(m, args):
    if args.hz > 1000:
        print("note: %d Hz may need the 4K dongle; this is a 1K model" % args.hz)
    image, conf = load(m)
    conf.report_rate = cfg.RATES[args.hz]
    apply(m, image, conf)
    print("report rate set to %d Hz" % args.hz)


def cmd_dpi(m, args):
    image, conf = load(m)
    i = level_index(conf, args.level)
    conf.levels[i].x = args.dpi
    conf.levels[i].y = args.dpi if args.y is None else args.y
    apply(m, image, conf)
    print("DPI level %d set to %d" % (args.level, args.dpi))


def cmd_dpi_color(m, args):
    hexstr = args.color.lstrip("#")
    if len(hexstr) != 6:
        sys.exit("colour must be RRGGBB")
    image, conf = load(m)
    conf.levels[level_index(conf, args.level)].color = tuple(bytes.fromhex(hexstr))
    apply(m, image, conf)
    print("DPI level %d colour set to #%s" % (args.level, hexstr.lower()))


def cmd_dpi_levels(m, args):
    image, conf = load(m)
    conf.dpi_count = args.count
    conf.dpi_active = min(conf.dpi_active, args.count - 1)
    apply(m, image, conf)
    print("%d DPI levels enabled" % args.count)


def cmd_dpi_active(m, args):
    image, conf = load(m)
    conf.dpi_active = level_index(conf, args.level)
    apply(m, image, conf)
    print("active DPI level set to %d" % args.level)


def cmd_option(m, args):
    image, conf = load(m)
    conf.options[args.name] = args.value
    apply(m, image, conf)
    print("%s set to %d" % (args.name, args.value))


def cmd_button(m, args):
    if not 1 <= args.button <= cfg.KEY_COUNT:
        sys.exit("button must be 1..%d" % cfg.KEY_COUNT)
    if args.action.startswith("fire:"):
        try:
            _, p1, p2 = args.action.split(":")
            value = (cfg.KEY_FIRE, int(p1), int(p2))
        except ValueError:
            sys.exit("use fire:P1:P2, e.g. fire:10:3")
    else:
        value = cfg.BUTTON_ACTIONS[args.action]
    image, conf = load(m)
    conf.buttons[args.button - 1] = value
    apply(m, image, conf)
    print("button %d set to %s" % (args.button, cfg.describe_key(*value)))


def cmd_watch(m, args):
    print("listening for status notifications (Ctrl+C to stop)")
    try:
        while True:
            resp = m.read_report(None)
            if resp and len(resp) >= 17 and resp[0] == REPORT_ID and resp[1] == CMD_STATUS_CHANGED:
                flags = [n for b, n in enumerate(STATUS_NAMES) if resp[6] >> b & 1]
                print(time.strftime("%H:%M:%S"), "changed:", ", ".join(flags) or "none",
                      "| raw", resp[6:16].hex(" "))
    except KeyboardInterrupt:
        pass


def build_parser():
    ap = argparse.ArgumentParser(prog="m916",
                                 description="Linux configuration tool for the Redragon M916-PRO-1K.")
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
    p.add_argument("hz", type=int, choices=sorted(cfg.RATES))
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
    p.add_argument("name", choices=sorted(cfg.OPTIONS))
    p.add_argument("value", type=int)
    p.set_defaults(func=cmd_option)
    p = sub.add_parser("button", help="assign a function to a button slot")
    p.add_argument("button", type=int, help="slot 1..16 (see info)")
    p.add_argument("action", help="|".join(cfg.BUTTON_ACTIONS) + "|fire:P1:P2")
    p.set_defaults(func=cmd_button)
    sub.add_parser("watch", help="print status notifications").set_defaults(func=cmd_watch)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command == "button" and args.action not in cfg.BUTTON_ACTIONS \
            and not args.action.startswith("fire:"):
        ap.error("unknown action %r" % args.action)

    path = args.device
    if not path:
        devices = find_devices()
        if not devices:
            sys.exit("Mouse not found (VID 3554, vendor collection with report ID 8).")
        path = devices[0]
    try:
        m = Mouse(path)
    except PermissionError:
        sys.exit("Permission denied on %s: install udev/70-redragon-m916.rules or run with sudo."
                 % path)
    try:
        # The receiver answers DeviceOnLine itself; everything else needs the mouse awake
        if args.command != "watch" and not m.online():
            print("mouse is asleep or off: move it or click a button...", file=sys.stderr)
            if not m.wait_online():
                sys.exit("error: the mouse did not wake up")
        args.func(m, args)
    except (MouseAsleep, ProtocolError, OSError) as e:
        sys.exit("error: %s" % e)
    finally:
        m.close()
