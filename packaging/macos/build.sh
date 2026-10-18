#!/bin/sh
# Build the macOS installer (dist/Budget-<version>-macOS-<arch>.dmg) on this Mac.
# Usage: ./packaging/macos/build.sh   (from anywhere)
set -eu
cd "$(dirname "$0")/../.."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to build the installer (end users do not need it)."
    echo "Install it with:  brew install uv   or see https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

uv sync --locked --group build --group dev
uv run pytest -q
uv run --group build python packaging/build.py
echo
echo "Installer ready (commit it to publish it):"
ls -1 installers/macos/*.dmg
