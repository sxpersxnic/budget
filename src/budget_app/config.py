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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
COICOP_FILE = RESOURCES_DIR / "coicop2018.csv"


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


def data_dir() -> Path:
    """Directory holding all CSV data files.

    Order: BUDGET_DATA_DIR env var, the OS user data folder for installed builds, ./data otherwise.
    """
    default = user_data_dir() if is_frozen() else PROJECT_ROOT / "data"
    path = Path(os.environ.get("BUDGET_DATA_DIR", default))
    path.mkdir(parents=True, exist_ok=True)
    return path
