"""Several households or profiles (F34): add, rename, forget and switch profiles; move a profile's folder (F26).

A profile is a name and a data folder (`storage/profiles.py` keeps the registry in the home folder). Each folder
has its own records, settings, lock and encryption (F27). Switching closes the open folder and opens the other one
through `opener`, set by `main`; it may refuse (the folder is open in another window) before the registry
changes. New profiles without a folder of their own go to `<home>/profiles/<name>`.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from budget_app import config
from budget_app.i18n import t
from budget_app.services.context import ValidationError
from budget_app.storage import claim, lock
from budget_app.storage import profiles as registry
from budget_app.storage.profiles import Profile, Registry

PROFILES_DIR = "profiles"

# set by main: closes the open folder and opens the given one (`moved`: the same records in a new place, F26)
opener: Callable[[Path, bool], None] | None = None


def home() -> Path:
    return config.home_dir()


def load() -> Registry:
    return registry.load(home())


def get(reg: Registry, profile_id: str) -> Profile:
    found = reg.find(profile_id)
    if found is None:
        raise ValidationError(t("There is no such profile"))
    return found


def active() -> Profile:
    return load().active


# ------------------------------------------------------------------ changes
def _clean_name(name: str, reg: Registry, except_id: str = "") -> str:
    name = " ".join(name.split())
    if not name:
        raise ValidationError(t("The profile needs a name"))
    if any(p.name.casefold() == name.casefold() and p.id != except_id for p in reg.profiles):
        raise ValidationError(t("A profile called “{name}” already exists", name=name))
    return name


def folder_for(reg: Registry, text: str, name: str, own: str = "") -> Path:
    """The folder a profile gets: the typed path, or `<home>/profiles/<name>`; never another profile's folder (the
    profile `own` aside)."""
    root = home().resolve()
    if text.strip():
        folder = Path(text.strip()).expanduser().resolve()
    else:
        slug = re.sub(r"[^0-9A-Za-z._-]+", "-", name).strip("-") or "profile"
        folder = root / PROFILES_DIR / slug
    if folder in {p.folder.resolve() for p in reg.profiles if p.id != own}:
        raise ValidationError(t("That folder belongs to another profile"))
    if folder == root or folder in root.parents or folder.parent == root:
        # the home folder is the first profile's data folder; a folder directly inside it would be taken for one of
        # its own (attachments/, backups/)
        raise ValidationError(t("Choose a folder of its own, not the app’s folder or one directly inside it"))
    if folder.is_file():
        raise ValidationError(t("That is a file, not a folder"))
    return folder


def add(name: str, folder_text: str = "") -> Profile:
    """A new profile. Its folder is created if need be; an existing folder with records is used as it is."""
    reg = load()
    name = _clean_name(name, reg)
    folder = folder_for(reg, folder_text, name)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValidationError(t("The folder {folder} can’t be created", folder=folder)) from exc
    profile = Profile(name, str(folder))
    reg.profiles.append(profile)
    registry.save(reg, home())
    return profile


def rename(profile_id: str, name: str) -> Profile:
    reg = load()
    profile = get(reg, profile_id)
    profile.name = _clean_name(name, reg, profile_id)
    registry.save(reg, home())
    return profile


def remove(profile_id: str) -> Profile:
    """Forget a profile; its folder and files stay where they are. The open profile can't be removed."""
    reg = load()
    profile = get(reg, profile_id)
    if profile_id == reg.current:
        raise ValidationError(t("Open another profile before you remove this one"))
    reg.profiles.remove(profile)
    registry.save(reg, home())
    return profile


def switch(profile_id: str) -> Profile:
    """Make a profile the current one and open its folder (through `opener` when the app runs)."""
    reg = load()
    profile = get(reg, profile_id)
    if profile_id == reg.current:
        return profile
    try:
        profile.folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValidationError(t("The folder {folder} can’t be created", folder=profile.folder)) from exc
    if opener is not None:
        opener(profile.folder, False)  # may refuse before anything is saved
    reg.current = profile_id
    registry.save(reg, home())
    return profile


# ------------------------------------------------------------------ moving a folder (F26)
def _not_copied(name: str) -> bool:
    """What belongs to the running app or the home folder, not to the records."""
    return name in (lock.LOCK_FILE, registry.REGISTRY) or claim.is_claim_file(name) or name.endswith(".tmp")


def _files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and not any(_not_copied(n) for n in p.relative_to(folder).parts)
    )


def move(profile_id: str, folder_text: str) -> Profile:
    """Copy a profile's folder to `folder_text`, a new or empty folder (for example in a cloud folder), and use it
    from now on. The open profile reopens there (`opener`, as the same records). The old folder is left as it was."""
    reg = load()
    profile = get(reg, profile_id)
    if not folder_text.strip():
        raise ValidationError(t("Type the folder to move the records to"))
    target = folder_for(reg, folder_text, profile.name, own=profile_id)
    source = profile.folder.resolve()
    if target == source or source in target.parents:
        raise ValidationError(t("Choose a folder outside the current one"))
    if target.is_dir() and any(target.iterdir()):
        raise ValidationError(t("The folder {folder} is not empty", folder=target))
    try:
        target.mkdir(parents=True, exist_ok=True)
        for f in _files(source) if source.is_dir() else []:
            dest = target / f.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    except OSError as exc:
        raise ValidationError(t("The records can’t be copied to {folder}: {error}", folder=target, error=exc)) from exc
    if profile_id == reg.current and opener is not None:
        opener(target, True)
    profile.path = str(target)
    registry.save(reg, home())
    return profile
