# Developer tasks. Run `just` to list them. Needs uv (https://docs.astral.sh/uv/) and just.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Data folder for development (gitignored); override with `just data=/path/to/folder dev`.
data := "data"

# List the recipes
default:
    @just --list --unsorted

# Install the app and dev dependencies into .venv
setup:
    uv sync --group dev

# Start the desktop app and restart it whenever a file in src/ changes
dev:
    uv run --extra native --with watchfiles watchfiles --filter python "budget --native --data-dir {{ data }}" src

# Same as dev, but serve to the browser instead of a native window
dev-web port="8080":
    uv run --with watchfiles watchfiles --filter python "budget --no-show --port {{ port }} --data-dir {{ data }}" src

# Start the app once in the browser (no auto-restart)
run port="8080":
    uv run budget --port {{ port }} --data-dir {{ data }}

# Start the app in a native window, as the installed builds do
native:
    uv run --extra native budget --native --data-dir {{ data }}

# Run the tests (extra arguments go to pytest, e.g. `just test -k fx`)
test *args:
    uv run pytest -q {{ args }}

# Check lint and formatting
lint:
    uvx ruff check .
    uvx ruff format --check .

# Fix lint findings that are safe to fix, and format the code
fmt:
    uvx ruff check --fix .
    uvx ruff format .

# Type-check the sources (pandas, plotly and reportlab ship without type stubs)
typecheck:
    uv run --with mypy mypy --ignore-missing-imports src

# Everything CI cares about: lint, types, tests
check: lint typecheck test

# Build the installer for this OS into installers/ (macOS: .dmg; Windows: Setup.exe, needs Inno Setup 6)
[macos]
build:
    ./packaging/macos/build.sh

[windows]
build:
    & ./packaging/windows/build.ps1

[linux]
build:
    @echo "Installers are built on macOS or Windows only (PyInstaller cannot cross-compile)." && exit 1

# Regenerate the app icons in packaging/assets/
icons:
    uv run --group build python packaging/make_icons.py

# Remove build output and tool caches (keeps data/ and installers/)
[unix]
clean:
    rm -rf build dist .pytest_cache .ruff_cache .mypy_cache
    find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} +

[windows]
clean:
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist, .pytest_cache, .ruff_cache, .mypy_cache
    Get-ChildItem -Recurse -Directory -Filter __pycache__ | Where-Object FullName -notlike '*\.venv\*' | Remove-Item -Recurse -Force
