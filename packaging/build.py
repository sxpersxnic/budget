"""Build a double-clickable installer for the current platform.

    macOS   -> dist/Budget-<version>-macOS-<arch>.dmg      (Budget.app, drag to Applications)
    Windows -> dist/Budget-<version>-Windows-Setup.exe     (per-user install, Start menu entry, uninstaller)
    Linux   -> dist/Budget-<version>-Linux-<arch>.tar.gz   (app folder + install.sh: per-user install, menu entry)

The finished macOS and Windows installers are also copied to installers/<os>/ (replacing older
versions) together with a SHA-256 checksum. All three are attached to GitHub releases by CI.

PyInstaller cannot cross-compile: build each installer on its own operating system (the GitHub
Actions workflow .github/workflows/installers.yml does all three). The wrappers
packaging/macos/build.sh, packaging/windows/build.bat and packaging/linux/build.sh check the
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
import tarfile
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
# Linux window: pywebview's Qt backend, reached through qtpy's dynamic imports
LINUX_HIDDEN_IMPORTS = (
    "webview.platforms.qt",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
)


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def run(cmd: list[str | Path]) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    # fixed tool commands as an argument list, no shell: nothing user-controlled is interpolated
    subprocess.run([str(c) for c in cmd], check=True)  # noqa: S603  # nosec B603


def pyinstaller() -> Path:
    sep = os.pathsep
    icon = PACKAGING / "assets" / {"darwin": "icon.icns", "win32": "icon.ico"}.get(sys.platform, "icon.png")
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
        # the app reads its version from the package metadata (see budget_app.config.app_version)
        "--copy-metadata",
        "budget-app",
        "--exclude-module",
        "tkinter",
    ]
    if sys.platform == "darwin":
        cmd += ["--osx-bundle-identifier", BUNDLE_ID]
    if sys.platform.startswith("linux"):
        for module in LINUX_HIDDEN_IMPORTS:
            cmd += ["--hidden-import", module]
        cmd += ["--exclude-module", "PyQt5", "--exclude-module", "PySide2", "--exclude-module", "PySide6"]
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
    # the version in the volume name tells users which version they are about to install
    volname = f"{NAME} {version()}"
    run(["hdiutil", "create", "-volname", volname, "-srcfolder", staging, "-fs", "HFS+", "-format", "ULMO", "-ov", dmg])
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


def check_linux_libraries(app_dir: Path) -> None:
    """Fail when a Qt WebEngine library can't be resolved: the package would start without a window.

    PyInstaller bundles the libraries it finds on the build machine; the ones it can't find
    (installed by the CI's "Qt libraries for the build" step) are missing at runtime too.
    """
    internal = app_dir / "_internal"
    qt = internal / "PyQt6" / "Qt6"
    env = {**os.environ, "LD_LIBRARY_PATH": os.pathsep.join([str(internal), str(qt / "lib")])}
    missing: set[str] = set()
    for binary in [*qt.glob("lib/libQt6WebEngine*.so*"), qt / "libexec" / "QtWebEngineProcess"]:
        if binary.exists():
            out = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, env=env, check=False)  # noqa: S603, S607  # nosec B603 B607
            missing |= {line.split()[0] for line in out.stdout.splitlines() if "not found" in line}
    if missing:
        raise SystemExit(
            "Libraries missing from the Linux package (install them on the build machine and build again): "
            + ", ".join(sorted(missing))
        )


def build_linux(app_dir: Path) -> Path:
    """A .tar.gz with the app folder and install.sh, which installs it for the current user."""
    check_linux_libraries(app_dir)
    stem = f"{NAME}-{version()}-Linux-{platform.machine()}"
    staging = WORK / "linux" / stem
    shutil.rmtree(staging.parent, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app_dir, staging / NAME, symlinks=True)
    shutil.copy2(PACKAGING / "linux" / "install.sh", staging / "install.sh")
    shutil.copy2(PACKAGING / "assets" / "icon.png", staging / "budget.png")
    (staging / "VERSION").write_text(version() + "\n", encoding="utf-8")
    archive = DIST / f"{stem}.tar.gz"
    archive.unlink(missing_ok=True)
    with tarfile.open(archive, "w:gz") as tar:  # keeps the executable bits and symlinks
        tar.add(staging, arcname=stem)
    return archive


def main() -> None:
    app = pyinstaller()
    strip_unused(app)
    if sys.platform == "darwin":
        out = publish(build_macos(app), "macos", f"{NAME}-*-macOS-*.dmg")
    elif sys.platform == "win32":
        out = publish(build_windows(app), "windows", f"{NAME}-*-Windows-Setup.exe")
    else:
        out = build_linux(app)  # Qt WebEngine makes it too large to commit; published as a release asset only
    print(f"\nBuilt {out}")


if __name__ == "__main__":
    main()
