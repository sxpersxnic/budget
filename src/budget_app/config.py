"""Application-wide constants and paths."""

from __future__ import annotations

import os
import sys
from importlib import metadata
from pathlib import Path

APP_NAME = "Budget"
BASE_CURRENCY = "CHF"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip?0a220d633471964b427451aece0f2cbe"
ECB_CSV_NAME = "eurofxref-hist.csv"
# F33: the update check reads the newest release's tag here (only when the user turns the check on)
GITHUB_REPO = "sxpersxnic/budget"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
COICOP_FILE = RESOURCES_DIR / "coicop2018.csv"
NOTES_FILE = RESOURCES_DIR / "coicop_notes.json"  # F40: the UN's notes in English; translations beside it


def app_version() -> str:
    """Version from the package metadata (pyproject.toml); the installer build bundles that metadata."""
    try:
        return metadata.version("budget-app")
    except metadata.PackageNotFoundError:
        return "dev"


def is_frozen() -> bool:
    """True when running from an installed (PyInstaller) build."""
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    """Per-user application data folder of the operating system."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()


def home_dir() -> Path:
    """The app's home folder: the first profile's data folder, and where the profile registry lives (F34).

    Order: BUDGET_DATA_DIR env var (`--data-dir`), the OS user data folder for installed builds, ./data otherwise.
    """
    default = user_data_dir() if is_frozen() else PROJECT_ROOT / "data"
    path = Path(os.environ.get("BUDGET_DATA_DIR", default))
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """The data folder to open at startup: the current profile's (F34), which is the home folder until the user
    adds a profile. The one place that decides it; the profile switch in Settings opens other folders at runtime."""
    from budget_app.storage import profiles  # noqa: PLC0415 - storage sits above config

    return profiles.current_dir(home_dir())
