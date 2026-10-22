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
- [Sources](#sources)

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
- **Undo:** after saving or deleting, the confirmation at the bottom offers **Undo** for a few
  seconds. **Settings → Data → Change History** lists the last changes, each with **Undo**. An
  undo is refused when the records were changed again afterwards, so it never overwrites later
  work.
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
   (with a COICOP category or taxes), income, transfers, currency exchanges and
   revaluations (a change in an account's value, such as a gain on an investment). Turn on
   *Partly paid for others* when part of a payment was for someone else. Record their
   repayment later as income with *Repays an advance payment*. Under **Items** you can list
   what you bought (for example a grocery receipt) with quantity and price; the Transactions
   search then also finds a purchase by item name. Shortcuts: type a sum such as `12.50+8.30`
   into an amount, press **N** for a new transaction and **⌘↩** (Ctrl+Enter) to save, save the
   usual ones as **templates** (bookmark button), or **Duplicate** an existing transaction.
   The category field shows everyday names ("Groceries · 01.1") and your recently used
   categories first, and its search also knows shops and brands: type "Migros", "SBB" or "Netflix".
   It also finds a category by what it includes ("quinoa" finds 01.1.1.1 Cereals); hover over a
   category, or tap its ⓘ on a touch screen, to see what it includes and excludes.
   Expenditures and income have a **Payee** (who you paid, or who paid you): pick one of the
   suggestions (your recent payees come first) or type a new name. Once the payee is known, the
   app suggests a category below the category field, with the reason ("Used 14 times for this
   payee"); click **Use …** to take it, or choose another one.
   **Tags** label transactions across categories ("Holiday Italy", "Renovation"): pick existing
   ones or type a new name and press Enter; it becomes a tag when you save. A transaction can have
   several.
4. **Transactions → Import…** (optional): bring in a CSV export from your e-banking
   instead of typing. Choose the account, check the detected columns, give the new rows a
   category and import. The column settings can be saved per bank; transactions already
   imported are recognised and skipped. The payee is filled in from the bank text
   ("Einkauf Debitkarte 12.10.2026 MIGROS ZUERICH HB 1234" becomes "Migros"); double-click it in the
   preview to change it, and the app remembers your choice for that text. A camt.053 or camt.054
   statement (XML, the Swiss e-banking standard) needs no column step at all, and the app remembers its account by IBAN.
   When the file shows the account balance, the review step also offers each month's closing
   balance to record (the month the file ends inside stays unticked).
   Rows without a category get a suggested one, shown in italics with the reason beside it: from your
   rules, or the category the payee had most often. Check them before importing (importing keeps what
   you see), choose another category for some rows, or **Clear Suggestions**.
   To have a payee or bank text always suggest the same category, select a transaction and choose
   **Always Categorize Like This…** (also in the edit sheet): adjust the text it looks for ("migros")
   and save the rule. Taking a suggestion never creates a rule on its own.
   To tidy up several transactions at once, select them in the list and choose **Edit**: change
   their category, payee, account, tags (add or remove) or note (replace, append or clear) in one step,
   which one **Undo**
   reverts. Rows a change doesn't fit (a transfer has no category) keep what they have.
5. **Budget Plan → New Budget Entry:** plan recurring income and expenditure. Use "Forever"
   for open-ended budgets.
6. **Recurring** (sidebar): enter rent, premiums, salary or subscriptions once. When one is
   due it is listed for one-click booking (change the amount first if it varies, or skip it);
   turn on *Book automatically* for fixed amounts. *Transactions → Make Recurring…* starts a
   series from a transaction you already entered.
7. **Categories** (sidebar): look up a COICOP category by code, name or what it includes; the ⓘ
   beside a name shows what the category includes, also includes and excludes. The ones you used
   show what you spent in them this year.
   **Payees** (sidebar) lists everyone you paid or who paid you, with the number of transactions and
   the amounts. Rename a payee, merge several into one (tick them, then **Merge…**) or delete one you
   no longer use; each can be undone. Imports keep using the name you chose. On **Transactions**,
   search for a payee or choose one under **Filters**. **Payees → Rules…** (or **Settings → Rules…**)
   lists your categorization rules, to add, change or delete them. When several rules match, the one
   with the longest text wins.
   **Tags** (sidebar) lists your tags with their number of transactions and what was spent and
   received. Click a tag to see its transactions. Give a tag a **budget** (piggy bank button: an
   amount, optionally a period such as the dates of a holiday) and a bar shows the tagged spending
   against it; the part paid for others and income don't count. Rename or recolour a tag, merge
   several, or delete one (it is taken off its transactions; **Undo** brings it back). On
   **Transactions**, filter by tag under **Filters** or search for a tag name, and *Add Chart →
   Spending by Tag* on the Overview compares the tags over a period.
8. **Overview / Budget:** compare actual against budget, check the projected wealth, build
   charts, and export statements as Excel or PDF (share button on the Budget page).
   Once an account has an asset class other than cash, the Overview shows your wealth **by asset
   class**, and *Add Chart → Wealth by Asset Class* shows it month by month.
9. **Investments** (sidebar, optional): give an account the asset class *Securities* or *Crypto*
   in **Settings** (edit the account), then add its holdings (name, optional ISIN or symbol) on the
   Investments page. An account that already has transactions or balances is **converted**: choose
   the conversion month and the holdings it starts with (quantity and price on the first day of that
   month); its balances before that month stay as they were, and the difference to the last one is
   booked as *Capital gains on investment*, so the month still reconciles. Record
   **Buy**, **Sell** and **Dividend** with the cash account the money comes from or goes to (in the
   holding's currency), and enter prices with **Update Price** or **Paste Prices…** (one line per
   price: date, holding name/ISIN/symbol and price, as copied from your bank's or broker's price
   list; nothing is fetched online). A dividend is entered gross with its withholding tax (35 %
   prefilled in CHF accounts, editable); the cash account receives the net. The app books the
   transfers, fees (category 12.2.9.9), dividends (gross in the income category *Dividends*, the
   withholding tax under *Taxes*) and every price change (*Capital gains on investment*) for you, so
   a securities or crypto account is always worth quantity × the latest price and needs no balances
   recorded. Each holding shows its value and gain (value − money put in + net dividends − fees);
   click it for its history, where trades and prices can be edited or deleted. The page also sums
   the Swiss withholding tax you can reclaim with your tax return, per year. Pillar 2,
   pillar 3a, real estate and other assets keep their recorded month-end balances.

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

- The COICOP notes (`src/budget_app/resources/coicop_notes.json`) are rebuilt from the UN workbook
  with `scripts/coicop_notes.py` (usage in the script); see [Sources](#sources).
- The specification is in [docs/Description_Budgeting_App.md](docs/Description_Budgeting_App.md).
- The implementation (data model, formulas, decisions) is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Code layout: `src/budget_app/{domain,storage,services,ui}`.

## Sources

- **Exchange rates:** euro foreign exchange reference rates of the European Central Bank.
- **COICOP 2018 codes and names:** Eurostat SDMX codelist `ESTAT/COICOP18`; the German, French
  and Italian names are the app's own translations.
- **COICOP notes** (what each category includes, also includes and excludes): United Nations
  Statistics Division, *Classification of Individual Consumption According to Purpose (COICOP)
  2018*, Statistical Papers Series M No. 99, from its "Final structure (Excel)" at
  <https://unstats.un.org/unsd/classifications/Econ> (retrieved on 2026-10-21). The English text is
  the UN's; the German, French and Italian translations are the app's own and unofficial, as
  the app says beside them.
