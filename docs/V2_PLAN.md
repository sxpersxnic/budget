# Budget v2 — Rust, GPUI, double-entry, instant

## Context

v1.0.0 has shipped. It is a Python app: NiceGUI inside pywebview, frozen with PyInstaller. It has 47 features, about 31k lines in `src/` and about 1,300 tests.

It works, but the stack has hit its limits:
- **Speed and size:** it is a webview UI over a local server, with white-flash work-arounds (decision 47) and installers bloated by OCR (+60–70 MB).
- **Money:** amounts are `float` everywhere, with about 66 ad-hoc `round()` calls.
- **Storage:** 22 CSV tables that are fully rewritten on each change, with no schema versioning.

v2 is a next-generation rewrite tailored to personal use, for two users: the owner on macOS and one other person on Windows.

Decisions made with the user:
- **UI:** GPUI + gpui-component. It is the fastest Rust UI available: GPU-rendered, with virtualized tables and built-in charts.
- **Platforms:** macOS (Apple Silicon) and Windows. Linux only for CI, not as a shipped target.
- **Next-gen pillars:**
  1. **Instant everything:** command palette, live fuzzy search, keyboard-first, cold start under 200 ms.
  2. **A double-entry ledger core** with an append-only event journal.
  3. **A twelve-factor app**, adapted to a desktop app (see below).
  4. **Calm by design:** the app should feel like its own world, made for the people who use it. Money is stressful, so every screen should say: you're fine, here is the one thing that matters. A new design language, not v1's look ported (see below).
- **Work on a separate branch:** the rewrite lives on a long-lived `v2` branch, and `main` stays v1 until cutover.

Accepted trade-offs:
- GPUI's screen-reader support is weak. We keep keyboard access, contrast and font scaling at v1's AA level, but screen-reader support is out of scope.
- GPUI's API is pre-1.0, so we pin exact versions.

## Branching and workflow

- Cut a long-lived **`v2`** branch from `main` (c40dd64) and push it to origin.
- Each milestone below goes into its own worktree under `.claude/worktrees/v2-mN-<slug>`, on branch `v2/mN-<slug>`. Each milestone is one PR into `v2` (squash-merged); the user merges.
  - When a stacked PR depends on an earlier one that was squash-merged, merge `v2` into it first.
- `main` takes v1 hotfixes only. Every v1.x hotfix is merged into `v2` as well.
- At cutover (M8), one PR `v2 → main`. v1 lives on afterwards on a `v1.x` branch and the `v1.0.0` tag.
- **Repository layout on `v2`:**
  - A Cargo workspace at the root: `Cargo.toml` and `crates/`.
  - The Python tree (`src/`, `tests/`) stays read-only until M8: the v1 importer tests use its fixtures and demo data. At M8 it moves to `legacy/` or is deleted.

## Architecture

```
crates/
  money/        Money (i64 minor units + Currency), Rate, rounding rules. No IO.
  ledger/       Accounts tree, Transaction = balanced Postings, commodities/FX cost,
                invariants, commands → events, projections (balances, budgets).
  swiss/        COICOP 2018 catalogue (embedded), QR-bill (SIX 2.3), camt.053/054,
                bank-text cleaning, ECB FX, withholding-tax rules, tax sections.
  store/        SQLite (rusqlite, bundled-sqlcipher), migrations, event journal,
                snapshots, undo/redo, backup.
  index/        In-memory query engine: nucleo fuzzy search, aggregates per
                month × category × account, kept current from journal events.
  import/       CSV heuristics (encoding/delimiter/date/decimal), per-bank profiles,
                categorizer (rules → payee history → keywords), dedupe.
  v1-migrate/   Reads a v1 data folder (incl. Fernet+scrypt decryption) → events.
  app/          GPUI + gpui-component UI. Owns no domain state.
  cli/          `budget` binary: admin processes (twelve-factor XII).
```

### Ledger model (pillar 2)

- **Money:** `i64` minor units plus an ISO currency, with the exponent taken from the ISO 4217 table. Splits allocate to the cent (largest remainder), as v1 does (`domain/splits.py`).
- **Accounts:**
  - `Assets:*`, `Liabilities:*` and `Equity:*` work as in any double-entry ledger.
  - `Income:*` uses v1's income categories.
  - `Expenses:<COICOP code>` covers the full four-level COICOP tree.
  - Payees, tags and the `series_id` are dimensions on postings, not accounts.
