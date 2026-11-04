# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface: python3 -m m916 <command>"""
import argparse
import sys
import time

from . import config as cfg, profile
from .i18n import _, ngettext
from .protocol import FLASH_SIZE, SLOTS, STATUS_NAMES, CMD_STATUS_CHANGED, REPORT_ID, Mouse, \
    MouseAsleep, ProtocolError, describe_link, device_link, on_slot, open_mouse


def progress_printer(label):
    def report(done, total):
        if done % 500 < 10 or done == total:
            print("\r  %s %d/%d bytes" % (label, done, total), end="", file=sys.stderr)
        if done == total:
            print(file=sys.stderr)
    return report


def print_fields(rows, width):
    """Print (label, value) rows with the colons aligned, at least `width` wide."""
    width = max([width] + [len(label) for label, _value in rows])
    for label, value in rows:
        print("%-*s: %s" % (width, label, value))


def load(m):
    image = cfg.read_image(m)
    return image, cfg.Config.decode(image)


def apply(m, image, conf):
    errors = conf.validate()
    if errors:
        sys.exit(_("error: %s") % "; ".join(errors))
    new = conf.encode(image)
    m.write_fields(cfg.changed_fields(image, new), new)


def level_index(conf, level):
    if not 1 <= level <= cfg.MAX_DPI_LEVELS:
        sys.exit(_("DPI level must be 1..%d") % cfg.MAX_DPI_LEVELS)
    return level - 1


def cmd_info(m, args):
    version = m.version().hex(" ")
    level, charging, mv = m.battery()
    print_fields([(_("device"), m.path), (_("connection"), describe_link(m.link)),
                  (_("firmware"), version),
                  (_("battery"), "%d%%%s, %d mV" % (level, _(" (charging)") if charging else "", mv)),
                  (_("slot"), _("%(slot)d of %(count)d") % {"slot": m.slot() + 1, "count": SLOTS})],
                 10)
    image, conf = load(m)
    rate = cfg.RATE_HZ.get(conf.report_rate)
    print_fields([(_("report rate"), "%s Hz" % (rate or _("raw 0x%02x") % conf.report_rate)),
                  (_("DPI levels"), _("%(count)d (active: %(active)d)")
                   % {"count": conf.dpi_count, "active": conf.dpi_active + 1})],
                 13)
    for i, lv in enumerate(conf.levels):
        mark = "*" if i == conf.dpi_active else (" " if i < conf.dpi_count else "-")
        print("  %s %d: %5d DPI%s  %s #%02x%02x%02x"
              % (mark, i + 1, lv.x, "" if lv.x == lv.y else " (Y %d)" % lv.y, _("colour"), *lv.color))
    print(_("options:"))
    for name, value in conf.options.items():
        print("  %-22s %s" % (name, cfg.describe_option(name, value)))
    print(_("buttons:"))
    for k in range(cfg.KEY_COUNT):
        print("  %2d: %s" % (k + 1, conf.describe_button(k)))
    bad = cfg.bad_checks(image)
    if bad:
        print(_("warning: bad check bytes at %s") % ", ".join(hex(o) for o in bad))


def cmd_dump(m, args):
    slot = m.slot()
    data = m.read_flash(0, FLASH_SIZE, progress_printer(_("read")))
    with open(args.file, "wb") as f:
        f.write(data)
    print(_("saved %(size)d bytes of slot %(slot)d to %(file)s")
          % {"size": len(data), "slot": slot + 1, "file": args.file})


def cmd_restore(m, args):
    with open(args.file, "rb") as f:
        image = f.read()
    if len(image) != FLASH_SIZE:
        sys.exit(_("%(file)s is %(size)d bytes, expected %(expected)d")
                 % {"file": args.file, "size": len(image), "expected": FLASH_SIZE})
    slot = m.slot() + 1
    print(_("reading the flash of slot %d to compare...") % slot)
    current = m.read_flash(0, FLASH_SIZE, progress_printer(_("read")))
    writes = cfg.restore_writes(current, image)
    if not writes:
        print(_("flash already matches the image, nothing to do"))
        return
    blocks = ", ".join("0x%04x" % a for a, _data in writes[:20]) + (" ..." if len(writes) > 20 else "")
    print(ngettext("%(count)d block differs in slot %(slot)d: %(blocks)s",
                   "%(count)d blocks differ in slot %(slot)d: %(blocks)s",
                   len(writes)) % {"count": len(writes), "slot": slot, "blocks": blocks})
    # TRANSLATORS: the answer to "[y/N]" questions; "y" is also accepted
    yes = ("y", _("y"))
    if not args.yes and input(_("write them? [y/N] ")).strip().lower() not in yes:
        return
    for a, data in writes:
        m.write_flash(a, data)
    if cfg.restore_writes(m.read_flash(0, FLASH_SIZE, progress_printer(_("verify"))), image):
        sys.exit(_("verification failed after restore"))
    print(_("slot %d restored and verified") % slot)


