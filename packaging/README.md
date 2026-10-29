# Building the installers

These steps produce stand-alone installers that bundle Python and every dependency. End users
need neither uv nor Python.

| Platform | Output | Build on |
| --- | --- | --- |
| macOS | `dist/Budget-<version>-macOS-<arch>.dmg` (contains `Budget.app`) | a Mac. The architecture matches the build machine: arm64 on Apple Silicon, x86_64 on Intel. |
| Windows | `dist/Budget-<version>-Windows-Setup.exe` (Inno Setup, per-user install) | a Windows 10/11 PC |
| Linux | `dist/Budget-<version>-Linux-<arch>.tar.gz` (app folder and `install.sh`, per-user install) | Linux with glibc 2.34+ (Ubuntu 22.04 or later) |

PyInstaller cannot cross-compile, so each installer is built on its own operating system. The
installers are built by hand and attached to the GitHub release of their version (see
[Releasing an update](#releasing-an-update)); no workflow builds them. The CI workflow
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs the checks only, and `just ci` runs
the same checks locally.

The builds are unsigned (see [Signing](#signing-optional)).

## macOS

```bash
brew install uv                # once; or see https://docs.astral.sh/uv/
./packaging/macos/build.sh
```

## Windows

1. Install the prerequisites (once), then open a new terminal:

   ```powershell
   winget install --id astral-sh.uv -e
   winget install --id JRSoftware.InnoSetup -e
   ```

2. Double-click `packaging\windows\build.bat`, or run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
   ```

If Inno Setup is installed elsewhere, set the `ISCC` environment variable to the full path of
`ISCC.exe`.

## Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # once
./packaging/linux/build.sh
```

The build machine needs the X11 and EGL libraries Qt WebEngine links against. PyInstaller
bundles the ones it finds, and `build.py` stops if one is still missing. On Ubuntu 22.04:

```bash
sudo apt-get install -y libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
  libxcb-xkb1 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxfixes3 libfontconfig1 \
  libdbus-1-3 libglib2.0-0 liblcms2-2 libminizip1 libopus0 libsnappy1v5 libwebp7 libwebpdemux2 \
  libwebpmux3
```

## What the build does

The three wrappers:

1. check the prerequisites;
2. run `uv sync --locked --group build --group dev`;
3. run the tests;
4. call `build.py`.

`build.py` itself can also be run directly:
`uv run --group build python packaging/build.py`.

The version comes from `pyproject.toml` (`[project] version`). Raise it before building a new
release; it is the only place it is set.

## Releasing an update

1. Raise `version` in `pyproject.toml`, for example `0.1.0` → `0.2.0`, run `uv sync`, and merge
   the change into `main` through a pull request.
2. Tag that commit with the same version and push the tag:
   `git tag v0.2.0 && git push origin v0.2.0`. The tag must match `[project] version`; the
   in-app update check (F33) reads the newest release's tag.
3. Build on each platform from the tagged commit (`just build`, or the wrappers above). Each
   build leaves the installer and its `.sha256` file in `dist/`.
4. Create the release and attach the files, from the first machine:

   ```bash
   gh release create v0.2.0 dist/Budget-0.2.0-* --title "Budget 0.2.0" --generate-notes --verify-tag
   ```

   and from the others: `gh release upload v0.2.0 dist/Budget-0.2.0-*`. The installers are not
   committed to the repository.

What makes the new installer an update rather than a second copy:

- **Version everywhere.** `build.py` bundles the package metadata (`--copy-metadata
  budget-app`), so the app shows its version in the sidebar. The same number names the DMG,
  its volume ("Budget 0.2.0"), `Info.plist`, the Windows Setup file and its *Installed apps* entry.
- **Windows** (`windows/budget.iss`). The fixed `AppId` finds the existing installation, whose
  folder, language and tasks are reused. The tasks page is skipped and the Ready page states
  *Budget 0.1.0 will be replaced by version 0.2.0*. `CloseApplications` asks to close a running
  Budget (Windows Restart Manager). `[InstallDelete]` removes the old `_internal` folder first:
  PyInstaller's file set changes between versions, and stale libraries must not survive.
- **macOS.** `Budget.app` keeps its bundle identifier (`app.budget.local`), so dragging the new
  version onto *Applications* replaces the old one. The data lives outside the bundle.
- **Data.** Both platforms keep user data outside the program folder (`~/Library/Application
  Support/Budget`, `%APPDATA%\Budget`), so installing an update never touches it; the Windows
  uninstaller leaves it too. In the app itself:
  - The first start of a new version copies the data files to `backups/<date>_<old>_to_<new>/`
    before reading them (`storage/backup.py`, keeps the last ten).
  - Every write goes to a temporary file, is flushed to disk and then replaces the old file,
    so closing the app during an update can't leave a half-written file.
  - Columns and settings a version doesn't know are written back unchanged, so a downgrade
    doesn't drop them.
  - A release that adds a field must give it a default value, so files from older versions
    still load. A release that changes the format must migrate old files when they are
    loaded; the backup is the safety net.

| File | Purpose |
| --- | --- |
| `build.py` | Runs PyInstaller (`--windowed --onedir`, plus the NiceGUI static files and the COICOP data). Then, on macOS, sets the version in `Info.plist`, signs the app and creates the DMG with an *Applications* shortcut. On Windows, it runs Inno Setup. |
| `macos/build.sh` | One-command macOS build. |
| `windows/build.bat`, `windows/build.ps1` | One-command Windows build. |
| `linux/build.sh` | One-command Linux build. |
| `linux/install.sh` | Shipped in the Linux package. Installs for the current user into `~/.local/opt/Budget`, with a menu entry (`~/.local/share/applications/budget.desktop`) and the command `~/.local/bin/budget`; replaces the whole program folder on updates; `--uninstall` removes it and keeps the data. |
| `windows/budget.iss` | Inno Setup script. Installs into `%LOCALAPPDATA%\Programs\Budget` (or Program Files when run as admin), with a Start-menu entry, an optional desktop icon and an uninstaller. |
| `launcher.py` | Frozen entry point. It calls `multiprocessing.freeze_support()` before the app is imported, because the native window runs in a child process. On Linux it selects pywebview's Qt backend, the one that is bundled. |
| `make_icons.py` | Regenerates `assets/icon.png`, `icon.icns` and `icon.ico`. |

Build output goes to `dist/` and intermediate files to `build/`. Both are git-ignored. Next to each
installer, `build.py` writes `<installer>.sha256`, which `shasum -a 256 -c` (macOS, Linux) checks.

To keep the downloads small, `build.py` removes JavaScript payloads the app never loads: unused
NiceGUI element bundles, and Plotly's Jupyter widget and offline-HTML scripts. NiceGUI renders
charts with its own plotly.js.

**QR-bill libraries (F03).** `pypdfium2` (PDFium, about 7 MB) and `zxing-cpp` (1.4 MB) bring native code.
Both ship wheels for every target: pypdfium2 `py3-none` wheels for macOS 13+ arm64 and x86_64, Windows x64
and arm64 and manylinux 2.17 x86_64 and aarch64; zxing-cpp `cp312-abi3` wheels (so also Python 3.13) for
macOS 11+ arm64, 10.13+ x86_64, Windows x64 and arm64 and manylinux 2.27/2.28 x86_64 and aarch64. The build
needs no change: `pyinstaller-hooks-contrib` has hooks for `pypdfium2` and `pypdfium2_raw` (they collect
`libpdfium` and its `version.json`), and `zxingcpp` is a single extension module that the import analysis
finds even though the app imports it only when a bill is scanned. Checked on macOS arm64 with a frozen
probe that read a QR bill from a generated PDF and a PNG. PDFium's macOS library is built for macOS 13
(`minos 13.0`) and numpy's arm64 wheel needs macOS 14, so `Info.plist` declares 14.0 as the minimum
(it said 11.0 before, which the bundled numpy never ran on).

**Receipt reader (F04).** `rapidocr` (a pure-Python wheel of 27 MB that carries its PP-OCRv6 models) and
`onnxruntime` (21 MB on macOS arm64, 15 MB on Windows, 24 MB on Linux; cp313 wheels for macOS 14 arm64, Windows x64 and
arm64, manylinux 2.28 x86_64 and aarch64, **none for macOS x86_64**, so the dependency carries a marker and Intel Macs
run without the reader) are the optional extra `ocr` and part of the `build` group, so the installers include them;
RapidOCR pulls in `opencv-python-headless` (35–60 MB, abi3 wheels for every target), `shapely`, `pyclipper`, `PyYAML`,
`omegaconf`, `requests`, `tqdm` and `colorlog`. `pyinstaller-hooks-contrib` has hooks for `cv2`, `onnxruntime` and
`shapely`; RapidOCR imports its engine module by name and reads `config.yaml`, `default_models.yaml` and `models/*.onnx`
from its package folder, so `build.py` passes `--collect-submodules rapidocr --collect-data rapidocr`. Expect the
installed app to grow by about 130 MB and the compressed DMG and Setup by roughly 60–70 MB; release assets may be
up to 2 GB, so that is no problem. To build without the reader, drop the two packages from the `build` group. The frozen
build with the reader has not been tried yet (the first build should open the Receipt sheet and drop a photo; the
app must say "Read from the receipt: …", never download anything, and the log must not show
"The OCR engine could not be loaded").

## Signing (optional)

- **macOS:** the build is ad-hoc signed by default. Users confirm the first launch once with
  **System Settings → Privacy & Security → Open Anyway**. To avoid that prompt, sign with an
  Apple Developer ID and notarize:

  ```bash
  MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./packaging/macos/build.sh
  xcrun notarytool submit dist/Budget-*.dmg --keychain-profile <profile> --wait
  xcrun stapler staple dist/Budget-*.dmg
  ```

- **Windows:** the installer is unsigned, so SmartScreen warns on first run. Sign
  `dist\Budget\Budget.exe` before packaging, and the resulting Setup.exe, with `signtool` and
  an Authenticode certificate to remove the warning.

## Runtime behaviour of installed builds

- The app opens in a native window: WebKit on macOS, the WebView2 runtime on Windows, Qt
  WebEngine (bundled) on Linux. WebView2 is preinstalled on Windows 10/11.
- The server listens on `127.0.0.1`, on a free port between 8000 and 8999.
- Data is kept in `~/Library/Application Support/Budget` (macOS), `%APPDATA%\Budget` (Windows)
  or `~/.local/share/budget` (Linux). It is not removed on uninstall.
- If no console is attached (windowed Windows build), the log goes to `budget.log` in the
  data folder.
- Only one instance can use a data folder at a time. A second launch exits immediately.

## Verification status

- **macOS (arm64):** built and tested.
  - The DMG layout, the signature and the native window were checked.
  - The ECB download over HTTPS works.
  - All pages and exports work in the frozen app.
  - Closing the window shuts the app down.
- **Windows:** built on GitHub's `windows-latest` runner while a workflow still built the
  installers (2026-10-19, 51 MB); the installed app has not been tried on a Windows PC yet. Check
  the first build: start the installed app, open every page, and try one export.
- **Linux:** built and tested in Ubuntu 24.04 containers (arm64): the package installs and
  uninstalls with `install.sh`, the browser mode serves every page, and the Qt WebEngine window
  opens on a virtual display and shows the app, also on a system without the extra libraries
  bundled from the build machine. An x86_64 package built on Ubuntu 22.04 (2026-10-19, 273 MB)
  passed the library check in `build.py`; try it once on a desktop.
