# Developer tasks. Run `just` to list them. Needs uv (https://docs.astral.sh/uv/) and just.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Data folder for development (gitignored); override with `just data=/path/to/folder dev`.
data := "data"

# List the recipes
default:
    @just --list --unsorted

# Install the app and dev dependencies into .venv, and the git hooks (lefthook.yml)
setup:
    uv sync --group dev
    uv run lefthook install

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

# Start the app in a native window on a year of example data, for manual testing (demo-data/ is rebuilt each run)
demo:
    uv run python scripts/demo_data.py demo-data
    uv run --extra native budget --native --data-dir demo-data

# Run the tests (extra arguments go to pytest, e.g. `just test -k fx`)
test *args:
    uv run pytest -q {{ args }}

# Run the tests with coverage; fails below the threshold in pyproject.toml ([tool.coverage.report])
cov *args:
    uv run pytest -q --cov --cov-report=term {{ args }}

# Check lint and formatting
lint:
    uv run ruff check .
    uv run ruff format --check .

# Fix lint findings that are safe to fix, and format the code
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Type-check the sources (pandas, plotly and reportlab ship without type stubs)
typecheck:
    uv run mypy --ignore-missing-imports src

# Everything CI cares about: lint, types, tests with coverage
check: lint typecheck cov

# Build the installer for this OS (macOS: .dmg; Windows: Setup.exe, needs Inno Setup 6; Linux: .tar.gz)
[macos]
build:
    ./packaging/macos/build.sh

[windows]
build:
    & ./packaging/windows/build.ps1

[linux]
build:
    ./packaging/linux/build.sh

# Regenerate the app icons in packaging/assets/
icons:
    uv run --group build python packaging/make_icons.py

# Remove build output and tool caches (keeps data/ and installers/)
[unix]
clean:
    rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
    find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} +

[windows]
clean:
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist, .pytest_cache, .ruff_cache, .mypy_cache, .coverage, htmlcov
    Get-ChildItem -Recurse -Directory -Filter __pycache__ | Where-Object FullName -notlike '*\.venv\*' | Remove-Item -Recurse -Force
