# UX review before 1.0

A review of the app as it stands after step 25 (2026-10-29), done on the running app with the sample data, in
WebKit (the engine of the macOS window) at 1440 × 900 and 390 × 844, in light and dark mode. It looks at what a user
has to understand to get something done, not at the code. The standard is the one of
[DESIGN_REVIEW.md](DESIGN_REVIEW.md): clarity, deference to content, one clear hierarchy per screen.

Nothing here is built yet, apart from the two fixes at the end. The proposal needs the product owner's approval
before 1.0.

## Verdict

Each feature works and most screens are tidy on their own. The app as a whole is confusing, because it grew by
adding a page per feature: the sidebar went from four places (the redesign of DESIGN_REVIEW.md) to eighteen, and
several of them answer the same question in different ways. A user doesn't know where to look, and the pages
contradict each other: *Recurring* says "No recurring transactions yet" while *Subscriptions* has found six, and
*Reminders* repeats two items of the Overview's *Notable This Month*.

The visual language holds up (type, colour, dark mode, the ledgers). The problems are structure, consistency
between pages, and text.

## 1 · Structure: eighteen places, four answers to one question

The sidebar lists every feature at the same level:

> Overview · Budget · Transactions · Recurring · Subscriptions · Bills · Reminders · Budget Plan · Goals · Scenarios ·
> Categories · Payees · Tags · Investments · Loans · Shared Expenses · Year in Review · Tax Summary · Settings

- **"What's coming up?" has four places.** Recurring (due occurrences), Bills (QR bills due), Reminders (all of it
  again, plus budgets and goals) and Loans (payments due). Two of them have badges. The user can't tell which
  one is the real list.
- **"What repeats?" has two.** Recurring (series the user made) and Subscriptions (series the app suggests).
- **"What's my plan?" has three.** Budget Plan, Goals and Scenarios are all about the future, on separate pages.
- **Lists everyone needs rarely sit beside daily work.** Categories, Payees and Tags are opened a few times a year,
  yet take three of the top places.
- **Settings holds data.** Accounts, advance-payment accounts and income categories are records, not preferences,
  so the Settings page runs to two screens (language, plan, projection, accounts, categories, rules, reminders,
  profiles, security, data, backups, updates).
- **The digit shortcuts (F32) reach only the first nine places**, which shows the list is too long.

### Proposal: seven places and Settings

```text
  + New Transaction

  Overview        what you have, what's notable, what's due (the one "inbox")
  Budget          this month: actual against budget            (unchanged)
  Transactions    the ledger; Import and Receipt as actions; Recurring as a tab
  Plan            the budget as series, with Goals and Scenarios as tabs
  Wealth          accounts and balances, Investments, Loans, who owes you
  Reports         Year in Review, Tax Summary
  ───────────
  Settings        preferences only; Categories, Payees, Tags, Rules as a "Lists" section
```

- **One "Due" list, on the Overview,** replaces Reminders as a page and the due parts of Recurring, Bills and Loans:
  every item says what it is and has its action in the row (*Book*, *Pay*, *Record balance*). The sidebar keeps one
  badge, on Overview. Bills stays as the list of open QR bills under Transactions (a bill is a transaction waiting).
- **Recurring and Subscriptions become one tab** under Transactions: *Your series* first, then *Found in your
  transactions* with *Make Recurring…*. The empty state then never says "nothing" while the app has found six.
