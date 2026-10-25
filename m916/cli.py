# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface: python3 -m m916 <command>"""
import argparse
import sys
import time

from . import config as cfg, profile
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
    image = cfg.read_image(m)
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
        print("  %-22s %s" % (name, cfg.describe_option(name, value)))
    print("buttons:")
    for k in range(cfg.KEY_COUNT):
        print("  %2d: %s" % (k + 1, conf.describe_button(k)))
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
    writes = cfg.restore_writes(current, image)
    if not writes:
        print("flash already matches the image, nothing to do")
        return
    print("%d block(s) differ: %s" % (len(writes), ", ".join("0x%04x" % a for a, _ in writes[:20])
                                     + (" ..." if len(writes) > 20 else "")))
    if not args.yes and input("write them? [y/N] ").strip().lower() != "y":
        return
    for a, data in writes:
        m.write_flash(a, data)
    if cfg.restore_writes(m.read_flash(0, FLASH_SIZE, progress_printer("verify")), image):
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


def button_slot(n):
    if not 1 <= n <= cfg.KEY_COUNT:
        sys.exit("button must be 1..%d" % cfg.KEY_COUNT)
    return n - 1


def parse_action(action):
    """(type, p1, p2) or a list of shortcut events for an action argument."""
    kind, _, arg = action.partition(":")
    try:
        if kind == "fire":
            interval, times = arg.split(":")
            return (cfg.KEY_FIRE, int(interval), int(times))
        if kind == "dpi-lock":
            return (cfg.KEY_DPI_LOCK, int(arg) // cfg.DPI_STEP - 1, 0)
        if kind == "key":
            return cfg.parse_combo(arg)
    except (ValueError, KeyError) as e:
        sys.exit("bad action %r: %s" % (action, e))
    if action not in cfg.BUTTON_ACTIONS:
        sys.exit("unknown action %r" % action)
    return cfg.BUTTON_ACTIONS[action]


def cmd_button(m, args):
    k = button_slot(args.button)
    value = parse_action(args.action)
    image, conf = load(m)
    if isinstance(value, list):
        conf.set_shortcut(k, value)
    else:
        conf.buttons[k] = value
    apply(m, image, conf)
    print("button %d set to %s" % (args.button, conf.describe_button(k)))


def parse_repeat(text):
    if text in cfg.REPEAT_MODES:
        return cfg.REPEAT_MODES[text]
    if text.isdigit() and 1 <= int(text) <= cfg.REPEAT_COUNT_MAX:
        return int(text)
    raise argparse.ArgumentTypeError("use 1..%d or %s" % (cfg.REPEAT_COUNT_MAX,
                                                         "|".join(cfg.REPEAT_MODES)))


def cmd_macro(m, args):
    k = button_slot(args.button)
    try:
        macro = cfg.Macro(args.name, cfg.parse_steps(" ".join(args.steps)))
    except (ValueError, KeyError) as e:
        sys.exit("error: %s" % e)
    image, conf = load(m)
    conf.set_macro(k, macro, args.repeat)
    apply(m, image, conf)
    print("button %d set to %s" % (args.button, conf.describe_button(k)))


def cmd_profile_save(m, args):
    path = profile.resolve(args.file)
    image, conf = load(m)
    profile.save(path, conf, args.description)
    if conf.report_rate not in cfg.RATE_HZ:
        print("warning: unknown report rate code 0x%02x left out" % conf.report_rate)
    print("profile saved to %s" % path)


def cmd_profile_load(m, args):
    try:
        data = profile.load(profile.resolve(args.file))
        image, conf = load(m)
        profile.apply(conf, data)
    except profile.ProfileError as e:
        sys.exit("error: " + "\n  ".join(e.errors))
    new = conf.encode(image)
    fields = cfg.changed_fields(image, new)
    if not fields:
        print("the mouse already matches the profile, nothing to do")
        return
    print("%d field(s) to write: %s" % (len(fields), ", ".join("0x%04x" % a for a, _ in fields)))
    if args.dry_run:
        return
    m.write_fields(fields, new)
    print("profile applied and verified")


def cmd_profile_list(m, args):
    profiles = profile.list_profiles()
    if not profiles:
        print("no profiles in %s (save one with: m916 profile save NAME)" % profile.profiles_dir())
        return
    print("profiles in %s:" % profile.profiles_dir())
    width = max(len(name) for name, _, _ in profiles)
    for name, _, description in profiles:
        print(("  %-*s  %s" % (width, name, description or "")).rstrip())


def cmd_keys(m, args):
    from . import keys
    print("modifiers:", " ".join(keys.MODIFIERS))
    print("keys     :", " ".join(keys.KEYS))
    print("media    :", " ".join(keys.MEDIA_KEYS))
    print("mouse    :", " ".join(keys.MOUSE_BUTTONS))


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
    p = sub.add_parser("option", help="set a single-byte option (raw value, see 'info')",
                       epilog="lod: 1 = 1 mm, 2 = 2 mm; sensor-mode: 0 = LP, 1 = HP; "
                              "led-off-time and full-performance-time in units of 10 s; "
                              "dpi-led-mode: 1 steady, 2 flicker; dpi-led-brightness: %s; "
                              "dpi-led-speed: 1..5"
                              % "/".join(map(str, cfg.DPI_LED_BRIGHTNESS)))
    p.add_argument("name", choices=sorted(cfg.OPTIONS))
    p.add_argument("value", type=int)
    p.set_defaults(func=cmd_option)
    p = sub.add_parser("button", help="assign a function to a button slot",
                       epilog="actions: %s, fire:INTERVAL_MS:SHOTS (0 shots = while held), "
                              "dpi-lock:DPI, key:COMBO (e.g. key:ctrl+c, key:mute; see 'keys')"
                              % ", ".join(cfg.BUTTON_ACTIONS))
    p.add_argument("button", type=int, help="slot 1..16 (see info)")
    p.add_argument("action")
    p.set_defaults(func=cmd_button)
    p = sub.add_parser("macro", help="assign a macro to a button slot",
                       epilog="steps: +KEY presses, -KEY releases, a number is the delay in ms "
                              "after the previous step, e.g. \"+ctrl 10 +c 10 -c 10 -ctrl\"")
    p.add_argument("button", type=int, help="slot 1..16 (see info)")
    p.add_argument("name", help="up to %d characters" % cfg.MACRO_NAME_MAX)
    p.add_argument("steps", nargs="+")
    p.add_argument("-r", "--repeat", type=parse_repeat, default=1,
                   help="times to run (1..%d), or %s" % (cfg.REPEAT_COUNT_MAX,
                                                        "|".join(cfg.REPEAT_MODES)))
    p.set_defaults(func=cmd_macro)
    p = sub.add_parser("profile", help="save, load or list JSON profiles",
                       epilog="a bare NAME is NAME.json in %s; a path with '/' or ending in "
                              ".json is used as given. A profile may leave sections out; "
                              "loading it only writes the fields that change "
                              "(see docs/PROFILES.md)" % profile.profiles_dir())
    psub = p.add_subparsers(dest="profile_command", required=True)
    p = psub.add_parser("save", help="save the current configuration")
    p.add_argument("file", metavar="NAME|FILE")
    p.add_argument("-m", "--description", help="text shown by 'profile list'")
    p.set_defaults(func=cmd_profile_save)
    p = psub.add_parser("load", help="apply a profile (only the fields that change)")
    p.add_argument("file", metavar="NAME|FILE")
    p.add_argument("-n", "--dry-run", action="store_true", help="only show what would change")
    p.set_defaults(func=cmd_profile_load)
    psub.add_parser("list", help="list the saved profiles").set_defaults(func=cmd_profile_list)
    sub.add_parser("keys", help="list key names for shortcuts and macros").set_defaults(func=cmd_keys)
    sub.add_parser("watch", help="print status notifications").set_defaults(func=cmd_watch)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command == "keys" or getattr(args, "profile_command", None) == "list":
        return args.func(None, args)
    if args.command == "button":
        parse_action(args.action)  # fail before touching the device
    if args.command == "profile" and args.profile_command == "load":
        try:
            profile.check(profile.load(profile.resolve(args.file)))
        except profile.ProfileError as e:
            sys.exit("error: " + "\n  ".join(e.errors))

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
