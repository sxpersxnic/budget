"""Build a double-clickable installer for the current platform.

    macOS   -> dist/Budget-<version>-macOS-<arch>.dmg   (Budget.app, drag to Applications)
    Windows -> dist/Budget-<version>-Windows-Setup.exe  (per-user install, Start menu entry, uninstaller)

PyInstaller cannot cross-compile: build the macOS installer on a Mac and the Windows installer on
a Windows PC. The wrappers packaging/macos/build.sh and packaging/windows/build.bat check the
prerequisites, run the tests and then call this script.

Usage:  uv sync --group build && uv run --group build python packaging/build.py
Environment:
    MACOS_SIGN_IDENTITY  codesign identity (default "-" = ad-hoc signature)
    ISCC                 path to Inno Setup's ISCC.exe (default: found on PATH / default install dir)
"""

from __future__ import annotations

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
NAME = "Budget"
BUNDLE_ID = "app.budget.local"


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
    run(["hdiutil", "create", "-volname", NAME, "-srcfolder", staging, "-fs", "HFS+", "-format", "UDZO", "-ov", dmg])
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
    if sys.platform == "darwin":
        out = build_macos(app)
    elif sys.platform == "win32":
        out = build_windows(app)
    else:
        out = app
        print("Linux: no installer format configured; the app folder is", app)
    print(f"\nBuilt {out}")


if __name__ == "__main__":
    main()
