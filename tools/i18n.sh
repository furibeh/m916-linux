#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Update the translations: extract the strings marked with _() / ngettext() into
# po/m916.pot, merge them into every po/LANG.po and compile m916/locale/LANG/LC_MESSAGES/m916.mo.
# Run it after changing user-visible text, then translate the new entries in each .po file
# (msgfmt prints how many are untranslated or fuzzy) and run it again.
# Needs GNU gettext (Fedora: dnf install gettext). The program itself only uses the .mo files.
set -eu
cd "$(dirname "$0")/.."

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' m916/__init__.py)
pot=po/m916.pot
new=$(mktemp)
trap 'rm -f "$new"' EXIT

xgettext --language=Python --from-code=UTF-8 --keyword=_ --keyword=ngettext:1,2 \
    --add-comments=TRANSLATORS: --add-location=file --sort-by-file \
    --package-name=m916-linux --package-version="$version" \
    --msgid-bugs-address=https://github.com/furibeh/m916-linux/issues \
    --output="$new" m916/*.py
# Keep the old template when only its creation date would change
if [ -f "$pot" ] && diff -q -I '^"POT-Creation-Date:' "$pot" "$new" >/dev/null; then
    :
else
    cp "$new" "$pot"
fi

for po in po/*.po; do
    lang=$(basename "$po" .po)
    msgmerge --quiet --update --backup=none --add-location=file --sort-by-file "$po" "$pot"
    msgattrib --no-obsolete --output-file="$po" "$po"
    mkdir -p "m916/locale/$lang/LC_MESSAGES"
    printf '%s: ' "$lang"
    msgfmt --check --statistics --output-file="m916/locale/$lang/LC_MESSAGES/m916.mo" "$po"
done
