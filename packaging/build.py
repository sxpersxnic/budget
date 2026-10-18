"""Build a double-clickable installer for the current platform.

    macOS   -> dist/Budget-<version>-macOS-<arch>.dmg   (Budget.app, drag to Applications)
    Windows -> dist/Budget-<version>-Windows-Setup.exe  (per-user install, Start menu entry, uninstaller)

The finished installer is also copied to installers/<os>/ (replacing older versions) together
with a SHA-256 checksum, so it can be committed and downloaded from the repository.

PyInstaller cannot cross-compile: build the macOS installer on a Mac and the Windows installer on
a Windows PC. The wrappers packaging/macos/build.sh and packaging/windows/build.bat check the
prerequisites, run the tests and then call this script.

Usage:  uv sync --group build && uv run --group build python packaging/build.py
Environment:
    MACOS_SIGN_IDENTITY  codesign identity (default "-" = ad-hoc signature)
    ISCC                 path to Inno Setup's ISCC.exe (default: found on PATH / default install dir)
"""

from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import shutil
import subprocess  # nosec B404 - build tool running fixed commands
import sys
import tomllib
from pathlib import Path

import nicegui

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
WORK = ROOT / "build"
INSTALLERS = ROOT / "installers"
NAME = "Budget"
BUNDLE_ID = "app.budget.local"
# NiceGUI ships JavaScript bundles for every element; these are never used by the app and are only
# loaded lazily when such an element is created, so their payload can be dropped (~10 MB).
UNUSED_NICEGUI_BUNDLES = ("mermaid", "codemirror", "echart", "json_editor", "scene", "leaflet", "xterm")
# plotly's own JS (Jupyter widget + offline HTML export); NiceGUI renders charts with its own plotly.js
UNUSED_PLOTLY_FILES = ("widgetbundle.js", "plotly.min.js")


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def run(cmd: list[str | Path]) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    # fixed tool commands as an argument list, no shell: nothing user-controlled is interpolated
    subprocess.run([str(c) for c in cmd], check=True)  # noqa: S603  # nosec B603


def pyinstaller() -> Path:
    sep = os.pathsep
    icon = PACKAGING / "assets" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    cmd: list[str | Path] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        NAME,
        "--icon",
        icon,
        "--distpath",
        DIST,
        "--workpath",
        WORK / "pyinstaller",
        "--specpath",
        WORK,
        "--paths",
        ROOT / "src",
        "--add-data",
        f"{Path(nicegui.__file__).parent}{sep}nicegui",
        "--add-data",
        f"{ROOT / 'src' / 'budget_app' / 'resources'}{sep}budget_app/resources",
        "--exclude-module",
        "tkinter",
    ]
    if sys.platform == "darwin":
        cmd += ["--osx-bundle-identifier", BUNDLE_ID]
    cmd.append(PACKAGING / "launcher.py")
    run(cmd)
    return DIST / (f"{NAME}.app" if sys.platform == "darwin" else NAME)


def strip_unused(app: Path) -> None:
    for dist_dir in list(app.rglob("dist")):
        element = dist_dir.parent
        if element.name in UNUSED_NICEGUI_BUNDLES and element.parent.name == "elements" and dist_dir.is_dir():
            if dist_dir.is_symlink():
                dist_dir.unlink()
            else:
                shutil.rmtree(dist_dir)
            print(f"  removed unused {element.name} bundle")
    for name in UNUSED_PLOTLY_FILES:
        for f in app.rglob(name):
            if f.parent.name == "package_data" and f.parent.parent.name == "plotly":
                f.unlink()
                print(f"  removed unused plotly/{name}")


def publish(installer: Path, platform_dir: str, pattern: str) -> Path:
    """Copy the installer to installers/<platform_dir>/, replacing older ones, and write its checksum."""
    dest = INSTALLERS / platform_dir
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob(pattern):
        old.unlink()
    target = dest / installer.name
    shutil.copy2(installer, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (dest / "SHA256SUMS.txt").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return target


def build_macos(app: Path) -> Path:
    plist_path = app / "Contents" / "Info.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    plist.update(
        {
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": version(),
            "CFBundleVersion": version(),
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "Local budgeting app",
        }
    )
    plist_path.write_bytes(plistlib.dumps(plist))
    # Info.plist changed after PyInstaller signed the bundle: sign again (ad-hoc unless an identity is given)
    identity = os.environ.get("MACOS_SIGN_IDENTITY", "-")
    sign = ["codesign", "--force", "--deep", "--sign", identity]
    if identity != "-":
        sign += ["--options", "runtime", "--timestamp"]
    run([*sign, app])
    run(["codesign", "--verify", "--deep", "--strict", app])

    staging = WORK / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    dmg = DIST / f"{NAME}-{version()}-macOS-{platform.machine()}.dmg"
    dmg.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", NAME, "-srcfolder", staging, "-fs", "HFS+", "-format", "ULMO", "-ov", dmg])
    return dmg


def find_iscc() -> str:
    candidates = [
        os.environ.get("ISCC"),
        shutil.which("iscc"),
        shutil.which("ISCC"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise SystemExit("Inno Setup 6 not found: install it (winget install JRSoftware.InnoSetup) or set ISCC")


def build_windows(app_dir: Path) -> Path:
    run(
        [
            find_iscc(),
            f"/DAppVersion={version()}",
            f"/DSourceDir={app_dir}",
            f"/DOutputDir={DIST}",
            f"/DIconFile={PACKAGING / 'assets' / 'icon.ico'}",
            PACKAGING / "windows" / "budget.iss",
        ]
    )
    return DIST / f"{NAME}-{version()}-Windows-Setup.exe"


def main() -> None:
    app = pyinstaller()
    strip_unused(app)
    if sys.platform == "darwin":
        out = publish(build_macos(app), "macos", f"{NAME}-*-macOS-*.dmg")
    elif sys.platform == "win32":
        out = publish(build_windows(app), "windows", f"{NAME}-*-Windows-Setup.exe")
    else:
        out = app
        print("Linux: no installer format configured; the app folder is", app)
    print(f"\nBuilt {out}")


if __name__ == "__main__":
    main()
