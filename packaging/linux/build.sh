#!/bin/sh
# Build the Linux package (dist/Budget-<version>-Linux-<arch>.tar.gz) on this machine.
# Usage: ./packaging/linux/build.sh   (from anywhere)
set -eu
cd "$(dirname "$0")/../.."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to build the package (end users do not need it)."
    echo "Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

uv sync --locked --group build --group dev
uv run pytest -q
uv run --group build python packaging/build.py
echo
echo "Package ready (attach it and its .sha256 to the release):"
ls -1 dist/*.tar.gz dist/*.tar.gz.sha256
