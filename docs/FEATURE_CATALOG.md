# Feature catalog

Candidate features for the Budget app and the work planned from the product owner's issues.
Open work comes first; finished work is collected under [Done](#done) at the end.

The app's ground rules still apply to every feature: local first (data in CSV files on the
user's computer), CHF as the base currency with ECB rates, COICOP 2018 for expenditure, and
nothing uploaded without the user asking for it.

**Status:** ☑ Done · ◐ Partly done · ☐ Open. Every table has a *Status* column, and every
feature description starts with its status.
**Effort:** S = up to a day · M = a few days · L = one to two weeks · XL = more than two weeks.
**Network:** *Local* runs fully offline · *Optional* works offline, with an online extra the
user turns on · *Online* needs a service.

*Issue 01* is the product owner's GitHub issue (local copy `docs/issues.local/ISSUE_01.md`,
not committed). The bugs B01–B15 and the features F36–F47 come from it or from testing it.

**Contents:** [Progress](#progress) · [Open now](#open-now) · [All features](#all-features) ·
[Open features from issue 01](#open-features-from-issue-01) · [Backlog](#backlog) ·
[Reference](#reference) · [Done](#done)

## Progress

| Milestone | Status | Done |
| --- | --- | --- |
| 1 · Bugs from issue 01 | ☑ Done | 6 of 6 steps |
| Follow-up fixes (B10–B15) | ☑ Done | 3 of 3 steps |
| 2 · Accounts and reconciliation | ☑ Done | 3 of 3 steps |
| 3 · Revaluations | ☑ Done | 4 of 4 steps |
| 4 · Import and charts | ☑ Done | 3 of 3 steps |
| 5 · COICOP data | ☐ Open | 0 of 2 steps; waits for decision 7 |
| After issue 01 (steps 19–25) | ◐ Partly | steps 19, 20, 21 and 23 done (F24, F25, F28, F08, F06, F05, F02) and F29; steps 22, 24, 25 open |
| Bugs B01–B15 | ☑ Done | 15 of 15 fixed |
| Engineering: tests and hooks | ☑ Done | coverage 99 %, gate at 87 % |
| Engineering: CI and installers for macOS, Windows, Linux | ☑ Done | checks pass on every PR; the installer workflow builds macOS (49 MB), Windows (51 MB) and Linux (273 MB); no release published yet |

## Open now

State on 2026-10-20: milestones 1–4 and the follow-up fixes are done (see [Done](#done));
milestone 5 waits for decision 7 (the COICOP source data), so the work after issue 01 comes
next. Steps run in the order given; *Needs* lists what has to be done first, and decisions
are numbered as in [Decisions for the product owner](#decisions-for-the-product-owner).

### Next milestones

#### Milestone 5 · COICOP data

Waits for the source data (decision 7).

| Step | Status | Task | Needs | Effort |
| --- | --- | --- | --- | --- |
| 17 | ☐ Open | **F40 · COICOP notes in search and on hover**; also shown on the F37 page. | decision 7 | M |
| 18 | ☐ Open | **F39 · COICOP level 5.** | decision 7 | M |

#### After issue 01

| Step | Status | Task | Why here |
| --- | --- | --- | --- |
| 19 | ☑ Done | **F24** Export to CSV and JSON | Was next before the issue; reuses the save helper from milestone 1. |
| 20 | ☑ Done | **F25** Full backup and restore, **F28** Undo and change history | Safety before more import features. |
| 21 | ☑ Done | **F08** Simpler category picker, **F06** Faster manual entry, **F05** Recurring transactions | F08 builds on the COICOP select from F40; F05 shares the schedule code from B09. |
| 22 | ☐ Open | **F09** Payees → **F07** Automatic categorization → **F13** Bulk edit | The *Stop typing* bundle: imports (F01, F36) become quicker to categorize. |
| 23 | ☑ Done | **F02** camt.053 import | Reuses F01's preview and F36's closing balance; camt files carry the balance. |
| 24 | ☐ Open | **F21** Investments and asset classes | Continues "capital gains on investment" from F43. |
| 25 | ☐ Open | The rest, roughly by value per effort: F11, F12, F03, F10, F20, F16, F18, F14, F19, F30, F32, F31, F22, F23, F33, F34, F15, F17, F27, F26, F04, F35 | – |

### Bugs

All bugs from issue 01 and its follow-ups, open ones first. For a fixed bug, *Cause* says
what changed. B10–B14 were found while writing the UI tests on 2026-10-19, B15 while testing
the Linux build.

| ID | Status | Bug | Cause | Effort | Fixed in |
| --- | --- | --- | --- | --- | --- |
| B04 | ☑ Fixed | *Income and expenditure* chart leaves out unaccounted expenditure | Closed by F42: total expenditure means recorded spending everywhere, and the chart offers *Unexplained difference* as its own series. | – | `fc4cb19` |
| B01 | ☑ Fixed | Excel and PDF buttons in the budget overview did nothing | See [Done](#done). | – | `cc05db9` |
| B02 | ☑ Fixed | Chart image, CSV and PDF export didn't work | See [Done](#done). | – | `cc05db9`, `ffa9733` |
| B03 | ☑ Fixed | Transactions used the latest exchange rate, not the one of their date | See [Done](#done). | – | `332eb81` |
| B05 | ☑ Fixed | Selecting and adding a chart didn't work | See [Done](#done). | – | `8e9ca8e`, `ffa9733` |
| B06 | ☑ Fixed | Currency exchange converted the received amount instead of asking for it | See [Done](#done). | – | `b73d8fe` |
| B07 | ☑ Fixed | Accounts on the budget page didn't adapt to the window size | See [Done](#done). | – | `6f34640` |
| B08 | ☑ Fixed | Income and expenditure should stack when the window is narrow | See [Done](#done). | – | `6f34640` |
| B09 | ☑ Fixed | Editing a budget entry on the budget page lacked *Until*, *Repeat* and *Note* | See [Done](#done). | – | `ef5da63` |
| B10 | ☑ Fixed | Closed sheets stay in the page | A sheet deletes itself on Quasar's *hide* event, after the closing animation. | – | `6ddb7d1` |
| B11 | ☑ Fixed | Settings says an account's currency is locked by transactions | The caption names recorded balances too. | – | `cb13b2a` |
| B12 | ☑ Fixed | Deleting an income category shows no confirmation message | "Deleted *name*" after deleting an income category. | – | `cb13b2a` |
| B13 | ☑ Fixed | *Forever* gives a confusing message when the plan has ended | The status line says the plan ends before the first month and points to Settings. | – | `cb13b2a` |
| B14 | ☑ Fixed | The Budget Plan page's edit dialog has no *Until* and *Repeat* | Until and Repeat in the Budget Plan dialog; a change replans the series from that month on. | – | `f1ab8b2` |
| B15 | ☑ Fixed | Chart axes repeat labels when the values are small | Axis format `,~f`: whole numbers stay whole, small steps keep their decimals. | – | `8249156` |

### Decisions for the product owner

Collected from the entries below. Decisions 2–5 were made on 2026-10-19, each as recommended, decision 6 on 2026-10-20.

1. **B08:** side by side on wide windows and stacked on narrow ones — is that the intent? Built for now: stacked at every width, which meets the issue as written.
2. ☑ **F42 / B04:** decided — replace *Unaccounted* by the reconciliation line, with the gap
   as an optional chart series. Built.
3. ☑ **F43:** decided — revaluations do **not** count towards *Net income*; they have their own
   section and enter only the reconciliation line.
4. ☑ **F45:** decided — value changes of foreign-currency balances between two month ends are
   shown automatically as *Capital gains on currency exchange*, without a booking.
5. ☑ **F47:** decided — finished months without balances use the same rule (the higher of
   recorded and budget per category). Built.
6. ☑ **F36:** decided — a month the file ends inside is offered **unticked**, with the note
   "the file ends on *date* — not a month-end balance"; complete months are ticked. Built.
7. **F39, F40:** which source for level 5 and for the German notes?
8. ☑ **F07:** decided on 2026-10-20 — rules and learning from history, no AI. Confirming a
   suggestion does not create a rule; *Always categorize like this* does, on request.

## All features

| ID | Status | Feature | Area | Effort | Network | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| F01 | ☑ Done | CSV import of transactions | Data in | M | Local | – |
| F02 | ☑ Done | Swiss bank statement import (camt.053 / camt.054) | Data in | M | Local | F01 (preview, duplicates) |
| F03 | ☐ Open | Swiss QR-bill scanning | Data in | M | Local | – |
| F04 | ☐ Open | Receipt scanning | Data in | L | Optional | F12 |
| F05 | ☑ Done | Recurring transactions | Data in | M | Local | – |
| F06 | ☑ Done | Faster manual entry | Data in | S–M | Local | – |
| F07 | ☐ Open | Automatic categorization | Organizing | M–L | Optional | F09 |
| F08 | ☑ Done | Simpler category picker | Organizing | S | Local | – |
| F09 | ☐ Open | Payees | Organizing | M | Local | – |
| F10 | ☐ Open | Tags | Organizing | M | Local | – |
| F11 | ☐ Open | Split transactions | Organizing | M | Local | – |
| F12 | ☐ Open | Attachments | Organizing | M | Local | – |
| F13 | ☐ Open | Bulk edit | Organizing | S | Local | – |
| F14 | ☐ Open | Savings goals | Planning | M | Local | – |
| F15 | ☐ Open | What-if scenarios | Planning | L | Local | – |
| F16 | ☐ Open | Budget from history | Planning | S | Local | – |
| F17 | ☐ Open | Subscription and recurring-spend detection | Insight | M | Local | F09 |
| F18 | ☐ Open | Trends and unusual spending | Insight | M | Local | – |
| F19 | ☐ Open | Year in review | Insight | M | Local | – |
| F20 | ☐ Open | Swiss tax summary | Insight | M | Local | F10 |
| F21 | ☐ Open | Investments and asset classes | Wealth | L | Optional | – |
| F22 | ☐ Open | Loans and mortgages | Wealth | M | Local | – |
| F23 | ☐ Open | Who owes what (shared expenses) | Wealth | M | Local | – |
| F24 | ☑ Done | Export to CSV and JSON | Data out | S–M | Local | – |
| F25 | ☑ Done | Full backup and restore | Data out | S | Local | – |
| F26 | ☐ Open | Sync through a cloud folder | Data out | L | Optional | F28 |
| F27 | ☐ Open | Encryption and app lock | Security | L | Local | – |
| F28 | ☑ Done | Undo and change history | Security | M | Local | – |
| F29 | ☑ Done | Multi-language support | Experience | L | Local | – |
| F30 | ☐ Open | Onboarding and demo data | Experience | M | Local | – |
| F31 | ☐ Open | Reminders and alerts | Experience | M | Local | – |
| F32 | ☐ Open | Keyboard shortcuts and accessibility | Experience | M | Local | – |
| F33 | ☐ Open | In-app update check | Experience | S | Optional | – |
| F34 | ☐ Open | Several households or profiles | Experience | M | Local | – |
| F35 | ☐ Open | Phone companion | Experience | XL | Optional | F27 |
| F36 | ☑ Done | Closing balance from bank files | Data in | M | Local | F01 |
| F37 | ☑ Done | COICOP information page | Organizing | S | Local | – (F40 adds notes) |
| F38 | ☑ Done | Exchange-rate chart | Insight | S | Local | – |
| F39 | ☐ Open | COICOP level 5 | Organizing | M | Local | source data |
| F40 | ☐ Open | COICOP notes in search and on hover | Organizing | M | Local | source data |
| F41 | ☑ Done | Opening balance and change per account | Wealth | S | Local | – |
| F42 | ☑ Done | Reconciliation line instead of *Unaccounted* | Insight | S | Local | – |
| F43 | ☑ Done | Revaluation transactions | Wealth | M | Local | – |
| F44 | ☑ Done | Exchange spread booked as revaluation | Wealth | S–M | Local | B03, B06, F43 |
| F45 | ☑ Done | Revaluations in the budget overview | Insight | S | Local | F43 |
| F46 | ☑ Done | Unexplained difference per account | Wealth | S–M | Local | F41, F43 |
| F47 | ☑ Done | Projection uses the higher of actual and budget | Planning | S–M | Local | – |

## Open features from issue 01

### F36 · Closing balance from bank files

**Status:** ☑ Done (2026-10-20, `a30559a`)

Bank exports usually carry a running balance (*Saldo*, *Kontostand*, *Balance*). Today the
import ignores it.

- The bank profile gets a balance column, detected from its name where possible.
- The review step lists "Balance at end of *month*" for each month the file covers to the
  end, and shows any recorded balance it would replace. Accepted ones become the account's
  month-end balance, at the month-end rate.
- A manual *Closing balance* field covers banks that put the balance outside the table.
- Small extras that make categorizing imports quicker: a category editor in each row, and
  "transfer to own account" as a row type.
- **Decide:** what to do with a file that ends mid-month (skip its last balance, or offer it
  with a warning). Decided (decision 6): offer it unticked, with a note.

**As built.** Bank profiles have an optional `balance_col`, detected from names such as *Saldo*,
*Kontostand*, *Balance*, *Solde* or *Saldo contabile* and chosen in the columns step (older saved
profiles load unchanged). `services/import_balances.py` finds the order of the lines as the reading
(file order or reversed) in which the dates run forward and every balance equals the previous one
plus the amounts in between, lines without a balance bridged by their amounts; the balance after the
last booking of each month covered becomes a candidate. A month is complete, and ticked, when the file
continues past it or its last booking is on the month's last day; the month the file ends inside is
offered unticked with "the file ends on *date* — not a month-end balance". Balances that fit neither
order, or both with different results, are not offered; a short reason is shown instead. A camt
statement offers its closing booked balance, ticked when it is dated on a month's last day. The review
step lists "Balance at end of *month*" with a checkbox, "replaces *amount*" for a different recorded
balance, and leaves an identical recorded one unticked ("already recorded"). Ticked balances are saved
with `wealth.upsert_entry` after the transactions, also when every row is a duplicate ("Import 0
Transactions and 1 Balance"). Not done: the manual *Closing balance* field, the per-row category
editor and the "transfer to own account" row type.

### F37 · COICOP information page

**Status:** ☑ Done (2026-10-20, `009f0a5`)

A page listing all COICOP categories as a tree with code and name, in the interface
language, with a search box. Linked from the sidebar or Settings. With F40, each category
also shows what it includes.

**As built.** *Categories* in the sidebar (after Budget Plan, route `/categories`): all 602
categories of the four levels as one tree, code and name in the interface language, divisions
bold, the number of subcategories, and what was spent in each this calendar year in the display
currency. The search matches code or name, ignoring case and accents (*getranke* finds
*Getränke*); the ancestors of each match stay visible and open, and clearing the search
restores what was open before. *Taxes* and *Advance payments* are the app's own categories, not
COICOP, so a note below the tree explains them instead of listing them. F40 adds the notes to
each node and to its search text.

### F38 · Exchange-rate chart

**Status:** ☑ Done (2026-10-20, `3a4e7d3`)

**As built:** chart type *Exchange Rates* on the Overview; currencies default to those of the
user's accounts; daily or month end; index view; CSV/Excel/PNG/PDF export like the other
charts. `FxRates.history()` provides the prices.

A fourth chart type on the Overview: the ECB rates of chosen currencies against the display
currency over a period, daily or at month end (the rates balances are valued with). Optional
index view (start = 100) when currencies differ in scale. Exportable like the other charts.

### F39 · COICOP level 5

**Status:** ☐ Open

COICOP 2018 itself has four levels (division, group, class, subclass), and the app already
ships all of them. A fifth level exists only in national extensions.

- Needs a source with a fifth level whose codes keep the COICOP 2018 codes, so saved data
  stays valid; German names for the new codes (and French and Italian later).
- The code is ready for any depth; the work is the data and its translation.
- **Decide:** which source. A Swiss classification from the Federal Statistical Office
  would fit the users best, if it extends COICOP 2018.

### F40 · COICOP notes in search and on hover

**Status:** ☐ Open

The official COICOP 2018 publication describes each category with *includes*, *also
includes* and *excludes* notes. The shipped file has names only.

- Add the notes (about 600) and a German translation of them.
- Category search in every dropdown also matches the notes; hovering an entry shows them.
- The import page's category select switches to the same control.
- **Decide:** where the German notes come from (official translation, if one exists, or our
  own); whether *excludes* is shown too (helps choosing, not asked for).

### F41 · Opening balance and change per account

**Status:** ☑ Done (2026-10-19, `fc4cb19`)

The budget page shows for each account the balance at the end of the previous month, the
balance at the end of this month and the difference, in the account's currency (and the
closing value in the display currency, as today). Accounts emptied this month stay visible.

### F42 · Reconciliation line instead of *Unaccounted*

**Status:** ☑ Done (2026-10-19, `fc4cb19`)

*Unaccounted expenditure* stops being a row of the expenditure table. Instead, a line above
the income and expenditure tables shows:

- recorded income minus recorded expenditure,
- the change in account balances,
- the difference between the two (the former *Unaccounted* amount).

Total expenditure then means recorded expenditure on the budget page, in the charts and in
the statement export. The statement gets the reconciliation as its own section. With F45,
revaluations are part of the line.

### F43 · Revaluation transactions

**Status:** ☑ Done (2026-10-19, `16c72f5`)

A fifth transaction type for value changes that are neither income nor spending.

- One account; enter either the old and new value or just the difference (positive or
  negative), in the account's currency.
- Two kinds: *Capital gains on currency exchange* and *Capital gains on investment*.
- Revaluations count neither as income nor as expenditure; they appear in their own section
  (F45) and explain account changes (F46).
- **Decide:** whether revaluations count towards *Net income* on the budget page.

### F44 · Exchange spread booked as revaluation

**Status:** ☑ Done (2026-10-19, `16c72f5`)

With the received amount typed in (B06), the bank's rate differs from the ECB rate. The
difference becomes a linked revaluation of the kind *Capital gains on currency exchange* on
the receiving account, usually a loss.

- The exchange itself is recorded at the ECB rate; the revaluation brings the receiving
  account to the amount actually received. Together they match the bank statement.
- The revaluation is created, updated and deleted with its exchange and can't be edited on
  its own.
- Existing exchanges have no spread and stay as they are.

### F45 · Revaluations in the budget overview

**Status:** ☑ Done (2026-10-19, `16c72f5`) — the optional chart lines are not built.

A third section next to income and expenditure listing the month's revaluations by kind,
with a total, also in the statement export. Optionally as lines in the *Income and
expenditure* chart.
**Decide:** whether the change in value of foreign-currency balances between two month ends
counts as *Capital gains on currency exchange* automatically, or stays in the reconciliation
difference until booked.

### F46 · Unexplained difference per account

**Status:** ☑ Done (2026-10-19, `16c72f5`)

Next to each account, the difference between the actual change of its balance and the sum
of its transactions in the month (income, spending, transfers, exchanges, revaluations), in
the account's currency. Shown only when both month-end balances are recorded. A *Book as
revaluation* action fills in F43's form with the difference.

### F47 · Projection uses the higher of actual and budget

**Status:** ☑ Done (2026-10-19, `fc4cb19`)

For a month that isn't over, the projected wealth takes, for each category, the recorded
amount if it is higher than the budget, and the budget otherwise. Applies to income and
expenditure alike, to the projected wealth on the budget page and to the Overview chart.

- Compared per top-level row (COICOP division, taxes, advance payments, income category),
  so a budget set on a parent category isn't counted twice.
- Advance payments have no budget and use the recorded amount.
- **Decide:** months that are over but have no recorded balances yet: budget only (as
  today) or the same rule.

## Backlog

Feature ideas from the first catalog, by area. F01 is done and described under [Done](#done);
F29 is done for English and German.

### Getting data in

#### F02 · Swiss bank statement import (camt.053 / camt.054)

**Status:** ☑ Done

Swiss banks deliver statements as ISO 20022 XML (camt.053 for account statements, camt.054
for notifications); since the SIX migration this is the standard e-banking export.

- Needs no column mapping: amounts, currency, booking date, counterparty, reference and the
  bank's own transaction ID are all structured, so duplicate detection is exact.
- Reuses F01's preview and duplicate handling.
- **Decide:** whether to also read the older MT940 format some banks still offer.

**As built.** The import page takes `.csv` and `.xml`; a camt file skips the columns step and goes
straight to review, through the same duplicate marking, category suggestions and one-write save as
F01. Any version is read (namespace-agnostic); only booked entries are imported, a batch splits
into its payments when their amounts add up, and `import_ref` is `camt:<IBAN>:<bank reference>`.
The account is remembered per IBAN (`import_accounts.json`) and a statement in another currency
than the account is refused. The closing balance is offered as a month-end balance (F36). Not done: MT940,
camt.052 intraday reports, files holding several accounts (refused with a message).

#### F03 · Swiss QR-bill scanning

**Status:** ☐ Open

Every Swiss invoice carries a QR code with the amount, currency, creditor and reference.

- Drop a PDF or photo of the bill (or several) → the QR code is decoded locally → a
  pre-filled expenditure opens with amount, currency, creditor as payee and the reference
  as the note.
- Optionally keep the bill as an attachment (F12) and mark it *unpaid* until paid.
- Decoding is exact (no OCR guesswork) and needs only a small QR library.
- **Decide:** whether the app should track open bills (due date, paid/unpaid) or only record
  the payment.

#### F04 · Receipt scanning

**Status:** ☐ Open

Photograph or drop a till receipt; the app reads merchant, date, total and, ideally, the
lines.

- **Local option:** OCR on the computer (Apple Vision on macOS, Tesseract elsewhere). Private
  and free, but reading totals from crumpled receipts is unreliable; expect the user to check.
- **Online option:** a vision-capable AI model reads the receipt far more reliably, including
  lines and VAT, but the image leaves the computer. Must be opt-in, per receipt or as a
  setting, with a clear statement of where the image goes.
- The result opens as a pre-filled transaction; with F11, the lines can become a split.
- The image is stored as an attachment (F12).
- **Decide:** local only, online only, or local with an online "read better" button; whether
  line items matter or the total is enough.

#### F05 · Recurring transactions

**Status:** ☑ Done (2026-10-20)

**As built:** *Recurring* in the sidebar (after Transactions, route `/recurring`, with the number
of due occurrences as a badge). A series is an expenditure, income or account transfer with amount,
currency, category, accounts, first date, rhythm (the Budget Plan's: every month to every year),
an optional end date and a note; it is stored in `recurring.csv`. Occurrences fall on the first
date's day (the 31st becomes the last day of shorter months). The page lists the **Due**
occurrences (date ≤ today, one row per missed date) with the amount and date editable, *Book*,
*Skip* and *Book All*; booking goes through the normal transaction save (validation, ECB rate of
the date), and the transaction keeps a reference to its series (`recurring_id`), shown in the
Transactions list's *Repeats* column. A series set to *Book automatically* is booked without
asking at startup and at each 30-minute refresh (and right away when it is saved). *Make
Recurring…* on a selected transaction starts a series with it as the first booking. Booking,
skipping, editing and deleting are undoable. Decided by the product owner: occurrences become
transactions only through the Due list or *Book automatically*; editing a series changes its
future occurrences only (it continues after the last occurrence booked or skipped), booked
transactions stay, and deleting a series keeps them. Not built: the budget projection does not
use series directly (it would count a series and a budget entry for the same category twice; a
budget entry still plans it), and there is no pause switch (an end date or deleting does that).

Rent, insurance premiums and subscriptions repeat every month or year.

- Define a series (amount, accounts, category, rhythm, start and end) once.
- **Modes:** create the transactions automatically on their date, or list them as *due* for
  one-click confirmation (safer when amounts vary).
- The budget projection can use them directly, instead of a separate budget entry.
- **Decide:** auto-create versus confirm; how edits to a series affect past transactions.

#### F06 · Faster manual entry

**Status:** ☑ Done (2026-10-20)

**As built:** sums in the amount fields (evaluated safely: numbers, + − × ÷ and brackets, Swiss
apostrophes and decimal commas); named templates from a bookmark menu in the form; recently used
accounts first in the account dropdowns (recent *categories* come with F08); ⌘↩ / Ctrl+Enter
saves, N opens a new transaction; *Duplicate* from the edit sheet and the Transactions list,
with the items.

Small changes for the most frequent task:

- *Duplicate* a transaction (from the list or the edit sheet).
- Favourites: saved templates such as "Coffee · 01.1.1 · Cash · 4.50".
- Recently used categories and accounts at the top of each dropdown.
- Arithmetic in the amount field (`12.50+8.30`).
- Keyboard: `N` for a new transaction, `⌘↩` / `Ctrl+Enter` to save.

### Organizing

#### F07 · Automatic categorization

**Status:** ☐ Open

Suggest the category (and account) for new or imported transactions.

1. **Rules:** "text contains *Migros* → 01.1 Food". Users create rules from any transaction
   ("Always categorize like this").
2. **Learning from history:** without rules, suggest the category this payee had most often.
   Simple, local and explainable ("used 14 times for this payee").
3. **Optional AI:** for unknown payees, ask an AI model to choose a COICOP code from the
   payee and text. Opt-in, since the text leaves the computer.

Suggestions are always shown and confirmable, never applied silently. Works best with
payees (F09) and pays off most together with imports (F01, F02).
**Decided** (decision 8): levels 1 and 2, no AI; rules are created only with *Always
categorize like this*, never from a confirmed suggestion.

#### F08 · Simpler category picker

**Status:** ☑ Done (2026-10-20, `6786f2a`)

**As built:** 81 common COICOP codes (and Taxes) have an everyday name in English, German, French
and Italian plus about 1'700 search words (shops, brands and Swiss terms such as Migros, SBB, ZVV,
Krankenkasse, Serafe, Kita, Nebenkosten) in `resources/everyday_categories.json`; every other
category keeps its translated COICOP name. The picker shows "Groceries · 01.1" with the official
name as a second line and lists the last ten distinct expenditure categories first (marked with a
history icon). The search matches everyday name, search words, official name and code, ignoring
case and accents, in the browser. Transaction form, budget forms, the Budget Plan sheet and the
import page use it; the stored value stays the COICOP code. Not built: favourites, and everyday
names outside the picker (lists and reports keep the COICOP labels).

COICOP names are statistical ("Individual consumption expenditure of non-profit institutions
serving households"). Keep COICOP as the storage format, but:

- show short everyday names ("Groceries", "Public transport") with the COICOP code beside them;
- put recent and favourite categories first;
- search everyday words ("Netflix" → 09.4 Recreational services).

#### F09 · Payees

**Status:** ☐ Open

A payee or merchant field, normalized ("MIGROS ZUERICH HB 1234" → "Migros").

- Payee list with merge and rename; payee filter and search in Transactions.
- Basis for categorization (F07), subscription detection (F17) and "top merchants" reports.
- Imports fill it from the bank text; QR bills from the creditor.

#### F10 · Tags

**Status:** ☐ Open

Labels across categories: "Holiday Italy 2026", "Renovation", "Business".

- Several tags per transaction; filter, total and chart by tag.
- Budget by tag (for example a project budget across many categories).
- Required for the tax summary (F20), where "deductible" is a tag.

#### F11 · Split transactions

**Status:** ☐ Open

One payment, several categories: a supermarket receipt with food, household goods and a
gift.

- The transaction keeps one total and account; lines split the amount by category, and
  optionally by tag.
- Reports and budgets count each line in its own category.
- Interacts with advance payments: a split line can be the part paid for someone else.

#### F12 · Attachments

**Status:** ☐ Open

Keep receipts, invoices and contracts with the transaction.

- Files are stored in the data folder (`attachments/<transaction id>/`), so backups and sync
  include them.
- Preview in the edit sheet; open with the system viewer.
- Required by receipt scanning (F04); useful for warranty and tax proofs.

#### F13 · Bulk edit

**Status:** ☐ Open

Select many transactions → change category, account, tag or note in one step. The selection
bar on the Transactions page already exists; this adds an *Edit* sheet for several rows.

### Planning and insight

#### F14 · Savings goals

**Status:** ☐ Open

"Holiday 4'000 CHF by June", "Emergency fund 3 months of spending".

- Target amount and date, optionally linked to an account.
- Progress on the Overview; the monthly amount needed to reach it on time.
- Optionally reserves money in the projection, so the projected free wealth is honest.

#### F15 · What-if scenarios

**Status:** ☐ Open

Compare futures: "What if I reduce to 80 % work from January?", "Buy a car for 25'000 CHF in
March".

- A scenario is a set of budget changes applied on top of the plan, never written into it.
- The Overview wealth chart shows the plan and each scenario as separate lines.
- **Decide:** how many scenarios to keep; whether a scenario can be adopted into the plan.

#### F16 · Budget from history

**Status:** ☐ Open

Propose a budget from what actually happened: the average (or median) of the last 3, 6 or 12
months per category, as a starting point the user edits. A one-sheet action on Budget Plan.

#### F17 · Subscription and recurring-spend detection

**Status:** ☐ Open

Find charges that repeat with similar amounts (streaming, phone, gym) and list them with
the yearly total. One click turns a detected subscription into a recurring transaction (F05).

#### F18 · Trends and unusual spending

**Status:** ☐ Open

- Month-over-month and year-over-year change per category on the Budget page.
- A short "Notable this month" list on the Overview: categories well above their usual
  level, a large single transaction, a budget about to be exceeded.

#### F19 · Year in review

**Status:** ☐ Open

A yearly report: income, spending by category, savings rate, wealth change, biggest
expenses, comparison with the previous year. On screen and as a PDF, built on the existing
statement export.

#### F20 · Swiss tax summary

**Status:** ☐ Open

Help with the annual tax declaration, without trying to file it:

- yearly totals for typically deductible items: professional expenses, pillar 3a payments,
  insurance premiums, medical costs, donations, childcare, debt interest;
- uses COICOP codes where they match and tags (F10) otherwise;
- export as PDF or CSV with the supporting transactions and attachments (F12).

Cantonal rules differ, so the app lists amounts and leaves the judgment to the user.

### Wealth

#### F21 · Investments and asset classes

**Status:** ☐ Open

Today wealth comes from recorded account balances. Add:

- asset classes (cash, securities, pillar 2/3a, real estate, crypto, other) and net worth
  by class;
- securities holdings with quantity and price, valued manually or (optional, online) with
  daily prices;
- performance per holding and in total.

Revaluations of the kind "capital gains on investment" (F43) are the manual first step;
F21 would create them from price changes.

#### F22 · Loans and mortgages

**Status:** ☐ Open

Liabilities with interest rate and repayment plan; interest payments are categorized
automatically. Projected wealth includes amortization; a mortgage renewal date can trigger
a reminder (F31).

#### F23 · Who owes what (shared expenses)

**Status:** ☐ Open

Advance payments already record money paid for others. Build on that:

- an overview per person: open amounts, oldest open item, total settled this year;
- a "settle up" action that records the repayment;
- an optional summary to send (text or PDF), without the app sending anything itself.

### Data out and safety

#### F24 · Export to CSV and JSON

**Status:** ☑ Done (2026-10-19)

**As built:** a share button on Transactions and Budget Plan exports the rows the filters show,
in the list's column order, as CSV or Excel. *Settings → Data → Export as JSON…* writes every
record into one document (`format: budget-app/export`, `version: 1`). Decided: the JSON export is
for other programs and is not imported back; full backups (F25) are the restore format.

- Export the current Transactions or Budget Plan view (with its filters) as CSV or Excel.
- Export everything as one JSON file: accounts, categories, transactions, balances, budget,
  settings, with a format version, readable by other tools.
- **Decide:** whether the JSON export should also be importable (it then doubles as F25).

#### F25 · Full backup and restore

**Status:** ☑ Done (2026-10-19)

**As built:** *Settings → Data*: *Back Up Now…* saves `Budget-backup-<date>.zip` (the records
plus a manifest; the ECB file is downloaded again instead). *Choose Backup…* restores one after a
confirmation; the archive is checked completely first (plain CSV/JSON names only, size limits,
every table must load), and the current records go to `backups/<timestamp>_before-restore/`.
*Automatic Backups* lists those and the update backups (the ten most recent) with *Restore…*.

- *Back up now* writes a dated ZIP of the data folder to a place the user picks.
- *Restore* replaces the data from a ZIP after confirmation, with an automatic backup of the
  current state first.
- The update backups in `backups/` can be listed and restored from the same screen.

#### F26 · Sync through a cloud folder

**Status:** ☐ Open

Use the app on two computers by keeping the data folder in iCloud Drive, OneDrive or
Dropbox.

- Settings gets *Data folder* with a move function.
- The single-instance lock only works on one computer. Two computers editing at the same
  time can overwrite each other's files, so the app needs change detection and a merge
  strategy (per-record changes, which F28's change log enables).
- **Decide:** "one computer at a time" with a warning (simple), or real merging (large).

#### F27 · Encryption and app lock

**Status:** ☐ Open

The CSV files are readable by anyone with access to the computer.

- **App lock:** password, or Touch ID / Windows Hello, before the app opens.
- **Encryption at rest:** data files encrypted with a key derived from the password.
- **Trade-off:** encrypted files can no longer be opened in Excel, and a forgotten password
  means lost data, so a recovery key is needed.

#### F28 · Undo and change history

**Status:** ☑ Done (2026-10-20, `a41bc40`)

**As built:** every change to the records is recorded with the record before and after; one user
step (a save with its items, an import with its balances, an exchange with its spread) is one
action. Confirmation toasts offer *Undo*; *Settings → Data → Change History* lists the newest
actions with their changes and *Undo*. An undo is refused if a record changed afterwards. The
history keeps the newest 500 actions in `history.jsonl` and is not part of backups. Not built:
"who" (the app has one user) and per-record history views.

- *Undo* for the last action (delete, edit, import), offered in the confirmation toast.
- A change log (who, when, before and after) for transactions, balances and budget entries.
- Makes imports safe to try, and enables merging in F26.

### Experience

#### F29 · Multi-language support

**Status:** ☑ Done (2026-10-19): English, German, French and Italian

**As built:** every interface text and all 438 COICOP names in German, French and Italian
(`resources/i18n/<lang>.json`, `coicop_<lang>.json`); the language follows the system or is chosen
in Settings. Dates per language (`9. Okt. 2026`, `9 oct. 2026`, `9 ott 2026`); amounts keep the
Swiss format everywhere. The grid's own texts and the date picker follow the language, and the
Windows installer offers all four. The COICOP names are the app's own translations, following
the Eurostat/ISTAT wording where known, not certified official texts. Decided: user-entered
names (accounts, income categories) are not translated.

Interface in English, German, French and Italian, matching Switzerland and the installer,
which already speaks English, German and French.

- All interface text moves into translation files (gettext); the language follows the
  system or is chosen in Settings.
- Number and date formats per language (`1'234.50` stays for Switzerland; `9. Okt. 2026`,
  `9 oct. 2026`).
- **COICOP:** official names exist in the national languages (Eurostat publishes COICOP 2018
  translations); the shipped `coicop2018.csv` gets one column per language. Everyday names
  (F08) need translating too.
- PDF and Excel statements in the chosen language.
- **Decide:** which languages first; whether user-entered names (accounts, income
  categories) need translations (usually not).

#### F30 · Onboarding and demo data

**Status:** ☐ Open

- A first-start guide: language, display currency, accounts with their current balance,
  income categories, plan horizon. It ends on the Budget page with something to see.
- *Try with sample data:* a separate demo data folder to explore without touching real data.

#### F31 · Reminders and alerts

**Status:** ☐ Open

Desktop notifications, all optional:

- record month-end balances (first days of the month);
- a budget close to or over its limit;
- recurring payments due (F05), open QR bills (F03), goals off track (F14).

Shown when the app runs; reminders while it's closed would need a small background helper
(larger).

#### F32 · Keyboard shortcuts and accessibility

**Status:** ☐ Open

- Shortcuts: new transaction, save, search, previous/next month (`←` `→`), switch pages.
- Screen-reader labels, visible focus, logical tab order, sufficient contrast in both
  appearances (the colour tokens make the contrast checkable).

#### F33 · In-app update check

**Status:** ☐ Open

Once a day, read the latest version number from the GitHub repository (the same kind of
read-only request as the ECB download). If it's newer, the sidebar shows "Budget 0.2.0 is
available" with a link to the installer and the update steps. Can be switched off in
Settings; the README's privacy note then needs one more line.

#### F34 · Several households or profiles

**Status:** ☐ Open

Separate budgets (personal, joint household, a small association) in separate data folders,
switchable from the sidebar. Builds on the existing `--data-dir` support; mainly a picker and
per-profile settings.

#### F35 · Phone companion

**Status:** ☐ Open

Record expenses on the phone where they happen.

- **Smallest step:** the app already runs a web server; a phone-friendly *quick add* page
  reachable on the home network. Needs the app lock (F27), because the server would then
  accept connections from other devices.
- **Larger step:** a phone app that queues entries offline and syncs with the computer.
- **Decide:** whether entries made away from home matter (then a sync service is needed,
  which conflicts with local first).

## Reference

### Suggested bundles

| Bundle | Features | Why together |
| --- | --- | --- |
| **Stop typing** | F01, F02, F09, F07 (rules and history), F13 | Imports bring data in; payees and categorization make them usable; bulk edit fixes the rest. |
| **Swiss essentials** | F02, F03, F20, F29 (de, fr, it) | Banking formats, bills, tax and languages of the target users. |
| **Everyday speed** | F06, F08, F05, F32 | Small changes to the most frequent tasks. |
| **Trust** | F24, F25, F28, F27 | Data can leave, come back, be undone and be protected. |
| **Look ahead** | F14, F15, F16, F18 | Planning beyond the current budget. |
| **Books that balance** | F41, F42, F43, F44, F45, F46 | Every change in an account is explained by a transaction or a revaluation (issue 01). |

### Cross-cutting decisions

These come up in several features; deciding them once keeps the features consistent.

1. **AI and online services** (F04, F07, F21, F33): never, opt-in per use, or opt-in as a
   setting? Which provider, and who pays?
2. **Exchange rate of transactions** (F01, F02, F03, F05, B03): decided — every transaction
   uses the ECB rate of its date (the last earlier rate on weekends and holidays); account
   balances use the last rate of the month; budget entries use the latest rate. Changed by
   issue 01: manual entries used the latest rate before.
3. **Data format changes:** payees, tags, splits and attachments add fields and files. Each
   needs a default for old data (the update backup covers mistakes); see
   [packaging/README.md](../packaging/README.md#releasing-an-update).
4. **Languages** (F29): decided and built — English, German, French and Italian. Every new
   text needs all three translations; the tests enforce it.

## Done

### Milestone 1 · Bugs from issue 01

Merged into `main` on 2026-10-19.

| Step | Bug | What changed | Commit |
| --- | --- | --- | --- |
| 1 | **B01** Excel and PDF buttons in the budget overview did nothing; **B02** chart image, CSV and PDF export didn't work | The app window (pywebview) drops browser downloads. Every export now goes through one helper: the system save dialog in the app window, a normal download in the browser. Charts gained *Save as PDF…*. | `cc05db9`, `ffa9733` |
| 2 | **B05** Selecting and adding a chart didn't work | Adding worked but put the card out of sight. A new chart now goes first, scrolls into view and is highlighted; errors are shown. | `8e9ca8e`, `ffa9733` |
| 3 | **B07** Accounts didn't adapt to the window; **B08** tables should stack when narrow | Account rows are a grid that follows the card's width; the ledgers use container queries. The tables stay below each other at every width (decision 1). | `6f34640` |
| 4 | **B03** Transactions used the latest exchange rate | Every transaction uses the rate of its date; stored ones are re-valued after each rate check. | `332eb81` |
| 5 | **B06** Currency exchange converted the received amount | The amount received is typed in; the form compares it with the ECB rate. | `b73d8fe` |
| 6 | **B09** Budget edit dialog lacked until, repeat and note | All three per entry; changing the schedule replans the series from that month on. | `ef5da63` |

**B03 as built.** Every transaction gets the ECB rate of its date, or the last earlier rate
on weekends and holidays; future dates get the newest rate. Changing the date revalues the
entry. After each rate check (start, then every 30 minutes) stored transactions whose rate date
doesn't match are re-valued: entries from older versions, entries made before the ECB
published that day (around 16:00) and future-dated entries once their rate exists. This
replaces the specification's rule that manual entries use the latest rate.

**B09 as built.** Budget entries are stored one row per month, linked by a series ID. The
dialog shows the series' end month (or *Forever*), its rhythm and an editable note; changing
the schedule replans the series from the edited month on, as a new series, and the earlier
months end there. The Budget Plan page's dialog has the same fields since B14.

### Follow-up fixes and milestone 2

| Step | Status | Task | Needs | Effort |
| --- | --- | --- | --- | --- |
| 6a | ☑ Done | **B10 · Close sheets for good**: remove a dialog from the page when it closes. | – | S |
| 6b | ☑ Done | **B14 · Until and repeat on the Budget Plan page** too (reuses `budget.reschedule` from B09). | – | S |
| 6c | ☑ Done | **B11, B12, B13 · Wording fixes** in Settings and the New Budget Entry form; **B15** chart axis labels. | – | S |
| 7 | ☑ Done | **F41 · Opening balance and change per account.** | – | S |
| 8 | ☑ Done | **F42 · Reconciliation line** above the income and expenditure tables; closes B04. | decision 2 (decided) | S |
| 9 | ☑ Done | **F47 · Projection uses the higher of actual and budget** per category. | decision 5 (decided) | S–M |

### Milestone 3 · Revaluations

| Step | Status | Task | Needs | Effort |
| --- | --- | --- | --- | --- |
| 10 | ☑ Done | **F43 · Revaluation transactions.** | decision 3 (decided) | M |
| 11 | ☑ Done | **F44 · Exchange spread booked as revaluation.** | B03 (done), B06 (done), F43 | S–M |
| 12 | ☑ Done | **F45 · Revaluations section** in the budget overview; the reconciliation line from F42 then counts them. | F42 (done), F43, decision 4 (decided) | S |
| 13 | ☑ Done | **F46 · Unexplained difference per account**, with a *Book as revaluation* action. | F41 (done), F43 | S–M |

### Milestone 4 · Import and charts

| Step | Status | Task | Needs | Effort |
| --- | --- | --- | --- | --- |
| 14 | ☑ Done | **F36 · Closing balance from bank files.** | decision 6 (decided) | M |
| 15 | ☑ Done | **F38 · Exchange-rate chart.** | – | S |
| 16 | ☑ Done | **F37 · COICOP information page** with codes, names and search. | – | S |

### Built features

| Item | Status | Notes |
| --- | --- | --- |
| F01 · CSV import of transactions | ☑ Done | Details below. |
| F29 · Multi-language support | ☑ Done | English, German, French and Italian, including the COICOP names. |
| Item lists on transactions | ☑ Done | Name, quantity and unit price per expenditure or income; search finds item names. Covers part of F11 and gives F04 a place for receipt lines. |
| Test suite and git hooks | ☑ Done | UI tests in NiceGUI's simulated client, 99 % line coverage with a gate at 87 %, lefthook checks before commit and push (`8fdc730`). |
| CI and installers for macOS, Windows and Linux | ☑ Done | GitHub Actions run the checks on every push and pull request and build the three installers (first successful runs on 2026-10-19); a version tag publishes them as a release. Linux: `.tar.gz` with a per-user `install.sh` and a Qt WebEngine window. The Windows and Linux installers have not been tried on a real PC yet. See [packaging/README.md](../packaging/README.md). |

#### F01 · CSV import of transactions

**Status:** ☑ Done

Today every transaction is typed by hand. Import a bank or card export instead.

- **Flow:** choose a file → map its columns (date, amount, sign or debit/credit, text,
  currency) → choose the account → preview with categories filled in where known → import.
- **Bank profiles:** the column mapping is saved per bank, so the next import is two clicks.
  CSV dialects vary (`;` vs `,`, `1'234.50` vs `1.234,50`, `dd.mm.yyyy`); the profile
  stores those too.
- **Duplicates:** each imported row gets a fingerprint (date, amount, account, text). Rows
  already imported are shown and skipped by default.
- **Fits:** imported rows become normal transactions, with the CHF amount at the ECB rate of
  the booking date. Since issue 01 (B03) manual entries use the rate of their date too.
- **Open since issue 01:** the closing balance in the file is offered since F36; categories
  are set for selected rows only, not per cell; transfers between own accounts can't be
  marked as such and must be given a category.
- **Decide:** which banks to support first; whether imports may create accounts; how to treat
  transfers between own accounts that appear in two files.
