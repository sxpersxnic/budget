# Feature catalog

Candidate features for the Budget app and the work planned from the product owner's issues.
Open work comes first; finished work is collected under [Done](#done) at the end.

The app's ground rules still apply to every feature: local first (data in CSV files on the
user's computer), CHF as the base currency with ECB rates, COICOP 2018 for expenditure, and
nothing uploaded without the user asking for it.

**Status:** ☑ Done · ◐ Partly done · ☐ Open · ☒ Dropped. Every table has a *Status* column, and every
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
| 5 · COICOP data | ☑ Done | step 17 (F40: English notes with our German, French and Italian translations); F39 dropped by decision 7 |
| After issue 01 (steps 19–25) | ◐ Partly | steps 19 to 24 done (F24, F25, F28, F08, F06, F05, F09, F07, F13, F02, F21) and F29; step 25 under way (F10, F12, F11, F03, F14, F16, F20, F18, F30, F33, F04, F15, F31, F19 and F17 done) |
| Bugs B01–B15 | ☑ Done | 15 of 15 fixed |
| Engineering: tests and hooks | ☑ Done | coverage 99 %, gate at 87 % |
| Engineering: CI and installers for macOS, Windows, Linux | ☑ Done | checks pass on every PR; the installer workflow builds macOS (49 MB), Windows (51 MB) and Linux (273 MB); no release published yet |

## Open now

State on 2026-10-21: milestones 1–4, the follow-up fixes and steps 19–24 are done (see
[Done](#done)); every decision is made, and milestone 5 (F40 notes in English, German, French and Italian) is done.
Step 25 is under way (F10 tags, F12 attachments, F11 split transactions, F03 QR bills, F14 savings goals, F16 budget from history, F20 tax summary and F18 trends done on 2026-10-22, F30 onboarding and demo data and F33 update check, F04 receipt scanning, F15 what-if scenarios, F31 reminders, F19 year in review and F17 subscriptions on 2026-10-28). Steps run in the order given; *Needs* lists what has to be done first, and decisions
are numbered as in [Decisions for the product owner](#decisions-for-the-product-owner).

### Next milestones

#### Milestone 5 · COICOP data

Decision 7: notes from the UN COICOP 2018 publication, our own translations; no level 5.

| Step | Status | Task | Needs | Effort |
| --- | --- | --- | --- | --- |
| 17 | ☑ Done | **F40 · COICOP notes in search and on hover**; also shown on the F37 page. Built with the UN's English notes and our German, French and Italian translations. | decision 7 (decided) | M |
| 18 | ☒ Dropped | **F39 · COICOP level 5.** | decision 7 (decided) | – |

#### After issue 01

| Step | Status | Task | Why here |
| --- | --- | --- | --- |
| 19 | ☑ Done | **F24** Export to CSV and JSON | Was next before the issue; reuses the save helper from milestone 1. |
| 20 | ☑ Done | **F25** Full backup and restore, **F28** Undo and change history | Safety before more import features. |
| 21 | ☑ Done | **F08** Simpler category picker, **F06** Faster manual entry, **F05** Recurring transactions | F08 builds on the COICOP select from F40; F05 shares the schedule code from B09. |
| 22 | ☑ Done | **F09** Payees → **F07** Automatic categorization → **F13** Bulk edit | The *Stop typing* bundle: imports (F01, F36) become quicker to categorize. |
| 23 | ☑ Done | **F02** camt.053 import | Reuses F01's preview and F36's closing balance; camt files carry the balance. |
| 24 | ☑ Done | **F21** Investments and asset classes | Continues "capital gains on investment" from F43. |
| 25 | ◐ Partly | The rest, roughly by value per effort: F11 (done), F12 (done), F03 (done), F10 (done), F20 (done), F16 (done), F18 (done), F14 (done), F19 (done), F30 (done), F32, F31 (done), F22, F23, F33 (done), F34, F15 (done), F17 (done), F27, F26, F04 (done), F35 | – |

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

Collected from the entries below. Decisions 2–5 were made on 2026-10-19, each as recommended, decision 6 on 2026-10-20,
decisions 1, 7 and 10–13 on 2026-10-21, decisions 14–21 on 2026-10-22. All decisions are made; none is open.

1. ☑ **B08:** decided — the income and expenditure tables sit side by side on wide windows and
   stack on narrow ones. Built: side by side once each table gets about 750 px (windows from about 1,800 px;
   narrower halves cut the category names short); tables of 560–800 px drop the progress bar.
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
7. ☑ **F39, F40:** decided — no level 5 (F39 dropped: the everyday names and search words of F08
   cover the detail); the notes (F40) come from the official UN COICOP 2018 publication in English,
   with our own German, French and Italian translations marked as unofficial, *excludes* included.
8. ☑ **F07:** decided on 2026-10-20 — rules and learning from history, no AI. Confirming a
   suggestion does not create a rule; *Always categorize like this* does, on request. Built.
9. ☑ **F21:** decided on 2026-10-21 — prices are entered by hand (or pasted from a bank or
   broker list), no online prices; every price update books a linked *Capital gains on
   investment* revaluation, so an account's booked value is always quantity × price; buys,
   sells, dividends and fees are modelled, so performance compares value with money put in.
   Built (architecture §7, decision 17).
10. ☑ **F09:** decided — the German word for payee is *Gegenpartei* (it covers both who you pay
    and who pays you).
11. ☑ **F09:** decided — payee names keep their legal form (AG, GmbH, SA, Sagl); known brands
    still get their short name.
12. ☑ **F21:** decided — dividends are entered gross with their withholding tax (35 % prefilled
    for CHF holdings); the net is credited, the tax is booked as Taxes, and a yearly sum of
    reclaimable tax is shown. Built (architecture §4.6, decision 18).
13. ☑ **F21:** decided — an account with history can become a securities or crypto account: its
    balances stay as history up to the conversion month, then holdings with an opening
    quantity and price take over. Built (architecture §4.6).
14. ☑ **F11:** decided on 2026-10-22 — a split is a category per item line: the Items list gets an
    optional category per line, lines without one keep the transaction's category, and reports
    and budgets count each line in its own category. Built (architecture §4.3, decision 21).
15. ☑ **F03:** decided — the app tracks open bills: a scanned QR bill waits in a Bills list with
    its due date until *Mark Paid* books the expenditure; the PDF stays as an attachment.
    Built (architecture §7, decision 22).
16. ☑ **F12:** decided — attachments are copied into the data folder
    (`attachments/<transaction id>/`), so backups and restore include them; one file at most 20 MB.
    Built (architecture §7, decision 20).
17. ☑ **F10:** decided — the first version has tags with filter, totals and chart, and a budget per
    tag (amount, optional period) with actual against budget. Built (see F10).
18. ☑ **F15:** decided on 2026-10-22 — any number of named scenarios, each a set of budget changes
    on top of the plan and never written into it; the Overview wealth chart shows the plan and the
    chosen scenarios; *Adopt into Plan* copies a scenario's changes into the budget as one undo step.
    Built (architecture §7, decision 30).
19. ☑ **F26:** decided — one computer at a time: the data folder can move into a cloud folder, a
    lock file there warns when another computer has the app open, and files changed elsewhere are
    detected and reloaded; no merging.
20. ☑ **F27:** decided — an app lock (password, or Touch ID / Windows Hello where available)
    before the app opens, and encryption at rest as an opt-in on top, with a recovery key; without
    the opt-in the files stay readable CSV.
21. ☑ **F35:** decided — a phone-friendly *quick add* page on the home network, behind the app
    lock and a pairing code; no entries from away from home, no cloud service.

## All features

| ID | Status | Feature | Area | Effort | Network | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| F01 | ☑ Done | CSV import of transactions | Data in | M | Local | – |
| F02 | ☑ Done | Swiss bank statement import (camt.053 / camt.054) | Data in | M | Local | F01 (preview, duplicates) |
| F03 | ☑ Done | Swiss QR-bill scanning | Data in | M | Local | – |
| F04 | ☑ Done | Receipt scanning | Data in | L | Local | F12 |
| F05 | ☑ Done | Recurring transactions | Data in | M | Local | – |
| F06 | ☑ Done | Faster manual entry | Data in | S–M | Local | – |
| F07 | ☑ Done | Automatic categorization | Organizing | M–L | Local | F09 |
| F08 | ☑ Done | Simpler category picker | Organizing | S | Local | – |
| F09 | ☑ Done | Payees | Organizing | M | Local | – |
| F10 | ☑ Done | Tags | Organizing | M | Local | – |
| F11 | ☑ Done | Split transactions | Organizing | M | Local | – |
| F12 | ☑ Done | Attachments | Organizing | M | Local | – |
| F13 | ☑ Done | Bulk edit | Organizing | S | Local | – |
| F14 | ☑ Done | Savings goals | Planning | M | Local | – |
| F15 | ☑ Done | What-if scenarios | Planning | L | Local | – |
| F16 | ☑ Done | Budget from history | Planning | S | Local | – |
| F17 | ☑ Done | Subscription and recurring-spend detection | Insight | M | Local | F09 |
| F18 | ☑ Done | Trends and unusual spending | Insight | M | Local | – |
| F19 | ☑ Done | Year in review | Insight | M | Local | – |
| F20 | ☑ Done | Swiss tax summary | Insight | M | Local | F10 |
| F21 | ☑ Done | Investments and asset classes | Wealth | L | Local | – |
| F22 | ☐ Open | Loans and mortgages | Wealth | M | Local | – |
| F23 | ☐ Open | Who owes what (shared expenses) | Wealth | M | Local | – |
| F24 | ☑ Done | Export to CSV and JSON | Data out | S–M | Local | – |
| F25 | ☑ Done | Full backup and restore | Data out | S | Local | – |
| F26 | ☐ Open | Sync through a cloud folder | Data out | L | Optional | F28 |
| F27 | ☐ Open | Encryption and app lock | Security | L | Local | – |
| F28 | ☑ Done | Undo and change history | Security | M | Local | – |
| F29 | ☑ Done | Multi-language support | Experience | L | Local | – |
| F30 | ☑ Done | Onboarding and demo data | Experience | M | Local | – |
| F31 | ☑ Done | Reminders and alerts | Experience | M | Local | – |
| F32 | ☐ Open | Keyboard shortcuts and accessibility | Experience | M | Local | – |
| F33 | ☑ Done | In-app update check | Experience | S | Optional | – |
| F34 | ☐ Open | Several households or profiles | Experience | M | Local | – |
| F35 | ☐ Open | Phone companion | Experience | XL | Optional | F27 |
| F36 | ☑ Done | Closing balance from bank files | Data in | M | Local | F01 |
| F37 | ☑ Done | COICOP information page | Organizing | S | Local | – (F40 added the notes) |
| F38 | ☑ Done | Exchange-rate chart | Insight | S | Local | – |
| F39 | ☒ Dropped | COICOP level 5 | Organizing | M | Local | source data |
| F40 | ☑ Done | COICOP notes in search and on hover | Organizing | M | Local | – |
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
COICOP, so a note below the tree explains them instead of listing them. Since F40 an info
button beside a category's name opens its notes below the row, and the search also finds what a
category includes (see F40).

### F38 · Exchange-rate chart

**Status:** ☑ Done (2026-10-20, `3a4e7d3`)

**As built:** chart type *Exchange Rates* on the Overview; currencies default to those of the
user's accounts; daily or month end; index view; CSV/Excel/PNG/PDF export like the other
charts. `FxRates.history()` provides the prices.

A fourth chart type on the Overview: the ECB rates of chosen currencies against the display
currency over a period, daily or at month end (the rates balances are valued with). Optional
index view (start = 100) when currencies differ in scale. Exportable like the other charts.

### F39 · COICOP level 5

**Status:** ☒ Dropped (decision 7: the everyday names and search words of F08 cover the detail)

COICOP 2018 itself has four levels (division, group, class, subclass), and the app already
ships all of them. A fifth level exists in national extensions; the UN's own structure file
(used for F40) has 269 five-digit codes, all in division 01 (food and non-alcoholic beverages).

- Needs a source with a fifth level whose codes keep the COICOP 2018 codes, so saved data
  stays valid; German names for the new codes (and French and Italian later).
- The code is ready for any depth; the work is the data and its translation.
- **Decided** (decision 7): not built.

### F40 · COICOP notes in search and on hover

**Status:** ☑ Done (2026-10-22: English, German, French and Italian)

**As built.** The notes come from the UN Statistics Division's *Final structure (Excel)* of
COICOP 2018 (Statistical Papers Series M No. 99), downloaded on 2026-10-21;
`scripts/coicop_notes.py` turns it into `resources/coicop_notes.json` (one item per line) and can be
re-run. 440 of the 602 categories have notes; the other 162 have none in the source (most
divisions and groups, which the publication describes only in an introduction, and all of
divisions 14 and 15). The source's 269 level-5 codes are not used (F39). The German, French and
Italian translations (`coicop_notes_de.json`, `_fr.json`, `_it.json`, 1'732 distinct items each) are
the app's own and marked *Unofficial translation*; each names categories as the app's COICOP names in
that language do (French *aides techniques*, Italian *n.a.c.*, *ausili*, *in regime di ricovero*), and
the Italian follows Swiss (Ticino) usage. A language file marked `_untranslated` would show English.

- **Search:** the picker (every category dropdown, the import page included) and the
  *Categories* page also find a category by the words of what it *includes* and *also includes*,
  in the interface language and in English. *Excludes* is shown but not searched, so a category
  is never found by what it does not cover. In the picker, matches by name or search word come
  first, matches by notes after them. The words travel with each option (about 115 kB more per
  picker in English, 215 kB in German); the notes themselves do not.
- **Hover:** the picker loads the notes of the interface language once per page, when it first
  opens (`/coicop-notes/<lang>`, about 40 kB compressed, cached by the browser), and shows an
  option's notes in a tooltip on hover or when the arrow keys reach it: *Includes*, *Also
  includes*, *Excludes*, and "Unofficial translation" in small print for a translation. On touch
  screens an info button in the option opens the notes inside it; a tap on the option still
  selects it.
- **Categories page:** an info button beside a category's name opens its notes below the row.

The official COICOP 2018 publication describes each category with *includes*, *also
includes* and *excludes* notes. The shipped file has names only.

- Add the notes (about 600) and a German translation of them.
- Category search in every dropdown also matches the notes; hovering an entry shows them.
- The import page's category select switches to the same control.
- **Decided** (decision 7): the English notes of the UN publication, our own German, French
  and Italian translations (marked unofficial), *excludes* shown too.

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

**Status:** ☑ Done (2026-10-22)

**As built (decision 15):** **Bills** in the sidebar (after Recurring, route `/bills`, icon `o_request_quote`) with a
badge counting the open bills that are overdue or due within 7 days. A drop zone, "Drop QR bills (PDF or photo)",
takes PDFs (every page, up to 20, is rendered with PDFium and searched; a document without a code is read once
more at double resolution) and photos (JPEG, PNG, WebP; EXIF orientation applied); the QR codes are found with
ZXing, all on the computer. Every code that is a QR bill is read and checked like a bank would
(`domain/qrbill.py`, SIX Implementation Guidelines 2.3, payload version 0200): IBAN check digits (CH and LI only),
QR-IBAN only with a QR reference and the reverse, QR reference (27 digits, mod 10 recursive), creditor reference
(ISO 11649), currency CHF or EUR, amount format and range (an empty amount is filled in when paying; 0.00 is a
notice and refused), structured (S) and combined (K) addresses with their field lengths, the 140- and
997-character limits and the EPD trailer. Each valid bill becomes a record in `bills.csv` (creditor, address,
IBAN, amount, currency, reference type and reference, message, bill information, due date, status
open/paid/dismissed, the paying transaction, added); the due date is read from Swico bill information
(`//S1/…/11/<invoice date>/…/40/0:30` = invoice date plus the net term) and can be set or cleared per bill.
A bill already in the list (same IBAN, reference and amount, whatever its status; without a reference also the
same message and bill information) is refused with "This bill is already in the list"; a file without one says
"No QR bill found in this file", a broken code names its fault ("The creditor’s IBAN … is invalid"). The file
is kept as an attachment of the bill (`attachments/<bill id>/`, decision 22). The **Open** list is sorted by due
date (overdue in red, due within a week in orange, bills without a date last) and shows creditor, amount,
due date, message and reference and a link to the file; **Mark Paid…** opens the transaction sheet ("Pay
Bill") prefilled with the creditor as payee (a known payee keeps its spelling: "Swisscom (Schweiz) AG" finds
the payee of that name, a new one is cleaned like a bank text), the amount and currency, the message and the
formatted reference as note and today's date; the category is suggested with its reason (F07) and the bill's
file is listed under Attachments; **Mark Paid** saves the expenditure and marks the bill paid in one undo
step, and the file then belongs to the transaction. Dismiss (paid elsewhere, not owed), Reopen, the due date
and Delete are undoable too; *Paid and Dismissed* is collapsed below, with *Show Payment* for a paid bill.
Deleting the payment opens the bill again. Not built: HEIC photos (Pillow needs a plugin; the message asks for
JPEG or PNG), the camera, reminders outside the app (F31), and matching a bill with a payment already
imported from the bank (it is booked as a new expenditure).

Every Swiss invoice carries a QR code with the amount, currency, creditor and reference.

- Drop a PDF or photo of the bill (or several) → the QR code is decoded locally → a
  pre-filled expenditure opens with amount, currency, creditor as payee and the reference
  as the note.
- Optionally keep the bill as an attachment (F12) and mark it *unpaid* until paid.
- Decoding is exact (no OCR guesswork) and needs only a small QR library.
- **Decide:** whether the app should track open bills (due date, paid/unpaid) or only record
  the payment.

#### F04 · Receipt scanning

**Status:** ☑ Done (2026-10-28)

**As built (decision 29):** **Receipt…** in the Transactions toolbar (before *Import…*) opens the *Receipt* sheet:
a drop zone for one PDF or photo (JPEG, PNG, WebP; HEIC asks for JPEG or PNG), then the receipt on the left and a new
expenditure on the right. The receipt is read **on the computer** by RapidOCR (PP-OCRv6 multilingual models, bundled
in its wheel, so nothing is downloaded) on ONNX Runtime; the libraries are the optional extra `budget-app[ocr]`,
included in the installers. A PDF with its own text (an e-receipt) is read from that text without the engine. From
the rows that were read (`domain/receipts.py`) the app takes the **total** (the last row that says Total, Summe,
Montant, Totale, Amount due, … and carries an amount; subtotal, VAT, change, card and payment rows don't count; without
such a row the largest amount), the **date** (the first plausible one, day first or ISO, also glued to a time), the
**currency** (CHF or EUR when named, else the display currency) and the **merchant** (the first row of words before
the total that is not a date, phone number, address or the receipt's own bookkeeping), and offers them as amount,
date, currency and payee (a known payee keeps its spelling, F09; the category is suggested as for any payee, F07).
The rows between merchant and total that end in an amount become the **item lines** (F11) when they add up to the
total, discounts as negative lines; otherwise no lines, so a misread line never makes a wrong split. A caption above
the form says what was read ("Read from the receipt: Coop Pronto Zürich HB · 28 Oct 2026 · 11.45 CHF. Check it
before you save.") or that nothing could be; the file waits under *Attachments* ("Attached when you save") and
**Add Transaction** stores the expenditure, its lines and the file in one undo step. Reading takes one to two seconds
(the engine loads once, about ten seconds, at the first receipt) in a worker thread with a "Reading the receipt…"
line meanwhile; PDF pages are rendered in the event loop like QR bills. Without the engine (not installed, or an
Intel Mac, for which ONNX Runtime has no Python 3.13 wheel) the sheet says so and the user types amount, date and
payee from the picture beside the form. Not built: online reading (decision 1: never), the camera, HEIC photos, VAT
and payment method, learning a merchant's layout, and reading several receipts at once. The engine's reading of
umlauts is imperfect ("Zørich" for "Zürich" in a test picture): the merchant is a proposal the user checks, and a
known payee's spelling wins.

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

**Status:** ☑ Done (2026-10-20)

**As built:** levels 1 and 2, no AI (decision 8). **Rules** live in `rules.csv` (text, type, category, optional
account) and are made only on request: *Always Categorize Like This…* in the Transactions selection bar (one
expenditure or income with a category) and in the edit sheet opens a sheet with the payee as the text (without a
payee, the name found in the note), the category and the type; a rule with the same text and type is named there,
and saving replaces it. A rule matches when its text occurs in the payee or in the bank text or note, ignoring case
and accents; the longest text wins, between equally long ones the newest. **History:** without a matching rule, the
category the payee had most often among transactions of the same type (ties: the most recent), "Used 14 times for
this payee"; rows stored before payees (F09) fall back to the same bank text without its digits. Where no account
is fixed, an account is suggested too: the rule's, else the one the payee had most often. The **form** shows
"Use Groceries · 01.1" with the reason under the category of a new expenditure or income once it has a payee; a
click fills the category (and the account, if still empty). The **import preview** (CSV and camt) fills in
suggestions in italics, with a *Suggestion* column for the reason, counts them in the summary and offers *Clear
Suggestions*; a category chosen by hand loses the mark, a changed payee brings a new suggestion, and importing keeps
what the preview shows. Confirming a suggestion never makes a rule. **Categorization Rules** (`/rules`, under
Payees; *Rules…* on the Payees page and in Settings) lists the rules per type, with add, edit and delete; every
change is undoable. Not built: the AI level, conditions on amount or account, and applying a new rule to stored
transactions (bulk edit, F13, does that).

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
case and accents, in the browser; since F40 also what the COICOP notes say a category includes
(listed after the matches by name), and hovering an option shows its notes. Transaction form, budget forms, the Budget Plan sheet and the
import page use it; the stored value stays the COICOP code. Not built: favourites, and everyday
names outside the picker (lists and reports keep the COICOP labels).

COICOP names are statistical ("Individual consumption expenditure of non-profit institutions
serving households"). Keep COICOP as the storage format, but:

- show short everyday names ("Groceries", "Public transport") with the COICOP code beside them;
- put recent and favourite categories first;
- search everyday words ("Netflix" → 09.4 Recreational services).

#### F09 · Payees

**Status:** ☑ Done (2026-10-20, `2cc5766`)

**As built:** expenditures and income have a payee (transfers, exchanges and revaluations don't); templates,
*Duplicate* and recurring series carry it. `payees.csv` holds each payee once, compared without case, accents
and punctuation, with its *aliases*: the cleaned bank texts imported for it and its former names. Imports look a
text up there first and only then normalize it (`domain/payees.py`: dates, times, card, reference, terminal and
phone numbers, IBANs and postal codes go, so do the booking words of UBS, PostFinance, ZKB, Raiffeisen, Revolut
and TWINT in German, French, Italian and English, payment services such as `TWINT *` and `SumUp *`, and the
location at the end (the legal form stays: "Immo Muster AG", decision 11); about 170 brands, shops and banks in `resources/payees.json` get one
name, "MIGROS ZUERICH HB 1234" → "Migros"). The CSV import reads the payee from a counterparty column (*Empfänger*, *Payee*,
*Merchant* …) or the description, camt from the counterparty's name; the preview shows it and it can be edited
there, which teaches the alias. The form's payee field suggests known payees (recently used first) and takes a
new name as typed. Transactions has a Payee column, search and filter. The **Payees** page lists every payee
with its number of transactions and what was spent and received, and renames, merges (several → one name; their
transactions, templates, series and aliases move) and deletes unused payees, each as one undo step. Not built:
a "top merchants" report, and payees on transfers (QR-bill creditors became payees with F03). Category suggestions per payee
came with F07.

A payee or merchant field, normalized ("MIGROS ZUERICH HB 1234" → "Migros").

- Payee list with merge and rename; payee filter and search in Transactions.
- Basis for categorization (F07), subscription detection (F17) and "top merchants" reports.
- Imports fill it from the bank text; QR bills from the creditor.

#### F10 · Tags

**Status:** ☑ Done (2026-10-22)

**As built (decision 17):** `tags.csv` holds each tag with a colour from a fixed palette of nine (grey, red,
orange, yellow, green, teal, blue, purple, pink; shades for light and dark mode) and an optional budget (amount,
currency, optional start and end date). Transactions, templates and recurring series store the **ids** of their
tags in a `tags` column, separated by `|`, so a rename touches one row; older files load without tags. The
transaction form has a **Tags** field for every type (a spread booking follows its exchange and stays untagged), a
chip input with suggestions: existing tags are picked (typing a tag's name in any case picks it), a new name shows
as a dashed chip and becomes a tag only when the transaction is saved, in the same undo step. The recurring sheet,
templates, *Duplicate*, *Make Recurring…* and booked occurrences carry the tags; bulk edit (F13) has **Add Tags**
and **Remove Tags**. **Transactions** shows the tags as coloured chips (an AG Grid cell renderer; export and
sorting use the names), filters by tag (any of the chosen) and finds tag names in the search. The **Tags** page
(`/tags`, sidebar after Payees) lists each tag with its number of transactions, what was spent and received, and
with a budget a bar "540.00 of 2'500.00 CHF · 1'960.00 CHF left · 1 Jul 2026 – 31 Jul 2026" (red when over); a
click on a tag opens Transactions filtered by it (`?tag=<id>`); it adds tags, edits name and colour, merges,
deletes and sets budgets, each one undoable step. The Overview has a **Spending by Tag** chart (one bar per tag in
its colour, between two months; PNG, PDF, CSV and Excel like the other charts). **Decided while building:**
spending counts the own share of tagged expenditure (the part paid for others is an advance that comes back), and
tagged income other than repayments counts as received; a tag's budget compares only that spending within its
period, income does not reduce it. Deleting a tag in use is allowed: it is taken off every transaction, template
and series in one undoable action, and the confirmation says how many transactions lose it. The German word is
*Schlagwort* ("Tag" means day), French *étiquette*, Italian *etichetta*. Tags per item line came with F11 (a line's
tag counts that line's part). Not built: budgets per tag and month. Since F20, tags can be linked to sections of the tax summary.

Labels across categories: "Holiday Italy 2026", "Renovation", "Business".

- Several tags per transaction; filter, total and chart by tag.
- Budget by tag (for example a project budget across many categories).
- Required for the tax summary (F20), where "deductible" is a tag.

#### F11 · Split transactions

**Status:** ☑ Done (2026-10-22)

**As built (decision 14):** a split is a category per item line. In the transaction form each item line of an
expenditure has a split button that opens a row below it with the category picker (F08) and a tag field (F10) for
that line; lines without a category keep the transaction's. Below the list, "Rest in 01.1 Food: 23.40 CHF" shows
what stays in the transaction's category, live. `transaction_items.csv` got `category` and `tags` columns (older
files load unchanged). **How the amount is counted** (`domain.splits`, the one place every consumer uses): each
line with its own category (or tags) counts its total there, in CHF at the transaction's own rate rounded to the
cent; the rest (lines without a category, deposits, rounding, anything not itemised) stays in the transaction's
category and takes the rounding, so the parts add up to the amount and to `amount_chf` exactly. Lines with their
own category may not add up to more than the amount (Save refuses it with the totals); a negative line is a
discount on its category and raises the rest by as much. **Advances:** every part gives the same share
(advance / amount) to *Advance payments*, so the advance, its repayments and their settlement are unchanged.
**Counted per part:** the monthly statement and its export, budget vs. actual, the expenditure tree, the
*Income and Expenditure* chart series and their exports, the projection's "higher of recorded and budget", the
COICOP page's spending this year, and tag totals, budgets and the *Spending by Tag* chart (a line's tags count
that line, on top of the transaction's). Net income, account balances and the reconciliation use the whole
transaction and never change (tested). **Transactions** shows a split as "01.1 Food + 2 more"; the search finds
its lines' categories, and the tag column and filter include its lines' tags. Deleting or merging a tag rewrites
the lines too. **Bulk edit** changes only the rest's category (the hint says how many rows are split). Income,
transfers, exchanges and revaluations are not split: the form hides the split buttons for income and says that
lines with a category lose it when saved. Undo covers the lines (they are saved in the transaction's action).
Not built: a drill-down from a Budget category to its transactions (the page has none yet); recurring series
and templates have no items, so *Make Recurring…* of a split transaction books unsplit occurrences.

One payment, several categories: a supermarket receipt with food, household goods and a
gift.

- The transaction keeps one total and account; lines split the amount by category, and
  optionally by tag.
- Reports and budgets count each line in its own category.
- Interacts with advance payments: a split line can be the part paid for someone else.

#### F12 · Attachments

**Status:** ☑ Done (2026-10-22)

**As built:** every transaction type has an *Attachments* section in its form, under the note: a drop
zone (drag files onto it, or click to choose several), then one row per file with a thumbnail (photos)
or an icon, the name, size and date added, and *Open*, *Save a Copy…* and *Remove*. Clicking the name of
a photo or PDF shows it below the list (the PDF in the window's own viewer). In the edit sheet a file is
attached, or removed, at once, each with an Undo toast; a new transaction keeps the chosen files ("Attached
when you save") and stores them with it, in the same undo step, so a receipt can go with the expenditure
it belongs to. Accepted: PDF, photos (JPEG, PNG, HEIC, WebP), Word, Excel and OpenDocument files, text and
CSV, at most 20 MB each and checked by content as well as by extension (no HTML, SVG, scripts or old
binary Office files); the same check runs again before a file is opened, served or restored from a
backup, so a crafted backup can't place a program that *Open* would run. Files are copied to `attachments/<transaction id>/<attachment id>.<ext>` in the data
folder, never under the user's name; `attachments.csv` keeps the name. *Open* uses the system's viewer in
the app window and a link to `/attachment/<id>` in a browser; that route serves only files of existing
attachment records. Removing an attachment or deleting its transaction can be undone; the file is deleted
at a later start, once no undo can bring it back (architecture §7, decision 20). Backups contain the
files, a restore brings them back, and the copies made before an update or a restore keep them too. The
Transactions list has a paper-clip column with the number of files, and its search finds attachment
names. Not built: attaching from the Transactions list, sync, previews of office files and OCR (F04). Since
F03 a QR bill keeps its file as an attachment of the bill, which belongs to the payment once the bill is paid
(decision 22).

Keep receipts, invoices and contracts with the transaction.

- Files are stored in the data folder (`attachments/<transaction id>/`), so backups and sync
  include them.
- Preview in the edit sheet; open with the system viewer.
- Required by receipt scanning (F04); useful for warranty and tax proofs.

#### F13 · Bulk edit

**Status:** ☑ Done (2026-10-20)

**As built:** *Edit* in the Transactions selection bar opens *Edit {n} Transactions* when several
rows are selected (one row still opens the editor). Every field starts as *Leave unchanged*: the
expenditure category (the F08 picker) for expenditure rows, the income category for income rows
other than repayments, the payee (expenditure and income), the account (the From account of
expenditure, the To account of income,
offered from the accounts in the rows' currency and disabled when they mix currencies), and the
note (*Replace*, *Append* or *Clear*). Hints say how many rows a field leaves alone ("1 of 3 rows
is not expenditure and keeps its category"); transfers, exchanges and revaluations keep their
accounts; a spread booking (F44) follows its exchange and keeps its note. The status line says
how many rows change, and ⌘↩ / Ctrl+Enter saves. Each changed row goes through the same checks as
a single edit (advance payments and repayments included); if one fails, nothing is saved and the
failing rows are listed by date and amount. The whole edit is one undo step (*Edit several
transactions*); booked rows keep their recurring series, valuation, items and import reference.
Tags came with F10: *Add Tags* and *Remove Tags*. With F11, a split transaction changes only the category
of its rest; its lines keep theirs.

Select many transactions → change category, account, tag or note in one step. The selection
bar on the Transactions page already exists; this adds an *Edit* sheet for several rows.

### Planning and insight

#### F14 · Savings goals

**Status:** ☑ Done (2026-10-22)

**As built** (architecture §7, decision 23): `goals.csv` holds each goal: name, currency, a target amount or, for
an emergency fund, a number of **months of spending** (the target is N × the average recorded spending of the last
12 complete months, own share without advances, taxes included, counted from the first month with a transaction,
so it follows the spending), an optional target date, an optional linked account (else an amount *saved so far*
typed in), *reserve in the projection*, archived, and where the on-track line starts (the day it was added, or
linked to another account or given another currency, and what it held then). A linked account's balance is its
latest month-end balance plus what was booked on it since, up to today (holdings for securities and crypto
accounts), converted at the latest rate. Progress in `domain/goals.py`: the percentage (never 100 % before the
target), the **monthly amount needed** (what is left over the whole months to the target date, one less when the
date's day comes before today's, at least one; rounded up to the cent), *Reached*, *Overdue since …*, and **on
track / behind** against the straight line from the start to the target on its date; without a date "… to go · no
target date". The **Goals** page (`/goals`, sidebar after Budget Plan, icon `o_flag`) lists the goals by date with
a bar (green once reached), the plan line and the source; a sheet adds and edits (the months-of-spending target is
worked out live), rows archive and delete, archived goals can be restored; each change is one undoable step. The
**Overview** has a **Goals** card with the first three goals not archived (those still to reach first) and, when a
goal reserves, "Projected for Dec 2027: … free after … reserved for goals"; the card is left out without goals.
**Reserve:** the projection (F47) is unchanged; the *Wealth Over Time* chart adds a dashed *Free after goals* line
(and a `free_after_goals` column in its export): projected wealth minus what each reserving goal is to hold at that
month on its plan, today's amount plus the monthly amount needed per month and the full target from the month the
plan reaches it; a goal without a date reserves what it holds, an overdue or reached one its target. A goal's money
reserved stays reserved until the goal is archived, so a goal spent should be archived. Not built: reminders when
a goal falls behind (F31), contributions booked from the goal, several accounts per goal.

"Holiday 4'000 CHF by June", "Emergency fund 3 months of spending".

- Target amount and date, optionally linked to an account.
- Progress on the Overview; the monthly amount needed to reach it on time.
- Optionally reserves money in the projection, so the projected free wealth is honest.

#### F15 · What-if scenarios

**Status:** ☑ Done (2026-10-28)

**As built** (architecture §7, decision 30): `scenarios.csv` holds each scenario (name, note, *shown on the
Overview*), `scenario_changes.csv` its **changes**: for a category (expenditure or income) an amount in a currency,
the first month, the rhythm and the last month (or *to the end of the plan*), in the order they were entered. In the
scenario the budget of that category **is** that amount in those months instead of whatever the plan budgets there;
**0 takes the budget out** of those months ("drop the gym from March"); everything else stays as in the plan. Where
two changes of a scenario meet in a month and category, the later one counts. The scenario's projection is the plan's
(F47) with those budgets laid over: recorded amounts still count where they are higher, so a scenario never undoes
what has happened. The **Scenarios** page (`/scenarios`, sidebar after Goals, icon `o_alt_route`) lists each
scenario with "Projected for Dec 2027: 48'200.00 CHF · −9'600.00 CHF against the plan", its changes ("Salary ·
4'960.00 CHF every month · from Jan 2027 to the end of the plan"), *Add Change…*, a switch *On the Overview*, edit
and delete; the change sheet (type, amount, currency, category, From, Until or Forever, Repeat, note) says how many
months it covers and what the plan has for that category in the first month. The **Overview** wealth chart draws
every shown scenario as a dashed line of its own from the last recorded value, beside the plan and the goals' line,
and the chart export gets a `scenario: <name>` column each. **Adopt into Plan…** (confirmed) writes the changes into
the budget as one undo step: per change the plan's entries of that type and category in its months are replaced by a
series of the change's amount (none for 0), in entry order; every change is checked first, so nothing is written
when one lies beyond the plan's end; the scenario is then removed, and Undo brings back both the budget and the
scenario. Nothing is ever written into the plan until then. Not built: changes to recorded transactions or balances,
one-off amounts added on top of a budget (set the new total instead), scenarios of goals (F14) or of the free-wealth
line.

Compare futures: "What if I reduce to 80 % work from January?", "Buy a car for 25'000 CHF in
March".

- A scenario is a set of budget changes applied on top of the plan, never written into it.
- The Overview wealth chart shows the plan and each scenario as separate lines.
- **Decided** (decision 18): any number of scenarios, each can be adopted into the plan.

#### F16 · Budget from history

**Status:** ☑ Done (2026-10-22)

**As built:** *Propose from History…* on Budget Plan (beside *New Budget Entry*) opens one sheet. **Inputs:** the
last 3, 6 or 12 complete months (default 6), *Average* or *Median*, the COICOP level of the expenditure rows
(division, group (default) or class), the rounding (to the cent, 1, 5, 10 (default), 50 or 100 CHF), and From
(this month to the plan's end) and Until (a month or *Forever*, as in the New Budget Entry form). **Rows:** every
expenditure category with money in those months, in COICOP order with Taxes last, then the income categories by
name; each has a checkbox with the category, the months' values in whole francs (a tooltip names the months), the
budget it already has in the chosen months, and the proposed monthly amount, which can be edited (typed amounts
survive a change of From or Until, a new calculation replaces them). **How it counts** (`domain.proposal`,
`services.budget_history`; architecture §4.4, decision 24): in CHF at each transaction's own rate, whatever the
display currency; split lines in their own category (F11); the advance share and advance repayments left out;
transfers, exchanges and revaluations ignored; a month without spending counts as 0; months before the first
expenditure or income are left out, and the caption says so ("Your records start in Aug 2026, so 2 months
count"); the median of an even number of months is the mean of the middle two; rounding is half up and never
below 0. **Conflicts:** a row whose category already has a budget in the chosen months is unticked and marked
"Has a budget in 15 of these months. Tick to replace it."; ticking it replaces that budget in those months (the
form's *Replace*), earlier months keep theirs. A budget for a category inside or above the row's (01.1.1 under
01.1, or 01) is named as an overlap ("both would count"); the row starts unticked, and ticking it adds alongside.
A row that rounds to 0 starts unticked too. **Recurring:** a running series (F05) that books in the category is
named under the row ("Booked by a recurring transaction: Landlord AG · 04.1.1 …, 1'900.00 CHF (Every month)"); the
row stays ticked, because its bookings are part of the history and the projection does not use series, so nothing
is counted twice. **Creating:** the status line sums the ticked rows ("910.00 CHF spending and 140.00 CHF income a
month from Oct 2026, continued as the plan grows"); *Create Budget for N Categories* makes one monthly CHF series per
ticked row, with the note "From history: average of Apr 2026 – Sep 2026", all or nothing, as one action in the
change history (*Add budget entries from history*); the toast "Created N budget entries" (one per month and
category) offers Undo. On a phone each row stacks its amount below the category, and the buttons stay at the bottom
of the sheet while the rows scroll. Not built: a yearly series (insurance once a year) is proposed as a monthly
twelfth, which the projection's "higher of recorded and budget" (F47) then counts on top of the month it is paid;
plan such costs in their own month with New Budget Entry.

Propose a budget from what actually happened: the average (or median) of the last 3, 6 or 12
months per category, as a starting point the user edits. A one-sheet action on Budget Plan.

#### F17 · Subscription and recurring-spend detection

**Status:** ☑ Done (2026-10-28)

**As built** (architecture §7, decision 33): a **Subscriptions** page (`/subscriptions`, in the sidebar after
Recurring) with three groups. *Detected* lists the payees that charge again and again: expenditure is grouped by
payee (F09, compared by its lookup key, so "MIGROS" and "Migros" are one; a row without a payee by its note) and
currency, and a group is a subscription when its latest charges make a run: going back from the last charge, every
gap fits one rhythm (every month, two months, quarter, half year or year, within 4 days plus one per month of the
rhythm) and every charge is within 20 % or 2.00 of the median; a skipped or late charge, an irregular one or an
old price ends the run, so long imported histories still find today's subscription. The run needs at least three
charges (two for half-yearly and yearly rhythms), and its last one must be no older than two rhythms. Each row says what it costs a year, how much and how often, how many charges since
when, the last and the expected next date; the header sums the detected subscriptions per year in the display
currency. **Make Recurring…** opens F05's series sheet prefilled from the last charge (its payee, note, tags, the
most frequent category and account, the detected rhythm, the last charge as the series' first booking, so the
next occurrence is due a rhythm later); **Ignore** takes a group off the list for good (`subscription_ignores.csv`,
one undo step), and the closed *Ignored* group offers **Watch Again**. Rows a series booked (F05) are never
counted, and a group whose payee already has a running expenditure series is left out, so a subscription made
recurring disappears from *Detected* and appears under *Recurring Transactions*, which lists the running
expenditure series of F05 with their yearly cost and total. Nothing is booked by the page; the thresholds are
fixed and explained in `domain.subscriptions` (the README names them). In the demo data the rent, health
insurance, rail pass, phone and a quarterly card fee are detected, and a doctor's note seen twice half a year
apart (what *Ignore* is for); its streaming charges vary too much and are not. Not built: detecting price changes as a separate
event, grouping across currencies, half-month or weekly rhythms, and a notification when a subscription stops.

Find charges that repeat with similar amounts (streaming, phone, gym) and list them with
the yearly total. One click turns a detected subscription into a recurring transaction (F05).

#### F18 · Trends and unusual spending

**Status:** ☑ Done (2026-10-22)

**As built:** **Budget page:** *Compare with* above the two ledgers is a segmented control *Budget · Sep 2026 ·
Oct 2025* (the month before and the same month a year earlier, named). It changes the meaning of the existing
columns instead of adding any, so the ledgers keep their B08 layout: the *Budget* column becomes that month's
amount and *Difference* becomes *Change*, "▲ 25 %" / "▼ 100 %" / "new" (red when spending rose or income fell;
the amount in the cell's tooltip), and the ledger caption says "17 % less than in Sep 2026 (4'793.15 CHF)".
Below 560 px, where the difference column was hidden, compare mode shows the change instead of the other
month's amount (a fixed column, so the changes align). The budget meters and the pencil are hidden while
comparing; for the current month a caption says "Oct 2026 so far, against all of Sep 2026". A category or
income recorded only in the compared month gets its own row, so a stop shows as ▼ 100 %. Amounts are in the
page's currency and count per part (F11): a split line in its own category, the share paid for others in
*Advance payments*. **Overview:** a *Notable This Month* card after the wealth card lists at most five items,
exceeded budgets first, then large payments, categories well above their usual level and budgets on pace to be
exceeded; each row links to `/budget?month=…&category=…` (the month, the category's division opened and the row
marked and scrolled into view) or to `/transactions?month=…&category=…` (From and To set to the month, and a new
*Category* filter that includes subcategories and split lines). The card hides when nothing stands out (and
when nothing is recorded); with fewer than three recorded months it checks only budgets and says "Comparisons
with your usual spending start once 3 months are recorded." **Rules** (`domain.trends`, architecture §7,
decision 26; the README explains them): the usual level is the median of the last six complete months since the
first recorded expenditure; a group (COICOP level 2) is *well above usual* at 1.5 times it and 100 CHF more,
unless its budget covers it or one large payment explains half the excess; a payment is *large* from 250 CHF
when it is four times the median payment of its group (with three or more earlier payments), else from a
quarter of a usual month; a budget is *exceeded* 10 CHF above it and *on pace* when, from the 7th and with three
payments or more, spent ÷ day × days of the month ends 10 CHF above it (the most specific of nested budgets is
listed). CHF thresholds are converted to the display currency at the latest rate. The demo data has a washing
machine this month. Not built: notable items for past months, income trends on the Overview, and a chart of a
category's months (the *Income and Expenditure* chart shows that).

- Month-over-month and year-over-year change per category on the Budget page.
- A short "Notable this month" list on the Overview: categories well above their usual
  level, a large single transaction, a budget about to be exceeded.

#### F19 · Year in review

**Status:** ☑ Done (2026-10-28)

**As built** (architecture §7, decision 32): a **Year in Review** page (`/review`, in the sidebar before Tax
Summary: the two yearly pages close the list) with a year picker (last year by default when it has data, else
the latest year with data; this year is always offered as *so far*), the display currency and a share menu. A
past year is January to December; the current year is reviewed *so far*, January to the current month, and
compared with the same months of the year before. The metric strip shows income, expenditure, **saved** (the
hero, with the savings rate and the year before's) and wealth at the end of the last complete month (with the
change since the end of the year before, and the year before's change); every amount has the year before's
below it. Then a bar chart of income and expenditure per month, *Spending by Category* (the statement's
top-level rows: divisions, Taxes, Advance payments, each with the year before, its share of the year's spending
and the change as ▲/▼ and a percentage, red when spending rose, the amount in the tooltip; *new* and ▼ 100 %
for what only one year had), *Income* per category (red when it fell), and the five *Biggest Expenses* (the own
share of a payment, F11; a button opens the transaction). The figures are the monthly statements added up
(`aggregates`, per part: a split line in its own category, the share paid for others under *Advance payments*
and its repayments as income), so the year agrees with the Budget page month by month; wealth is the recorded
month-end wealth, nothing is projected. **Save as PDF…** renders the review with the chart as the browser draws
it (the Overview's chart export, `toImage`; the PDF does without when that fails) and the tables; **Export as
Excel…** gives one sheet per part (Summary, Months, Expenditure, Income, Biggest Expenses). Both are
formula-safe and in the interface language. Not built: a comparison with the budget of the year, revaluations
as a line of their own (they are in the wealth change), and a review per tag or account.

A yearly report: income, spending by category, savings rate, wealth change, biggest
expenses, comparison with the previous year. On screen and as a PDF, built on the existing
statement export.

#### F20 · Swiss tax summary

**Status:** ☑ Done (2026-10-22)

**As built** (architecture §7, decision 25): a **Tax Summary** page (`/tax`, last in the sidebar: used once a year,
it draws on categories, tags and investments) with a year picker (last year by default) and nine sections, each
with its total in CHF and, opened, its rows (payee or note, date, category, amount, the tags that brought a row in,
the attachments as links, a button that opens the transaction): *professional expenses* (07.3 itself, rail, bus,
fare network passes; workplace canteens; post-secondary, tertiary and other education), *pillar 3a payments*
(transfers into an account of the asset class pillar 3a from one of another class), *insurance premiums* (12.1
itself, life and accident, health), *medical and dental costs* (06, and 14.2, 15.2), *donations* (14 itself, 14.5,
14.6), *childcare* (13.3.0.1), *debt interest* (12.2.1, mostly by tag), *withholding tax to reclaim* (the tax of
dividends in CHF accounts, F21) and *dividends and interest* for the securities list (every dividend gross with the
tax withheld, ISIN in the payee, and income in a category named Dividends or Interest in any language). Split
lines (F11) count in their own category, the share paid for others doesn't count. **Sections…** links tags to
sections (`tax_sections.csv`, a tag in one section at most, new names become tags, one undo step); a tagged
expenditure part, income or transfer then counts there too, income in a spending section as a refund (negative).
Sections can overlap only by a tag, and the page says so (and how many transactions do). Exports: **PDF** (a
summary page with the note that cantonal rules differ, then a table per section with date, payee, category, amount
and the attachment names as *Proof*), **CSV** (one row per section row, formula-safe) and **Proofs as ZIP…** (the
PDF, the CSV and the attachment files in one folder per section, each read through F12's check; files that fail
it are left out and named). Not built: other deductions (property maintenance, alimony, the second-earner
deduction), amounts per canton, and a link from a section to Transactions. To confirm with the product owner: the
narrow code lists (professional expenses, insurance and donations leave out what is rarely deductible).

Help with the annual tax declaration, without trying to file it:

- yearly totals for typically deductible items: professional expenses, pillar 3a payments,
  insurance premiums, medical costs, donations, childcare, debt interest;
- uses COICOP codes where they match and tags (F10) otherwise;
- export as PDF or CSV with the supporting transactions and attachments (F12).

Cantonal rules differ, so the app lists amounts and leaves the judgment to the user.

### Wealth

#### F21 · Investments and asset classes

**Status:** ☑ Done (2026-10-21; dividends with withholding tax and conversions, decisions 12 and 13, on 2026-10-22)

**As built** (decisions 9, 12 and 13; architecture §4.6, §7 decisions 17 and 18):

- **Asset classes:** every account has one (`accounts.csv` → `asset_class`): cash (the default,
  also for files written before and for advance-payment accounts), securities, pillar 2, pillar 3a,
  real estate, crypto, other. It is set in the account sheet in Settings. Securities and crypto
  accounts are valued from their holdings; an account without transactions or balances becomes one
  right away, and stays one while it has holdings. The Overview shows wealth at the end of last
  month by class (a split bar with amounts and shares) once any account is not cash, and the chart
  *Wealth by Asset Class* stacks it month by month. Pillar 2/3a, real estate and other keep their
  recorded month-end balances.
- **Converting an account with history** (decision 13): choosing Securities or Crypto for an account
  with transactions or balances opens a conversion sheet – the conversion month (this month by
  default) and the opening holdings (name, optional ISIN/symbol, quantity, price; rows added and
  removed), with the last recorded balance, the opening value and the difference shown live. The
  account keeps its recorded balances up to the month before (`accounts.csv` → `invest_from`); those
  from the conversion month on are replaced, and its value comes from the holdings, which start as
  *opening* trades on the first day of that month (no money moves; their value counts as money put
  in, so no gain). The difference between the last recorded balance and the opening value is booked
  as one linked *Capital gains on investment* revaluation, so the conversion month reconciles to 0
  (tested in CHF and EUR). Refused while a transaction is dated in or after that month or a recurring
  series uses the account. Transactions and balances before the month stay editable; trades and
  prices before it are refused. An opening holding can be edited (quantity, price) or deleted; the
  revaluation follows. One undo step. Converting back stays refused while holdings exist, with a
  message that says to delete them on the Investments page first.
- **Holdings, trades, prices:** `holdings.csv` (account, name, ISIN or symbol), `trades.csv` (buy,
  sell, dividend, opening with date, quantity, price, fee, amount, tax, cash account, note) and
  `prices.csv` (one price per holding and day). Every change rebooks the holding's linked transactions in date order
  (`invest_id` points at the trade or price): a buy or sale is a transfer of quantity × price
  between the cash account (same currency, else refused) and the investment account, a fee an
  expenditure in 12.2.9.9 *Other financial services n.e.c.*, a dividend its gross as income in
  *Dividends* (added when missing) and its withholding tax as an expenditure in *Taxes*, both from
  the same cash account, and each price — a trade's price counts as one — a *Capital gains on
  investment* revaluation of quantity × the price change, rounded so the booked value is exactly
  round(quantity × latest price, 2). A sale of more than is held, or an edit or delete that would
  leave one, is refused; dates in the future are refused.
- **Month-end balance:** computed from the holdings (Σ quantity × latest price on or before the
  month end), not typed in and not stored; the reconciliation comes out at 0 (tested, also for a
  foreign-currency depot in another view currency).
- **Dividends** (decision 12): the sheet asks for the gross dividend, the withholding tax (35 % of the
  gross prefilled in CHF accounts, 0 in other currencies, editable; it follows the gross until typed
  in) and the cash account, and shows the net that account receives. `trades.csv` gained a `tax`
  column; older dividends load with tax 0 (their amount is the gross) and re-book correctly when
  edited.
- **Reclaimable withholding tax:** the Investments page lists *Reclaimable withholding tax {year}*
  for this year and the two before (the tax on dividends in CHF accounts), and the holding sheet shows
  the tax and the net in each dividend's history line (foreign tax as *foreign withholding tax*).
- **Performance:** per holding and in total: value, money put in (opening value + buys − sales),
  net dividends, fees, gain = value − money put in + net dividends − fees and its percentage of the
  money put in; the holding sheet shows the withholding tax apart and splits the gain into realized
  and unrealized (average cost).
- **Investments page** (`/investments`, sidebar after Payees): holdings by account with quantity,
  last price and date, value, gain and %, totals; *Buy*, *Sell*, *Dividend*, *Update Price* and
  *Paste Prices…* (tab, semicolon or comma separated, header line skipped, Swiss and other number
  formats) sheets; a holding sheet with its figures and history (edit and delete per trade or
  price, rename, delete with everything it booked). The booked rows appear in Transactions; opening
  one opens its trade or price, deleting or bulk-editing it there is refused. Every save is one
  undoable step.
- Not built: online prices (decision 9), transaction-level lots (the average cost method is used),
  editing a conversion's month afterwards (undo it and convert again).

The proposal:

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
- **Decided** (decision 19): one computer at a time, with a warning and reload; no merging.

#### F27 · Encryption and app lock

**Status:** ☐ Open

The CSV files are readable by anyone with access to the computer.

- **App lock:** password, or Touch ID / Windows Hello, before the app opens.
- **Encryption at rest:** data files encrypted with a key derived from the password.
- **Trade-off:** encrypted files can no longer be opened in Excel, and a forgotten password
  means lost data, so a recovery key is needed.
- **Decided** (decision 20): the lock for everyone who sets a password; encryption at rest as an
  opt-in, with a recovery key.

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

**Status:** ☑ Done (2026-10-28)

**As built** (architecture §7, decision 27): the sample set of `scripts/demo_data.py` moved into the package
(`services/demo.py`), so the installed app can write it: six accounts (bank, savings, a EUR account, a securities
depot, a crypto wallet and a pillar 3a account), a year of salary, rent, groceries with receipts, a holiday in
Italy, a bathroom renovation, a split receipt, dividends and prices, month-end balances, budgets, templates, tags
with budgets, goals and tax sections, dated relative to today and written through the services like typed-in
records; the script is a thin wrapper that empties a folder and calls it (`just demo`). **Welcome screen**
(`/welcome`, no sidebar): the Overview opens it while the data folder holds no record and it was not passed before
(`settings.onboarding_done`), so a folder with records never sees it. It has the language (applied at once), the
display currency, the data folder (its path and how to change it: `--data-dir` or `BUDGET_DATA_DIR`) and two
ways in: **Open Settings** (accounts and income categories; the Budget page then records the balances) or **Load
Sample Data**, which writes the set (a moment, in a worker thread) and opens the Budget page. The folder is marked
(`settings.sample_data`): the sidebar footer says *Sample data*, and **Settings → Data → Sample Data** offers
*Remove Sample Data…*, which deletes every record, attachment and the change history (language and currency stay)
and brings the welcome screen back; the same row loads the set into an empty folder. Without exchange rates
(offline on the first start) the set is CHF only. **Decided:** the sample data lives in the data folder itself,
and only while the folder is empty, rather than in a second folder: the app has one store, one lock and one
backup marker per folder, and switching folders is what profiles (F34) are for; a user with records tries the
sample set with `--data-dir`. Not built: a step-by-step guide for accounts, balances and the horizon (Settings
and the Budget page lead there), a folder picker.

- A first-start guide: language, display currency, accounts with their current balance,
  income categories, plan horizon. It ends on the Budget page with something to see.
- *Try with sample data:* a separate demo data folder to explore without touching real data.

#### F31 · Reminders and alerts

**Status:** ☑ Done (2026-10-28), in the app only

**As built** (architecture §7, decision 31): a **Reminders** page (`/reminders`, sidebar after Bills, icon
`o_notifications_none`, with a badge counting its items) lists, while the app runs, what wants doing or looking at,
grouped by kind and each row a link to where it is dealt with: **balances to record** (regular accounts without a
balance for last month whose last balance isn't zero, the Budget page's rule; links to that month), **recurring
transactions due** (one per due occurrence, F05; "Due since …" in red), **bills due** (open QR bills overdue or due
within a week, F03), **budgets exceeded or about to be** (the exceeded and on-pace items of *Notable this month*,
F18's rules and thresholds; links to the category on the Budget page) and **goals behind or overdue** (F14; "Behind:
… short of the plan line", "Overdue since … · … to go"). *Settings → Reminders* has one switch per kind (all on;
`settings.json` → `reminders_off`); a kind turned off leaves the page and the badge. Everything is computed from
the records when a page opens or data changes; nothing is stored per reminder, nothing is sent anywhere – no e-mail,
no system notification, no network, no background helper while the app is closed. Not built: desktop notifications,
reminders while the app is closed, snoozing or dismissing a reminder (deal with the item or turn the kind off).

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

**Status:** ☑ Done (2026-10-28)

**As built** (architecture §7, decision 28): **off by default**. **Settings → Updates → Check for Updates** is
a switch whose caption says what it does and what it never does: once a day, when on, the app reads the newest
release's version number from GitHub's releases API for this repository (`services/updates.py`, the same kind of
read-only request as the ECB rates, capped at 1 MB); nothing is downloaded or installed, and the request carries
nothing about the user or the data. Turning it on checks at once; *Check Now* checks again; the row below says
*Not checked yet* or "Checked on 28 Oct 2026: the newest release is 0.2.0" and links to the releases page. When
the release is newer than the running app, the sidebar footer of every page and the Settings row link **Budget
0.2.0 is available** to that release's page (installers and update steps), opened in the browser. The check runs
with the rate refresh (startup, then every 30 minutes) but asks at most once a day; a failed request is logged and
tried again at the next refresh, never shown. Turning the switch off forgets the result. Only a tag of the form
`v1.2.3` is accepted and the link is built from it, never taken from the answer; a dev build is never "behind".
**Decided:** the check stays off until the user turns it on (cross-cutting decision 1: nothing leaves the computer
on its own), and the README's privacy note names it as the one optional connection besides the ECB rates. Not
built: downloading or installing the update, release notes in the app.

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
- **Decided** (decision 21): quick add on the home network only, behind the app lock.

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

1. **AI and online services** (F04, F07, F21, F33): decided on 2026-10-21 — **never for now**;
   everything stays local, and features that need a service stay in the backlog until revisited. F04 reads
   receipts with a local OCR engine (RapidOCR) instead.
2. **Exchange rate of transactions** (F01, F02, F03, F05, B03): decided — every transaction
   uses the ECB rate of its date (the last earlier rate on weekends and holidays); account
   balances use the last rate of the month; budget entries use the latest rate. Changed by
   issue 01: manual entries used the latest rate before.
3. **Data format changes:** payees, tags, splits and attachments add fields and files. Each
   needs a default for old data (the update backup covers mistakes); see
   [packaging/README.md](../packaging/README.md#releasing-an-update).
4. **Languages** (F29): decided and built — English, German, French and Italian. Every new
   text needs all three translations; the tests enforce it. The COICOP notes (F40) have
   their own translation files, done for German, French and Italian.

## Done

### Milestone 1 · Bugs from issue 01

Merged into `main` on 2026-10-19.

| Step | Bug | What changed | Commit |
| --- | --- | --- | --- |
| 1 | **B01** Excel and PDF buttons in the budget overview did nothing; **B02** chart image, CSV and PDF export didn't work | The app window (pywebview) drops browser downloads. Every export now goes through one helper: the system save dialog in the app window, a normal download in the browser. Charts gained *Save as PDF…*. | `cc05db9`, `ffa9733` |
| 2 | **B05** Selecting and adding a chart didn't work | Adding worked but put the card out of sight. A new chart now goes first, scrolls into view and is highlighted; errors are shown. | `8e9ca8e`, `ffa9733` |
| 3 | **B07** Accounts didn't adapt to the window; **B08** tables should stack when narrow | Account rows are a grid that follows the card's width; the ledgers use container queries. The tables stay below each other, and since decision 1 sit side by side once each gets about 750 px (windows from about 1,800 px). | `6f34640`, B08 follow-up |
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
| F12 · Attachments | ☑ Done | Receipts and invoices with their transaction, in the data folder and in backups; details under F12. |
| Item lists on transactions | ☑ Done | Name, quantity and unit price per expenditure or income; search finds item names. F11 added a category and tags per line; gives F04 a place for receipt lines. |
| F03 · Swiss QR-bill scanning | ☑ Done | QR bills from PDFs and photos into a Bills list with due dates, paid through the transaction sheet; details under F03. |
| F04 · Receipt scanning | ☑ Done | A till receipt's PDF or photo read on the computer (RapidOCR) into a prefilled expenditure with its lines and the file attached; details under F04. |
| F30 · Onboarding and demo data | ☑ Done | A welcome screen on the first start (language, currency, data folder) and a year of sample data to load and remove; details under F30. |
| F33 · In-app update check | ☑ Done | Off by default; when on, reads the newest release's version from GitHub once a day and links to it, never downloads; details under F33. |
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
