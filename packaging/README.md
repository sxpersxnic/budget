# Building the installers

These steps produce stand-alone installers that bundle Python and every dependency. End users
need neither uv nor Python. Everything runs locally; no CI service is involved.

| Platform | Output | Build on |
| --- | --- | --- |
| macOS | `dist/Budget-<version>-macOS-<arch>.dmg` (contains `Budget.app`) | a Mac. The architecture matches the build machine: arm64 on Apple Silicon, x86_64 on Intel. |
| Windows | `dist/Budget-<version>-Windows-Setup.exe` (Inno Setup, per-user install) | a Windows 10/11 PC |

PyInstaller cannot cross-compile, so build each installer on its own operating system.

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

## What the build does

Both wrappers:

1. check the prerequisites;
2. run `uv sync --locked --group build --group dev`;
3. run the tests;
4. call `build.py`.

`build.py` itself can also be run directly:
`uv run --group build python packaging/build.py`.

The version comes from `pyproject.toml` (`[project] version`). Raise it before building a new
release.

| File | Purpose |
| --- | --- |
| `build.py` | Runs PyInstaller (`--windowed --onedir`, plus the NiceGUI static files and the COICOP data). Then, on macOS, sets the version in `Info.plist`, signs the app and creates the DMG with an *Applications* shortcut. On Windows, it runs Inno Setup. |
| `macos/build.sh` | One-command macOS build. |
| `windows/build.bat`, `windows/build.ps1` | One-command Windows build. |
| `windows/budget.iss` | Inno Setup script. Installs into `%LOCALAPPDATA%\Programs\Budget` (or Program Files when run as admin), with a Start-menu entry, an optional desktop icon and an uninstaller. |
| `launcher.py` | Frozen entry point. It calls `multiprocessing.freeze_support()` before the app is imported, because the native window runs in a child process. |
| `make_icons.py` | Regenerates `assets/icon.png`, `icon.icns` and `icon.ico`. |

Build output goes to `dist/` and intermediate files to `build/`. Both are git-ignored.

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

- The app opens in a native window: WebKit on macOS, the WebView2 runtime on Windows. WebView2
  is preinstalled on Windows 10/11.
- The server listens on `127.0.0.1`, on a free port between 8000 and 8999.
- Data is kept in `~/Library/Application Support/Budget` (macOS) or `%APPDATA%\Budget` (Windows).
  It is not removed on uninstall.
- If no console is attached (windowed Windows build), the log goes to `budget.log` in the
  data folder.
- Only one instance can use a data folder at a time. A second launch exits immediately.

## Verification status

- **macOS (arm64):** built and tested.
  - The DMG layout, the signature and the native window were checked.
  - The ECB download over HTTPS works.
  - All pages and exports work in the frozen app.
  - Closing the window shuts the app down.
- **Windows:** the scripts are written for Windows 10/11 but have not been run yet. Check the
  first build on a Windows PC: start the installed app, open every page, and try one export.
