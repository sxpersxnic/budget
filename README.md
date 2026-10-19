# Budget

A local personal-budgeting app.

- Data is stored in plain CSV files on your computer.
- CHF is the fixed base currency.
- Exchange rates come from the ECB's daily reference rates. A transaction is converted at the
  rate of its date, an account balance at the last rate of its month.
- Expenditure categories follow COICOP 2018.

The installers bundle everything the app needs. You do **not** need Python, uv or any other tools to use it.

**Download:** the installers are attached to each
[release on GitHub](https://github.com/sxpersxnic/budget/releases/latest), with a
`SHA256SUMS.txt` to check them:

- macOS: `Budget-<version>-macOS-arm64.dmg`
- Windows: `Budget-<version>-Windows-Setup.exe`
- Linux: `Budget-<version>-Linux-x86_64.tar.gz`

They are built automatically from the code in this repository (see
[Building the installers](#building-the-installers)). The first macOS installer, 0.1.0, is also
in the [`installers/`](installers/README.md) folder.

- [Install on macOS](#install-on-macos)
- [Install on Windows](#install-on-windows)
- [Install on Linux](#install-on-linux)
- [Update to a new version](#update-to-a-new-version)
- [Your data, backups and uninstalling](#your-data-backups-and-uninstalling)
- [First steps](#first-steps)
- [Building the installers](#building-the-installers)
- [Run from source (developers)](#run-from-source-developers)

## Install on macOS

Requirements: macOS 11 (Big Sur) or later. The installer must match your Mac: an installer
built on an Apple Silicon Mac (`…-macOS-arm64.dmg`) runs on Apple Silicon Macs.

1. Download **`Budget-<version>-macOS-arm64.dmg`** from the
   [latest release](https://github.com/sxpersxnic/budget/releases/latest) and double-click it. A window with **Budget** and **Applications** opens.
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

1. Download **`Budget-<version>-Windows-Setup.exe`** from the
   [latest release](https://github.com/sxpersxnic/budget/releases/latest) and double-click it.
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

## Install on Linux

Requirements: a 64-bit (x86_64) desktop Linux with glibc 2.34 or newer, for example Ubuntu
22.04, Debian 12, Fedora 36, Linux Mint 21 or later. The app window uses Qt WebEngine, which is
bundled. Desktop installations have the system libraries it needs; on a minimal system, install
`libxcb-cursor0` and `libnss3` (Debian and Ubuntu package names) if the window doesn't open.

1. Download **`Budget-<version>-Linux-x86_64.tar.gz`** from the
   [latest release](https://github.com/sxpersxnic/budget/releases/latest).
2. Unpack it and run the installer from a terminal:

   ```bash
   tar -xzf Budget-*-Linux-x86_64.tar.gz
   ./Budget-*-Linux-x86_64/install.sh
   ```

   No administrator rights are needed. The app installs for your user account only, into
   `~/.local/opt/Budget`, with a menu entry and the command `budget` (in `~/.local/bin`).
3. Start **Budget** from the application menu, or run `budget`. You can delete the downloaded
   and unpacked files afterwards.

## Update to a new version

Your installed version is shown at the bottom of the app's sidebar, for example
*Budget 0.1.0*. Newer installers are on the
[releases page](https://github.com/sxpersxnic/budget/releases).

**Your data is kept.** Updating replaces only the program. Your records live in the
[data folder](#your-data-backups-and-uninstalling), which neither installer touches. As an extra
safeguard, the first start of a new version copies your data files to
`backups/<date>_<old>_to_<new>/` inside the data folder before it reads them. The ten most
recent of these copies are kept.

**macOS**

1. Quit Budget (**Budget → Quit** or ⌘Q). A running app can't be replaced.
2. Open the new `.dmg`. The window title shows its version, for example *Budget 0.2.0*.
3. Drag **Budget** onto **Applications** and click **Replace**.
4. Eject the disk and open Budget. macOS treats every new version as a new download, so
   confirm it once more with **Open Anyway**, as on the [first launch](#install-on-macos).

**Windows**

1. Run the new `Budget-<version>-Windows-Setup.exe`. If Budget is open, Setup offers to
   close it.
2. Setup recognises the existing installation and keeps its folder, language and shortcuts.
   The last page before installing says *Update: Budget 0.1.0 will be replaced by
   version 0.2.0*. Click **Install**, then **Finish**.

**Linux**

1. Quit Budget.
2. Unpack the new `.tar.gz` and run its `install.sh`, as for the
   [first installation](#install-on-linux). It replaces the program folder and keeps your data.

## Your data, backups and uninstalling

The app opens in its own window and keeps all data in your user folder:

| System | Data folder |
| --- | --- |
| macOS | `~/Library/Application Support/Budget` (in the Finder: **Go → Go to Folder…**) |
| Windows | `%APPDATA%\Budget` (type it into the Explorer address bar) |
| Linux | `~/.local/share/budget` (or `$XDG_DATA_HOME/budget`) |

- **Backup:** **Settings → Data → Back Up Now…** saves all your records as one ZIP file
  (`Budget-backup-<date>.zip`). Keep it somewhere safe, for example on another drive.
- **Restore:** **Settings → Data → Choose Backup…** replaces all records with those of a backup
  file. The app also keeps its own copies under **Automatic Backups**: one before each update and
  one before each restore, the ten most recent. Each can be restored with **Restore…**. A restore
  first copies the current records to the backups folder, so it can be undone the same way.
- **Export:** the Transactions and Budget Plan lists export what they show (with their filters)
  as CSV or Excel (share button). **Settings → Data → Export as JSON…** writes every record into
  one JSON file for other programs; it can't be restored.
- **Older versions:** opening your data with an older version is safe too; it keeps fields it
  doesn't know about instead of dropping them.
- **Update:** see [Update to a new version](#update-to-a-new-version). Your data stays
  untouched.
- **Uninstall:**
  - macOS: move **Budget** from Applications to the Bin.
  - Windows: **Settings → Apps → Installed apps → Budget → Uninstall**.
  - Linux: run `~/.local/opt/Budget/install.sh --uninstall`.

  The data folder is kept on purpose. Delete it by hand if you want to remove your data too.
- **Internet:** only needed for the daily exchange-rate download. Offline, the app uses the
  last downloaded rates.
- **Privacy:** the app only accepts connections from your own computer. Nothing is uploaded.
- **One window at a time:** opening the app a second time while it is running does nothing.
  Switch to the window that is already open.

## First steps

1. **Settings:**
   - Choose the language (English, Deutsch, Français or Italiano; by default it follows your
     system).
   - Add your accounts, each with its currency.
   - Add your income categories.
   - Choose how far ahead to plan (*Plan Until*) and the display currency.
2. **Budget → Record Balance:** enter each account's balance at the end of last month.
3. **New Transaction** (top of the sidebar, available on every page): record expenditures
   (with a COICOP category or taxes), income, transfers and currency exchanges. Turn on
   *Partly paid for others* when part of a payment was for someone else. Record their
   repayment later as income with *Repays an advance payment*. Under **Items** you can list
   what you bought (for example a grocery receipt) with quantity and price; the Transactions
   search then also finds a purchase by item name.
4. **Transactions → Import…** (optional): bring in a CSV export from your e-banking
   instead of typing. Choose the account, check the detected columns, give the new rows a
   category and import. The column settings can be saved per bank; transactions already
   imported are recognised and skipped.
5. **Budget Plan → New Budget Entry:** plan recurring income and expenditure. Use "Forever"
   for open-ended budgets.
6. **Overview / Budget:** compare actual against budget, check the projected wealth, build
   charts, and export statements as Excel or PDF (share button on the Budget page).

## Building the installers

**Automatically (GitHub Actions):** every push to `main` builds all three installers after the
checks pass; they are attached to the workflow run as artifacts. To publish a release, raise
`version` in `pyproject.toml`, commit, then tag and push:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

The tag must match the version. The [Installers workflow](.github/workflows/installers.yml) then
creates the GitHub release with the three installers and `SHA256SUMS.txt`.

**Locally:** each installer is built on its own operating system. A Mac cannot build the Windows
installer, and Windows cannot build the macOS one. Building needs
[uv](https://docs.astral.sh/uv/), which downloads Python and every dependency automatically.
Users of the finished installer do not need it.

| Installer | Build on | Prerequisites | Command |
| --- | --- | --- | --- |
| macOS `.dmg` | a Mac | uv (`brew install uv`) | `./packaging/macos/build.sh` |
| Windows `Setup.exe` | a Windows 10/11 PC | uv (`winget install --id astral-sh.uv -e`) and Inno Setup 6 (`winget install --id JRSoftware.InnoSetup -e`) | double-click `packaging\windows\build.bat` |
| Linux `.tar.gz` | Linux x86_64, glibc 2.34+ | uv (`curl -LsSf https://astral.sh/uv/install.sh \| sh`) | `./packaging/linux/build.sh` |

Each build script:

1. checks the prerequisites;
2. installs the locked dependencies;
3. runs the tests;
4. writes the installer to `dist/`. The macOS and Windows installers are also copied, with a
   SHA-256 checksum, to `installers/<os>/`.

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

With [just](https://just.systems) installed, the common tasks are one command each
(`just` lists them all):

```bash
just setup        # dev dependencies and git hooks
just dev          # desktop window that restarts when a file in src/ changes
just dev-web      # the same in the browser, http://127.0.0.1:8080
just check        # lint, formatting, type check, tests and coverage
just build        # installer for this OS (see Building the installers)
```

Without just:

```bash
uv sync --group dev && uv run lefthook install   # dev dependencies and git hooks
uv run pytest --cov                                # tests with coverage (at least 87 %)
uv run ruff check . && uv run ruff format --check .  # lint and formatting (config in pyproject.toml)
```

Tests in `tests/ui/` drive the real pages in NiceGUI's simulated client, without a browser.
The git hooks ([lefthook.yml](lefthook.yml)) check lint and formatting before each commit, and
types, tests and coverage before each push.

- The specification is in [docs/Description_Budgeting_App.md](docs/Description_Budgeting_App.md).
- The implementation (data model, formulas, decisions) is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Code layout: `src/budget_app/{domain,storage,services,ui}`.