- **Plan** shows the plan as what the user made: one row per series ("Rent · 1'900 CHF · every month · from Oct 2025,
  forever") instead of 189 monthly rows that each say "Every month, forever". The month grid stays as a second view
  for editing one month. Goals and Scenarios are tabs of Plan, since both are read against it.
- **Wealth** gathers Accounts (out of Settings, with their balances), Investments, Loans and *Shared expenses* (who
  owes you is a receivable, decision 1).
- **Reports** gathers the two yearly pages.

The routes stay, so links, shortcuts and tests keep working; the change is the sidebar and where pages group.

## 2 · Consistency between pages

- **Two page widths.** Overview, Budget, Transactions and Budget Plan use the wide column; Recurring, Subscriptions,
  Bills, Reminders, Goals and Loans a narrow centred one. Switching pages moves the title from the left edge to the
  middle. One width for all, with narrow content left-aligned in it.
- **An instruction paragraph under every title.** "Charges that come back at the same rhythm…", "Rent, premiums,
  subscriptions: transactions that repeat…". Read once, skipped forever, and it pushes content down. It belongs in
  the empty state and in a help link, not above the content.
- **Primary actions are duplicated.** *New Transaction* is in the sidebar and again on Transactions; Recurring has
  *New Recurring Transaction* and *Add Recurring Transaction…*; Budget's primary button is *Record Balance*, which
  isn't what the page is for. One primary action per screen, the one the page exists for.
- **Toolbars grow sideways.** Transactions has Search, Filters, Export, Receipt…, Import… and New Transaction in one
  row, and "Receipt…" is cut off. Search and Filters stay; Import, Receipt and Export go into one *More* menu.
- **Grid headers carry a filter icon per column** in Transactions and Budget Plan, besides the *Filters* button, and
  one column has no header at all. One way to filter.

## 3 · Text and naming

- **Two names for the same category.** The category picker offers everyday names ("Groceries · 01.1", "Household and
  furniture · 05", F08); every list and report then shows the official COICOP name ("01.1 Food", "05 Furnishings,
  household equipment and routine household maintenance"). A user picks "Groceries" and never sees the word again.
  The everyday names exist for 82 codes and miss common ones (11.1 restaurants, 09.4 leisure). Proposal: fill the gaps
  and use the everyday name everywhere, with the COICOP code and name in the tooltip and on the Categories page.
- **Transfers show no accounts.** "Account transfer · 800.00 CHF" doesn't say from where to where. "Bank → Savings".
- **Truncation in places that have room.** "Cash and a…" (By Asset Class), "System (En…" (Language), "Receipt…".
- **Rows that wrap to three lines.** Subscriptions puts amount, rhythm, count, first, last and next charge in one
  caption. Two facts are enough on the row (*49.90 CHF every month · next 15 Nov*); the rest belongs in the details.
- **Empty COICOP divisions** (02, 03, 10, 13, 14, 15) take six of the Budget page's seventeen expenditure rows. They
  are dimmed, as designed, but "Individual consumption expenditure of Non-profit Institutions Serving Households"
  will never hold a household's spending; hide empty divisions unless budgeted, behind *Show all*.

## 4 · Smaller things

- The Subscriptions detection lists a doctor seen twice half a year apart; a half-yearly rhythm should need three
  charges, like the others, or be marked "possible".
- *Profiles* shows the full data-folder path of the only profile; with one profile the group can be a single
  *Add a Profile…* row.
- The *Advance payments* row in the Budget ledger and *Shared Expenses* describe the same money with two names.

## Priorities for 1.0

| Priority | Change | Size |
| --- | --- | --- |
| **Before 1.0** | The seven-place sidebar, with Due on the Overview and the merged pages above | L |
| **Before 1.0** | Plan as series (one row per series), month grid as second view | M |
| **Before 1.0** | One page width, one header pattern, no instruction paragraphs, one primary action | M |
| **Before 1.0** | Settings for preferences only; accounts to Wealth, lists to a Lists section | M |
| **Before 1.0** | Everyday category names everywhere (gaps filled); transfers with their accounts | M |
| After 1.0 | Toolbar *More* menu, one filter mechanism in grids | S |
| After 1.0 | Shorter subscription rows, empty divisions hidden, the small things of section 4 | S |

## Decisions for the product owner

1. **The seven places** of section 1 (Overview, Budget, Transactions, Plan, Wealth, Reports, Settings): approve, or
   name what should stay a place of its own.
2. **Reminders as a page goes**; its items become the Due list on the Overview, with one badge.
3. **Accounts and income categories move out of Settings** (accounts to Wealth, categories to Lists).
4. **The Plan page shows series**, with the monthly grid as a second view.
5. **Explanations move out of page headers** into empty states and a help link.
6. **Everyday category names in every list and report**, the COICOP name in the tooltip (the code stays visible).

## Fixed already (branch `feat/ux-v1`)

- **The file dialog didn't open in the macOS window** (*Choose File…* on Import, *Choose Backup…*, and the drop
  zones of bills, attachments and receipts). The click asked the server to open it, and WebKit allows a file dialog
  only during the click itself. It now opens in the page (`layout.picks_files`); checked in WebKit.
- **Sidebar items were as wide as their label**, so every highlight had another width. They now take the sidebar's
  width.