def cmd_rate(m, args):
    if args.hz > 1000:
        print(_("note: %d Hz may need the 4K dongle; this is a 1K model") % args.hz)
    image, conf = load(m)
    conf.report_rate = cfg.RATES[args.hz]
    apply(m, image, conf)
    print(_("report rate set to %d Hz") % args.hz)


def cmd_dpi(m, args):
    image, conf = load(m)
    i = level_index(conf, args.level)
    conf.levels[i].x = args.dpi
    conf.levels[i].y = args.dpi if args.y is None else args.y
    apply(m, image, conf)
    print(_("DPI level %(level)d set to %(dpi)d") % {"level": args.level, "dpi": args.dpi})


def cmd_dpi_color(m, args):
    hexstr = args.color.lstrip("#")
    if len(hexstr) != 6:
        sys.exit(_("colour must be RRGGBB"))
    image, conf = load(m)
    conf.levels[level_index(conf, args.level)].color = tuple(bytes.fromhex(hexstr))
    apply(m, image, conf)
    print(_("DPI level %(level)d colour set to #%(color)s") % {"level": args.level,
                                                            "color": hexstr.lower()})


def cmd_dpi_levels(m, args):
    image, conf = load(m)
    conf.dpi_count = args.count
    conf.dpi_active = min(conf.dpi_active, args.count - 1)
    apply(m, image, conf)
    print(ngettext("%d DPI level enabled", "%d DPI levels enabled", args.count) % args.count)


def cmd_dpi_active(m, args):
    image, conf = load(m)
    conf.dpi_active = level_index(conf, args.level)
    apply(m, image, conf)
    print(_("active DPI level set to %d") % args.level)


def cmd_option(m, args):
    image, conf = load(m)
    conf.options[args.name] = args.value
    apply(m, image, conf)
    print(_("%(option)s set to %(value)d") % {"option": args.name, "value": args.value})


def button_slot(n):
    if not 1 <= n <= cfg.KEY_COUNT:
        sys.exit(_("button must be 1..%d") % cfg.KEY_COUNT)
    return n - 1