- **v1 transaction types as postings:**

  | v1 type | Postings |
  |---|---|
  | expense | asset/liability → expense |
  | income | income → asset |
  | transfer | asset → asset |
  | exchange | two commodities at the ECB cost; the bank spread goes to `Equity:Revaluation:FX` (as in v1 F44) |
  | revaluation (capital gains, FX) | `Equity:Revaluation:*`, which stays out of net income as in v1 |
  | loan interest | `Expenses:12.2.1` |
  | shared-expense advance | a receivable `Assets:Receivable:<person>` |

- **Budgets** are a separate plan ledger, using the same account tree and series semantics as v1's `BudgetEntry`. Projection rule: per category, the higher of actual and budget (v1 F47).
- **Event journal:**
  - Every user action is a command, validated into one or more events and appended to the `journal` table. The current state is a projection of the journal.
  - **Undo** appends a compensating event. As in v1, it is refused if the records changed in between.
  - The journal gives an audit trail and allows sync to be added later without redesigning anything.
- **Plain-text export:** Beancount and hledger.

### Instant everything (pillar 1): budgets enforced in CI

| Metric | Budget |
|---|---|
| Cold start to the first interactive frame | < 200 ms on an M-series Mac, < 400 ms on pc-01 |
| Search results per keystroke, over 100k transactions | < 5 ms (nucleo, on a background executor) |
| Frame time in any view at 100k transactions | < 8 ms (virtualized `Table`) |
| A recalculation after any edit | < 16 ms, by updating the in-memory index incrementally instead of re-querying |

How the UI gets there:
- **⌘K / Ctrl+K command palette** for every action and place.
- **Every action has a shortcut;** v1's shortcuts (N, ⌘↩, /, ←/→, 1–9, ?) stay.
- **Startup work happens in the background:**
  - The journal tail is replayed from the latest snapshot. Snapshots are written every N events.
  - The window opens first, and the data streams in after it.

### Twelve-factor, adapted to a desktop app (pillar 3)

| Factor | v2 rule |
|---|---|
| I. Codebase | One repository and Cargo workspace; the deploys are the mac and win builds |
| II. Dependencies | `Cargo.lock`, `rust-toolchain.toml`, SQLite bundled; no system libraries needed |
| III. Config | Environment variables (`BUDGET_DATA_DIR`, `BUDGET_LOG`, `BUDGET_FX_URL`, `BUDGET_LOCALE`) over an optional `config.toml` over defaults (`figment`). No hard-coded paths. This replaces v1's profiles (F34): a profile is just a different `BUDGET_DATA_DIR`. |
| IV. Backing services | The store, the FX provider and the attachment store sit behind traits and are addressed by URL or path (`sqlite://…`, `ecb://`, `file://`), so they can be swapped in tests |
| V. Build, release, run | CI builds the artifact; a release is the artifact plus a tag plus a changelog; running is just launching it. Nothing is built on the user's machine. |
| VI. Processes | No authoritative state in the UI; all state is in the store and journal |
| VII. Port binding | The optional `budget serve` exposes a local HTTP API (for the phone quick-add later); it is off by default |
| VIII. Concurrency | Typed background workers (import, FX, index, backup) on GPUI's background executor |
| IX. Disposability | SQLite WAL, every command in one transaction, crash-safe at any moment, fast start and shutdown |
| X. Dev/prod parity | Same binary and same SQLite in development, CI and the release. CI runs on macOS and on the Windows runner (pc-01). |
| XI. Logs | `tracing` events go to stderr. The GUI also writes a rolling log file in the data folder, because a GUI process has no visible stdout. No custom log management. |
| XII. Admin processes | The `budget` CLI uses the same crates: `migrate-v1`, `check` (ledger invariants), `backup`, `restore`, `fx update`, `export beancount`, `demo` |

### Calm by design (pillar 4)

The test for every screen: **after a glance, do you feel more settled than before?**

**Principles**
1. **Answer first, numbers second.** Every place opens with one plain sentence before any table or chart. For example: *"You're on track. 1,240 left to spend until 31 October."* or *"Nothing is due this week."* The Overview becomes **Today**.
2. **Fewer numbers on screen.** Summaries round to whole francs; cents appear in detail views and on hover. At most one chart per place unless you ask for more. The chart builder is an opt-in tool, not a dashboard.
3. **No alarm colours for normal life.**
   - Red is reserved for real errors: data that doesn't reconcile, or a payment that failed.
   - Going over plan uses a soft amber with neutral wording: *"40 over plan"*, never *"OVERSPENT"*.
   - The comparison is framed against your own plan, never against a failing grade.
