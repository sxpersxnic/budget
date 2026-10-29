"""Which data folder the app opens: the profile registry in the app's home folder (F34).

A *profile* is a name and a data folder. The registry `profiles.json` lives in the *home folder*
(`config.home_dir()`: `--data-dir` / `BUDGET_DATA_DIR`, the OS user data folder for installed builds, `./data`
for development), which is also the first profile's data folder. An installation that never added a profile
has no registry and opens the home folder, as before: one profile, *Budget*, in the home folder.

This module only reads and writes the registry; the rules for adding, renaming, switching and moving profiles
are in `services/profiles.py`. `config.data_dir()` asks `current_dir` which folder to open at startup.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGISTRY = "profiles.json"
DEFAULT_ID = "default"
DEFAULT_NAME = "Budget"


@dataclass
class Profile:
    name: str
    path: str  # the data folder, absolute
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def folder(self) -> Path:
        return Path(self.path)


@dataclass
class Registry:
    current: str
    profiles: list[Profile]

    def find(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    @property
    def active(self) -> Profile:
        found = self.find(self.current)
        if found is None:
            raise KeyError(self.current)
        return found


def default(home: Path) -> Registry:
    """The registry of a home folder without one: the home folder itself is the only profile."""
    return Registry(DEFAULT_ID, [Profile(DEFAULT_NAME, str(home), DEFAULT_ID)])


def load(home: Path) -> Registry:
    """The registry of `home`; a missing or damaged one counts as the single profile in the home folder."""
    path = home / REGISTRY
    if not path.is_file():
        return default(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        profiles = [Profile(str(p["name"]), str(p["path"]), str(p["id"])) for p in raw["profiles"]]
        reg = Registry(str(raw["current"]), profiles)
        reg.active  # noqa: B018 - raises KeyError when the current id is unknown (an empty list included)
    except (OSError, ValueError, KeyError, TypeError):
        return default(home)
    return reg


def save(reg: Registry, home: Path) -> None:
    """Write the registry (temp file, fsync, rename)."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / REGISTRY
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"current": reg.current, "profiles": [asdict(p) for p in reg.profiles]}, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def current_dir(home: Path) -> Path:
    """The data folder to open: the current profile's (created if it is missing)."""
    folder = load(home).active.folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder
