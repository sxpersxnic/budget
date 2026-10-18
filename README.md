# Budget

A local personal-budgeting app.

- Data is stored in plain CSV files on your computer.
- CHF is the fixed base currency.
- Exchange rates come from the ECB's daily reference rates.
- Expenditure categories follow COICOP 2018.

The installers bundle everything the app needs. You do **not** need Python, uv or any other tools to use it.

- [Install on macOS](#install-on-macos)
- [Install on Windows](#install-on-windows)
- [Your data, updates and uninstalling](#your-data-updates-and-uninstalling)
- [First steps](#first-steps)
- [Building the installers](#building-the-installers)
- [Run from source (developers)](#run-from-source-developers)

## Install on macOS

Requirements: macOS 11 (Big Sur) or later. The installer must match your Mac: an installer
built on an Apple Silicon Mac (`…-macOS-arm64.dmg`) runs on Apple Silicon Macs.

1. Double-click **`Budget-<version>-macOS-arm64.dmg`**. A window with **Budget** and
   **Applications** opens.
2. Drag **Budget** onto **Applications**.
3. Eject the "Budget" disk in the Finder sidebar. You can then delete the `.dmg` file.
4. Open **Budget** from Applications, Launchpad or Spotlight.

**First launch:** the app is not notarized by Apple. If macOS says it cannot verify that
"Budget" is free of malware:

1. Click **Done** (or **OK**).
2. Open **System Settings → Privacy & Security**.
3. Scroll to **Security**, and next to *"Budget" was blocked…* click **Open Anyway**.
4. Confirm with your password or Touch ID.

You only have to do this once. Later launches open the app directly.

## Install on Windows

Requirements: Windows 10 or 11, 64-bit. The app uses Microsoft Edge WebView2, which is
included in Windows 10 and 11.

1. Double-click **`Budget-<version>-Windows-Setup.exe`**.
2. If SmartScreen shows *"Windows protected your PC"*, click **More info → Run anyway**.
   The installer is not code-signed.
3. Follow the wizard:
   - Choose the language.
   - Keep the suggested folder.
   - Optionally tick *Create a desktop shortcut*.
   - Click **Install**.

   No administrator rights are needed. The app installs for your user account only.
4. Leave **Launch Budget** ticked and click **Finish**. Afterwards, start the app from the
   **Start menu → Budget** (or the desktop shortcut).

## Your data, updates and uninstalling

The app opens in its own window and keeps all data in your user folder:

| System | Data folder |
| --- | --- |
| macOS | `~/Library/Application Support/Budget` (in the Finder: **Go → Go to Folder…**) |
| Windows | `%APPDATA%\Budget` (type it into the Explorer address bar) |

- **Backup:** copy the data folder. It contains your CSV files and settings.
- **Update:** install the new version over the old one:
  - macOS: drag it to Applications and choose **Replace**.
  - Windows: run the new Setup.exe.

  Your data stays untouched.
- **Uninstall:**
  - macOS: move **Budget** from Applications to the Bin.
  - Windows: **Settings → Apps → Installed apps → Budget → Uninstall**.

  The data folder is kept on purpose. Delete it by hand if you want to remove your data too.
- **Internet:** only needed for the daily exchange-rate download. Offline, the app uses the
  last downloaded rates.
- **Privacy:** the app only accepts connections from your own computer. Nothing is uploaded.
- **One window at a time:** opening the app a second time while it is running does nothing.
  Switch to the window that is already open.

## First steps

1. **Settings:**
   - Add your accounts, each with its currency.
   - Add your income categories.
   - Choose the budget horizon and the display currency.
2. **Budget overview → Add budget recording:** enter each account's balance at the end
   of last month.
3. **Add transaction:** record expenditures (with a COICOP category or taxes), income,
   transfers and currency exchanges. Tick *Advance payment* when part of a payment was
   for someone else. Record their repayment later as income with
   *Repayment of an advance payment*.
4. **Add budget entry:** plan recurring income and expenditure. Use "Forever" for open-ended
   budgets.
5. **Overview / Budget overview:** compare actual against budget, check the projected
   wealth, build charts, and export statements as Excel or PDF.

## Building the installers

Each installer is built locally on its own operating system. A Mac cannot build the Windows
installer, and Windows cannot build the macOS one. Building needs
[uv](https://docs.astral.sh/uv/), which downloads Python and every dependency automatically.
Users of the finished installer do not need it.

| Installer | Build on | Prerequisites | Command |
| --- | --- | --- | --- |
| macOS `.dmg` | a Mac | uv (`brew install uv`) | `./packaging/macos/build.sh` |
| Windows `Setup.exe` | a Windows 10/11 PC | uv (`winget install --id astral-sh.uv -e`) and Inno Setup 6 (`winget install --id JRSoftware.InnoSetup -e`) | double-click `packaging\windows\build.bat` |

Each build script:

1. checks the prerequisites;
2. installs the locked dependencies;
3. runs the tests;
4. writes the installer to `dist/`.

A build takes 2–5 minutes. The version number comes from `pyproject.toml`. Signing and other
details are in [packaging/README.md](packaging/README.md).

## Run from source (developers)

```bash
uv sync
uv run budget                 # opens http://127.0.0.1:8080 in the browser
```

| Option | Effect |
| --- | --- |
| `--native` / `--no-native` | Native window instead of the browser. Needs `uv sync --extra native`; it is the default for installed builds. |
| `--port 8090` | Use a different port. |
| `--data-dir ~/Documents/budget-data` | Store the CSV files elsewhere (default: `./data`). |
| `--no-show` | Don't open a browser tab. |
| `--host 0.0.0.0` | Listen on all network interfaces. The default is localhost only. The app has no login, so use this only on a trusted network. |

Checks:

```bash
uv run pytest                                   # unit tests
uvx ruff check . && uvx ruff format --check .   # lint and formatting (config in pyproject.toml)
```

- The specification is in [docs/Description_Budgeting_App.md](docs/Description_Budgeting_App.md).
- The implementation (data model, formulas, decisions) is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Code layout: `src/budget_app/{domain,storage,services,ui}`.