4. **Kind language.**
   - The everyday names from v1 stay.
   - Copy is never written to blame.
   - Empty states reassure and offer one next step.
   - Errors say what happened, what is safe, and what to do: *"Nothing was lost."*
5. **Gentle, finite to-dos.**
   - The Due list is grouped as *This week* and *Later*, with a calm "all clear" state.
   - No red badge counters, streaks or guilt mechanics.
   - When a goal is reached, a quiet note marks it; no confetti.
6. **Privacy calm.** ⌘. / Ctrl+. blurs every amount at once, for when someone is looking over your shoulder or you just don't want to see numbers today.
7. **A weekly check-in ritual.** A two-minute guided flow:
   1. Sort the uncategorized items.
   2. Confirm what is due.
   3. Read one sentence about the week.

   The ritual is optional, but it is the intended rhythm, so finance becomes a short habit rather than a chore.

**The look: its own world**
- **Palette:**
  - Light theme: warm paper (not white).
  - Dark theme: deep ink (not black).
  - One quiet accent, for example sage or moss.
  - Muted, low-chroma category colours.
  - Every pair keeps AA contrast, checked in CI as `test_contrast.py` does in v1.
- **Type:**
  - One humanist sans with **tabular numerals** for amounts, so columns align and don't jitter.
  - Generous line height and an 8 pt spacing grid.
  - The UI scales with system font settings.
- **Space:** wide margins and a lot of air. One primary action per view; secondary actions sit behind ⌘K.
- **Motion:**
  - 200–300 ms ease-out, nothing bouncy.
  - Numbers cross-fade instead of counting up.
  - Reduced motion is honoured.
  - At 120 fps, GPUI makes calm motion cheap.
- **Texture:** a subtle surface hierarchy (paper, card, sheet) instead of borders everywhere. Optionally a very faint grain on the background.
- **A name and identity:** v2 gets its own name, icon and wordmark, decided in milestone D.

## What carries over from v1

- **Port faithfully:**
  - COICOP data and everyday names: `src/budget_app/resources/`
  - QR-bill rules: `domain/qrbill.py`
  - camt.053/054 import: `services/camt.py`, `import_balances.py`
  - CSV heuristics: `services/importer.py`
  - Categorizer: `domain/rules.py`, `domain/payees.py`, `everyday.py`
  - Splits: `domain/splits.py`
  - Loans, investments, tax: `domain/loans.py`, `investments.py`, `tax.py`
  - FX rules: `domain/fx.py`, and ARCHITECTURE §4.1
  - The Due list
  - The 7 places as a starting point (milestone D may rename them, e.g. Overview → Today) and the everyday names
  - From `ui/theme.py`: only the contrast rules. The look itself is new (pillar 4).
- **The v1 tests are the spec.** Port the domain test cases from `tests/` (not `tests/ui/`) as table-driven Rust tests.
- **Deferred:**
  - F04 receipt OCR: size cost; revisit with a native OCR API
  - F26 cloud-folder sync: the journal makes a proper merge possible later
  - F35 phone companion: comes back through `budget serve`
  - F33 update check
- **Dropped:**
  - F34 profiles: replaced by the config rule (factor III)
  - F30 onboarding polish: `budget demo` replaces the sample data
  - F37 as a separate COICOP page: it becomes part of Categories
- **Languages:** en and de at first, using Fluent (`fluent-bundle`). Convert v1's `resources/i18n/*.json` to `.ftl` with a script, so fr and it are cheap to add if the second user needs them.

## Milestones (one PR into `v2` each)

**M0 — Foundations**
- Create the `v2` branch and the workspace.
- Pin exact versions in `rust-toolchain.toml`, `gpui` and `gpui-component`.
- Add the `figment` config, `tracing`, and a skeleton `budget` CLI.
- Add a GPUI window that opens on both operating systems.
- Add CI `.github/workflows/v2.yml`:
  - checks: fmt, clippy `-D warnings`, nextest, a coverage gate with `cargo-llvm-cov` of at least 85 % on the non-UI crates
  - platforms: macOS plus the Windows runner on pc-01
- Exit: the window measures under the cold-start budget on both operating systems.

**M1 — money + ledger**
- Types, balancing invariants, FX cost basis, the command → event model and projections.
- `proptest`: every transaction balances, every split sums to its total, and replaying the events reproduces the state.

**M2 — store**
- SQLite schema and migrations (`rusqlite_migration`), the journal table, snapshots and undo/redo.
- SQLCipher encryption with a password and a recovery key.
- Backup through the SQLite online-backup API plus attachments, packed as a ZIP.
- The CLI commands `check`, `backup` and `restore`.

**M3 — v1 migration** (the gate before any UI work)
- `budget migrate-v1 <dir>`:
  - Reads all 22 CSV tables plus the JSON files, and decrypts the `BUDGET-ENCRYPTED-1` files.
  - Converts the float amounts to minor units.
  - Writes a **reconciliation report**: for every account × month and every category × month, compare the v1 total with the v2 total. The differences must be 0, or each one must be listed and explained.
- Test it on v1's demo data (`services/demo.py`), then on the owner's real data, locally and never committed.

**D — Design language** (runs alongside M1–M3; M4 cannot start until the owner approves it)
- A mood board and the tokens:
  - palette (light and dark), type scale, spacing, radii, elevation and motion curves
  - the copy voice guide, with before/after examples taken from v1 strings
- Key screens:
  - Today (with the Due list)
  - Month
  - Transactions (search typed into it)
  - the weekly check-in
  - an empty state and an error state
  - the amounts-hidden mode
  - each in light and dark
- The v2 name, icon and wordmark.
- Delivered as a clickable HTML prototype for review. Figma optional.
- Exit: the owner signs it off, and ideally the second user has looked at it too. The tokens are then frozen into `crates/app/src/theme.rs`.

**M4 — UI shell and instant search**
- The places as settled in D, the ⌘K palette, the keymap, the design tokens from D and Fluent i18n.
- A virtualized Transactions table and nucleo search over the `index` crate.
- Measure against the budgets with a 100k-transaction dataset from `budget demo --size 100k`.

**M5 — Everyday flows**
- Entering, editing, splitting, bulk-editing and undoing transactions.
- The Budget month view, and the Overview page with the Due list.
- Import (CSV, camt) with auto-categorization, QR bills (PDF/image through `rxing` and `pdfium-render`), payees, rules and tags.

**M6 — Plan and Wealth**
- Plan: plan series, goals, scenarios and projection.
- Wealth: accounts, investments, loans, shared expenses, FX revaluation, and the ECB rate fetch.

**M7 — Reports and exports**
- Year in Review and the Swiss tax summary.
- Exports: CSV, xlsx (`rust_xlsxwriter`), PDF (`typst` as a library), and Beancount/hledger.

**M8 — Packaging and cutover**
- Build the macOS `.app` and `.dmg` and a Windows MSI/NSIS installer with `cargo-packager`. Code signing is still an open decision; v1's installers are unsigned.
- On first launch, detect a v1 data folder and run the migration, after making a backup.
- Merge `v2 → main` and tag `2.0.0`.

## Verification

- **Correctness:**
  - `cargo nextest` on macOS and Windows in CI.
  - The ported v1 domain test tables.
  - The `proptest` ledger invariants.
  - `budget check` on the migrated data.
- **Migration:** the M3 reconciliation report on the demo data and on the real data shows zero unexplained differences, and its summary goes into the M3 PR.
- **Performance:**
  - `criterion` benchmarks for search, aggregation and replay against the 100k dataset, with CI failing on a regression of more than 10 %.
  - Cold-start timing, logged by `tracing` from process start to the first frame, on the Mac and on pc-01.
  - On pc-01, start the GUI through a scheduled task, because processes started over SSH die when the session ends.
- **Calm review:** at each UI milestone, screenshots of every place in light and dark go into the PR and are checked against D's prototype and these points:
  - one sentence first
  - no red outside real errors
  - one primary action per view
  - the copy follows the voice guide
- **Hands-on:**
  - At each UI milestone, launch the app (`just v2-run`) and drive the main flows by keyboard only.
  - Before cutover, the second user tries a Windows build.

## Open items (they don't block M0–M3)

- The v2 name and identity: decided in milestone D.

- Code signing and notarization for the 2.0 installers.
- Whether the second user needs fr or it.
- Whether the two users ever share data, which decides whether shared expenses or sync between their two datasets matters.
