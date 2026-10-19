#!/bin/sh
# Install Budget for the current user; no administrator rights needed.
#
#   ./install.sh               install, or update an existing installation
#   ./install.sh --uninstall   remove the program (your data stays)
#
# Program:  ~/.local/opt/Budget            Menu entry: ~/.local/share/applications/budget.desktop
# Command:  ~/.local/bin/budget            Data:       ~/.local/share/budget (never touched here)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$HOME/.local/opt/Budget"
BIN_DIR="$HOME/.local/bin"
DESKTOP="$SHARE/applications/budget.desktop"

uninstall() {
    rm -rf "$APP_DIR"
    rm -f "$BIN_DIR/budget" "$DESKTOP"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$SHARE/applications" || true
    echo "Budget was removed. Your data in $SHARE/budget was kept."
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

if [ ! -x "$HERE/Budget/Budget" ]; then
    echo "Run this script from the unpacked Budget folder (Budget/Budget is missing)." >&2
    exit 1
fi

# Replace the whole program folder: the bundled library set changes between versions.
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$SHARE/applications"
cp -R "$HERE/Budget/." "$APP_DIR/"
cp "$HERE/budget.png" "$APP_DIR/budget.png"
cp "$HERE/install.sh" "$APP_DIR/install.sh"  # so `~/.local/opt/Budget/install.sh --uninstall` works
ln -sf "$APP_DIR/Budget" "$BIN_DIR/budget"

cat > "$DESKTOP" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Name=Budget
Comment=Local personal budgeting
Exec="$APP_DIR/Budget"
Icon=$APP_DIR/budget.png
Terminal=false
Categories=Office;Finance;
DESKTOP_ENTRY
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$SHARE/applications" || true

echo "Budget $(cat "$HERE/VERSION" 2>/dev/null || echo "") is installed."
echo "Start it from the application menu, or run: $BIN_DIR/budget"
