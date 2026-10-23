#!/bin/sh
# The flatpak Ghidra (org.ghidra_sre.Ghidra) cannot run headless from the host because
# support/launch.properties links to /var/config/ghidra.properties, which only exists inside
# the sandbox. This builds a mirror install: symlinks to the flatpak tree plus a real support/.
#
# Usage: tools/ghidra/make_flatpak_shim.sh [DEST]   (default ~/.local/share/ghidra-flatpak-shim)
# Then:  DEST/support/analyzeHeadless ...  or  GHIDRA_INSTALL_DIR=DEST for REA.
# Re-run after updating Ghidra in flatpak.
set -eu
SRC=/var/lib/flatpak/app/org.ghidra_sre.Ghidra/x86_64/stable/active/files/lib/ghidra
DEST=${1:-$HOME/.local/share/ghidra-flatpak-shim}

[ -f "$SRC/support/launch.properties.orig" ] || { echo "Ghidra flatpak not found at $SRC" >&2; exit 1; }
rm -rf "$DEST"
mkdir -p "$DEST"
for f in "$SRC"/*; do
    [ "$(basename "$f")" = support ] || ln -s "$f" "$DEST/$(basename "$f")"
done
cp -a "$SRC/support" "$DEST/support"
rm -f "$DEST/support/launch.properties"
cp "$SRC/support/launch.properties.orig" "$DEST/support/launch.properties"
echo "Ghidra shim ready: $DEST"
