# SPDX-License-Identifier: GPL-3.0-or-later
"""Translations (gettext). Source strings are English; catalogs live in m916/locale and are
built from po/*.po by tools/i18n.sh. The language comes from LANGUAGE, LC_ALL, LC_MESSAGES
or LANG, as for any program."""
import gettext
import os

DOMAIN = "m916"
LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")

translation = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
_ = translation.gettext
ngettext = translation.ngettext


def language():
    """Language of the loaded catalog (e.g. "es"), or None when showing the source strings."""
    return translation.info().get("language") or None
