# Building the installers

These steps produce stand-alone installers that bundle Python and every dependency. End users
need neither uv nor Python.

| Platform | Output | Build on |
| --- | --- | --- |
| macOS | `dist/Budget-<version>-macOS-<arch>.dmg` (contains `Budget.app`) | a Mac. The architecture matches the build machine: arm64 on Apple Silicon, x86_64 on Intel. |
| Windows | `dist/Budget-<version>-Windows-Setup.exe` (Inno Setup, per-user install) | a Windows 10/11 PC |
| Linux | `dist/Budget-<version>-Linux-<arch>.tar.gz` (app folder and `install.sh`, per-user install) | Linux with glibc 2.34+ (the CI uses Ubuntu 22.04) |

PyInstaller cannot cross-compile, so each installer is built on its own operating system.

## GitHub Actions

Two workflows in [`.github/workflows/`](../.github/workflows):

- **CI** (`ci.yml`): lint, formatting, types, tests and the coverage gate on every push to
  `main` and every pull request.
- **Installers** (`installers.yml`): runs CI first, then builds the three installers on
  `macos-latest` (arm64), `windows-latest` and `ubuntu-22.04`.
  - Every push to `main`, and *Run workflow* on the Actions tab: the installers are attached to
    the run as artifacts (kept 30 days).
  - A tag `v<version>`: also creates the GitHub release with the installers and
    `SHA256SUMS.txt`. The tag must match `[project] version` in `pyproject.toml`, or the
    release job stops.

The CI builds are unsigned, like the local ones (see [Signing](#signing-optional)). Code-signing
secrets can be added to the build job later.

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

The build machine needs the X11 and EGL libraries Qt loads while PyInstaller analyses it; the
package list is in the *Qt libraries for the build* step of `installers.yml`.

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

1. Raise `version` in `pyproject.toml`, for example `0.1.0` → `0.2.0`, and run `uv sync`.
2. Build on each platform (`just build`, or the wrappers above).
3. Commit `pyproject.toml`, `uv.lock` and the new files in `installers/`.

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

Build output goes to `dist/` and intermediate files to `build/`. Both are git-ignored. The
finished macOS or Windows installer is also copied to [`installers/macos/`](../installers) or
`installers/windows/`, replacing any older version and refreshing `SHA256SUMS.txt`. The Linux
package (about 250 MB with Qt WebEngine) exceeds GitHub's file size limit and is published only
as a release asset.

To keep the DMG under GitHub's 50 MiB recommended file size, `build.py` removes JavaScript
payloads the app never loads: unused NiceGUI element bundles, and Plotly's Jupyter widget and
offline-HTML scripts. NiceGUI renders charts with its own plotly.js.

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
- **Windows:** built by CI on `windows-latest` (first successful run 2026-10-19, 51 MB); the
  installed app has not been tried on a Windows PC yet. Check the first build: start the installed app, open every page, and try one
  export.
- **Linux:** built and tested in Ubuntu 24.04 containers (arm64): the package installs and
  uninstalls with `install.sh`, the browser mode serves every page, and the Qt WebEngine window
  opens on a virtual display and shows the app, also on a system without the extra libraries
  bundled from the build machine. The x86_64 package from CI (first successful run 2026-10-19,
  273 MB) passed the library check in `build.py`; try it once on a desktop.