def parse_action(action):
    """(type, p1, p2) or a list of shortcut events for an action argument."""
    kind, _sep, arg = action.partition(":")
    try:
        if kind == "fire":
            interval, times = arg.split(":")
            return (cfg.KEY_FIRE, int(interval), int(times))
        if kind == "dpi-lock":
            return (cfg.KEY_DPI_LOCK, int(arg) // cfg.DPI_STEP - 1, 0)
        if kind == "key":
            return cfg.parse_combo(arg)
    except (ValueError, KeyError) as e:
        sys.exit(_("bad action %(action)r: %(error)s") % {"action": action, "error": e})
    if action not in cfg.BUTTON_ACTIONS:
        sys.exit(_("unknown action %r") % action)
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
    print(_("button %(button)d set to %(action)s") % {"button": args.button,
                                                    "action": conf.describe_button(k)})


def parse_repeat(text):
    if text in cfg.REPEAT_MODES:
        return cfg.REPEAT_MODES[text]
    if text.isdigit() and 1 <= int(text) <= cfg.REPEAT_COUNT_MAX:
        return int(text)
    raise argparse.ArgumentTypeError(_("use 1..%(max)d or %(modes)s")
                                     % {"max": cfg.REPEAT_COUNT_MAX, "modes": "|".join(cfg.REPEAT_MODES)})


def cmd_macro(m, args):
    k = button_slot(args.button)
    try:
        macro = cfg.Macro(args.name, cfg.parse_steps(" ".join(args.steps)))
    except (ValueError, KeyError) as e:
        sys.exit(_("error: %s") % e)
    image, conf = load(m)
    conf.set_macro(k, macro, args.repeat)
    apply(m, image, conf)
    print(_("button %(button)d set to %(action)s") % {"button": args.button,
                                                    "action": conf.describe_button(k)})


def cmd_profile_save(m, args):
    path = profile.resolve(args.file)
    image, conf = load(m)
    profile.save(path, conf, args.description)
    if conf.report_rate not in cfg.RATE_HZ:
        print(_("warning: unknown report rate code 0x%02x left out") % conf.report_rate)
    print(_("profile saved to %s") % path)


def cmd_profile_load(m, args):
    try:
        data = profile.load(profile.resolve(args.file))
        image, conf = load(m)
        profile.apply(conf, data)
    except profile.ProfileError as e:
        sys.exit(_("error: %s") % "\n  ".join(e.errors))
    new = conf.encode(image)
    fields = cfg.changed_fields(image, new)
    if not fields:
        print(_("the mouse already matches the profile, nothing to do"))
        return
    addrs = ", ".join("0x%04x" % a for a, _length in fields)
    print(ngettext("%(count)d field to write: %(addrs)s", "%(count)d fields to write: %(addrs)s",
                   len(fields)) % {"count": len(fields), "addrs": addrs})
    if args.dry_run:
        return
    m.write_fields(fields, new)
    print(_("profile applied and verified"))


def cmd_profile_list(m, args):
    profiles = profile.list_profiles()
    if not profiles:
        print(_("no profiles in %s (save one with: m916 profile save NAME)") % profile.profiles_dir())
        return
    print(_("profiles in %s:") % profile.profiles_dir())
    width = max(len(name) for name, _path, _desc in profiles)
    for name, _path, description in profiles:
        print(("  %-*s  %s" % (width, name, description or "")).rstrip())


def cmd_slot(m, args):
    if args.number is not None:
        m.set_slot(args.number - 1)
        print(_("active slot set to %d") % args.number)
    else:
        print(_("active slot: %(slot)d of %(count)d") % {"slot": m.slot() + 1, "count": SLOTS})


def slot_number(text):
    if text.isdigit() and 1 <= int(text) <= SLOTS:
        return int(text)
    raise argparse.ArgumentTypeError(_("slot must be 1..%d") % SLOTS)


def cmd_keys(m, args):
    from . import keys
    print_fields([(_("modifiers"), " ".join(keys.MODIFIERS)), (_("keys"), " ".join(keys.KEYS)),
                  (_("media"), " ".join(keys.MEDIA_KEYS)), (_("mouse"), " ".join(keys.MOUSE_BUTTONS))],
                 9)


def cmd_watch(m, args):
    print(_("listening for status notifications (Ctrl+C to stop)"))
    try:
        while True:
            resp = m.read_report(None)
            if resp and len(resp) >= 17 and resp[0] == REPORT_ID and resp[1] == CMD_STATUS_CHANGED:
                flags = [n for b, n in enumerate(STATUS_NAMES) if resp[6] >> b & 1]
                print(time.strftime("%H:%M:%S"), _("changed:"), ", ".join(flags) or _("none"),
                      "| raw", resp[6:16].hex(" "))
    except KeyboardInterrupt:
        pass


def build_parser():
    ap = argparse.ArgumentParser(prog="m916",
                                 description=_("Linux configuration tool for the Redragon M916-PRO-1K."))
    ap.add_argument("-d", "--device", help=_("hidraw node (default: auto-detect)"))
    sub = ap.add_subparsers(dest="command", required=True)
    # For the commands that can work on a slot other than the active one
    on_slot_parser = argparse.ArgumentParser(add_help=False)
    on_slot_parser.add_argument("-s", "--slot", metavar="N", type=slot_number,
                                help=_("work on onboard slot 1..%d and then go back to the active "
                                       "one (default: the active slot)") % SLOTS)

    sub.add_parser("info", help=_("show device status and configuration")).set_defaults(func=cmd_info)
    p = sub.add_parser("dump", parents=[on_slot_parser],
                       help=_("save the whole flash of a slot to a file"))
    p.add_argument("file")
    p.set_defaults(func=cmd_dump)
    p = sub.add_parser("restore", parents=[on_slot_parser],
                       help=_("write back a flash image (only changed chunks)"))
    p.add_argument("file")
    p.add_argument("-y", "--yes", action="store_true", help=_("do not ask for confirmation"))
    p.set_defaults(func=cmd_restore)
    p = sub.add_parser("rate", help=_("set the report rate"))
    p.add_argument("hz", type=int, choices=sorted(cfg.RATES))
    p.set_defaults(func=cmd_rate)
    p = sub.add_parser("dpi", help=_("set the DPI of a level"))
    p.add_argument("level", type=int)
    p.add_argument("dpi", type=int)
    p.add_argument("--y", type=int, help=_("separate Y DPI"))
    p.set_defaults(func=cmd_dpi)
    p = sub.add_parser("dpi-color", help=_("set the LED colour of a DPI level"))
    p.add_argument("level", type=int)
    p.add_argument("color", help="RRGGBB")
    p.set_defaults(func=cmd_dpi_color)
    p = sub.add_parser("dpi-levels", help=_("number of enabled DPI levels"))
    p.add_argument("count", type=int)
    p.set_defaults(func=cmd_dpi_levels)
    p = sub.add_parser("dpi-active", help=_("select the active DPI level"))
    p.add_argument("level", type=int)
    p.set_defaults(func=cmd_dpi_active)
    p = sub.add_parser("option", help=_("set a single-byte option (raw value, see 'info')"),
                       epilog=_("lod: 1 = 1 mm, 2 = 2 mm; sensor-mode: 0 = LP, 1 = HP; "
                                "led-off-time and full-performance-time in units of 10 s; "
                                "dpi-led-mode: 1 steady, 2 breathing; dpi-led-brightness: %s; "
                                "dpi-led-speed: 1 (slowest)..5 (fastest)")
                              % "/".join(map(str, cfg.DPI_LED_BRIGHTNESS)))
    p.add_argument("name", choices=sorted(cfg.OPTIONS))
    p.add_argument("value", type=int)
    p.set_defaults(func=cmd_option)
    p = sub.add_parser("button", help=_("assign a function to a button slot"),
                       epilog=_("actions: %s, fire:INTERVAL_MS:SHOTS (each click INTERVAL_MS down and "
                                "INTERVAL_MS up; 0 shots = while held), dpi-lock:DPI, "
                                "key:COMBO (e.g. key:ctrl+c, key:mute; see 'keys')")
                              % ", ".join(cfg.BUTTON_ACTIONS))
    p.add_argument("button", type=int, help=_("button 1..16 (see info)"))
    p.add_argument("action")
    p.set_defaults(func=cmd_button)
    p = sub.add_parser("macro", help=_("assign a macro to a button slot"),
                       epilog=_("steps: +KEY presses, -KEY releases, a number is the delay in ms "
                                "after the previous step, e.g. \"+ctrl 10 +c 10 -c 10 -ctrl\""))
    p.add_argument("button", type=int, help=_("button 1..16 (see info)"))
    p.add_argument("name", help=_("up to %d characters") % cfg.MACRO_NAME_MAX)
    p.add_argument("steps", nargs="+")
    p.add_argument("-r", "--repeat", type=parse_repeat, default=1,
                   help=_("times to run (1..%(max)d), or %(modes)s")
                   % {"max": cfg.REPEAT_COUNT_MAX, "modes": "|".join(cfg.REPEAT_MODES)})
    p.set_defaults(func=cmd_macro)
    p = sub.add_parser("profile", help=_("save, load or list JSON profiles"),
                       epilog=_("a bare NAME is NAME.json in %s; a path with '/' or ending in "
                                ".json is used as given. A profile may leave sections out; "
                                "loading it only writes the fields that change "
                                "(see docs/PROFILES.md)") % profile.profiles_dir())
    psub = p.add_subparsers(dest="profile_command", required=True)
    p = psub.add_parser("save", parents=[on_slot_parser], help=_("save the current configuration"))
    p.add_argument("file", metavar="NAME|FILE")
    p.add_argument("-m", "--description", help=_("text shown by 'profile list'"))
    p.set_defaults(func=cmd_profile_save)
    p = psub.add_parser("load", parents=[on_slot_parser],
                        help=_("apply a profile (only the fields that change)"))
    p.add_argument("file", metavar="NAME|FILE")
    p.add_argument("-n", "--dry-run", action="store_true", help=_("only show what would change"))
    p.set_defaults(func=cmd_profile_load)
    psub.add_parser("list", help=_("list the saved profiles")).set_defaults(func=cmd_profile_list)
    p = sub.add_parser("slot", help=_("show or select the active onboard slot"),
                       epilog=_("the mouse keeps %d independent configurations (slots); the other "
                                "commands work on the active one unless they take --slot")
                       % SLOTS)
    p.add_argument("number", metavar="N", nargs="?", type=slot_number, help=_("slot to select (1..%d)") % SLOTS)
    p.set_defaults(func=cmd_slot)
    sub.add_parser("keys", help=_("list key names for shortcuts and macros")).set_defaults(func=cmd_keys)
    sub.add_parser("watch", help=_("print status notifications")).set_defaults(func=cmd_watch)
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
            sys.exit(_("error: %s") % "\n  ".join(e.errors))

    try:
        # Without -d, the cable if the mouse answers there, else the receiver
        m = Mouse(args.device, link=device_link(args.device)) if args.device else open_mouse()
    except PermissionError as e:
        sys.exit(_("Permission denied on %s: install udev/70-redragon-m916.rules or run with sudo.")
                 % (e.filename or args.device))
    if m is None:
        sys.exit(_("Mouse not found (VID 3554, vendor collection with report ID 8)."))
    try:
        # The receiver answers DeviceOnLine itself; everything else needs the mouse awake
        if args.command != "watch" and not m.online():
            print(_("mouse is asleep or off: move it or click a button..."), file=sys.stderr)
            if not m.wait_online():
                sys.exit(_("error: %s") % _("the mouse did not wake up"))
        slot = getattr(args, "slot", None)  # --slot
        with on_slot(m, None if slot is None else slot - 1):
            args.func(m, args)
    except (MouseAsleep, ProtocolError, OSError) as e:
        sys.exit(_("error: %s") % e)
    finally:
        m.close()
