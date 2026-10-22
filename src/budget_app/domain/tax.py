"""Swiss tax summary (F20), pure: the sections of the summary and the COICOP codes each one covers.

The summary helps with the yearly tax declaration without filing it: it lists the year's amounts per typical
deduction area and leaves the judgement to the user, since cantonal rules differ (architecture §7, decision 23).

A section collects the expenditure of its COICOP **codes** (each code with all its subcategories) and **exact** codes
(that code only, for a parent whose other children don't belong there, such as *07.3 Passenger transport services*
without flights), plus whatever carries a tag the user links to it (`services.tax`). Three sections have a rule of
their own (`rule`): transfers to pillar 3a accounts, the reclaimable withholding tax on dividends (F21) and the
dividends and interest for the securities list. Split lines (F11) count in their own category.

The code lists never overlap (tested), so a category puts a transaction (or a split line) in one section at most;
it can appear in a second section only through a tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from budget_app.domain.coicop import get_coicop
from budget_app.i18n import N_

PROFESSIONAL = "professional"
PILLAR3A = "pillar3a"
INSURANCE = "insurance"
MEDICAL = "medical"
DONATIONS = "donations"
CHILDCARE = "childcare"
DEBT_INTEREST = "debt_interest"
WITHHOLDING = "withholding"
SECURITIES = "securities"

# the rules of the sections that are not (only) found by category
RULE_PILLAR3A = "pillar3a"  # transfers to an account of the asset class pillar 3a from one of another class
RULE_WITHHOLDING = "withholding"  # the withholding tax of dividends in CHF accounts (F21, decision 12)
RULE_SECURITIES = "securities"  # dividends (F21) and income in the categories Dividends or Interest


@dataclass(frozen=True)
class Section:
    """One area of the summary, such as professional expenses."""

    key: str
    title: str  # English, marked with N_; shown with t()
    note: str  # what it covers, marked with N_
    codes: tuple[str, ...] = ()  # COICOP codes counted with all their subcategories
    exact: tuple[str, ...] = ()  # COICOP codes counted on their own, without their subcategories
    rule: str = ""
    income: bool = False  # income counts positive and spending negative (else the other way round)

    def covers(self, code: str) -> bool:
        """Whether expenditure in `code` belongs to this section by its category."""
        if code in self.exact:
            return True
        return any(c in self.codes for c in get_coicop().ancestors(code))

    @property
    def all_codes(self) -> tuple[str, ...]:
        """Every code the section names, in COICOP order (for showing which categories it uses)."""
        return tuple(sorted({*self.codes, *self.exact}, key=_code_order))


def _code_order(code: str) -> tuple[int, ...]:
    return tuple(int(p) for p in code.split("."))


SECTIONS: tuple[Section, ...] = (
    Section(
        PROFESSIONAL,
        N_("Professional expenses"),
        N_("Commuting by public transport, meals at work and further education"),
        # train and tram, bus, fare network passes; canteens other than at school; post-secondary, tertiary and
        # other education (courses). Flights, taxis and boats stay out, as do school fees of children.
        codes=("07.3.1", "07.3.2.1", "07.3.5", "11.1.2.9", "10.3", "10.4", "10.5"),
        exact=("07.3", "07.3.2", "11.1.2"),
    ),
    Section(
        PILLAR3A,
        N_("Pillar 3a payments"),
        N_("Transfers to accounts of the asset class pillar 3a"),
        rule=RULE_PILLAR3A,
    ),
    Section(
        INSURANCE,
        N_("Insurance premiums"),
        N_("Health, accident and life insurance"),
        codes=("12.1.1", "12.1.2"),  # household, building, car and liability insurance are not listed
        exact=("12.1",),
    ),
    Section(
        MEDICAL,
        N_("Medical and dental costs"),
        N_("Doctor, dentist, hospital, medicines and other health costs you paid yourself"),
        codes=("06", "14.2", "15.2"),  # health; health services of non-profits and of the state
    ),
    Section(
        DONATIONS,
        N_("Donations"),
        N_("Donations to charities and memberships of non-profits"),
        # charitable donations, religion, parties, unions and associations, environmental protection; the
        # division itself is "Donations and memberships" in the category picker
        codes=("14.5", "14.6"),
        exact=("14",),
    ),
    Section(
        CHILDCARE,
        N_("Childcare"),
        N_("Day care, after-school care and other childcare by third parties"),
        codes=("13.3.0.1",),
    ),
    Section(
        DEBT_INTEREST,
        N_("Debt interest"),
        N_("Interest on mortgages, loans and credit cards; mostly found by tag"),
        codes=("12.2.1",),  # COICOP's interest margin of lenders, credit card issuers and the like
    ),
    Section(
        WITHHOLDING,
        N_("Withholding tax to reclaim"),
        N_("The Swiss withholding tax on dividends in CHF accounts"),
        rule=RULE_WITHHOLDING,
    ),
    Section(
        SECURITIES,
        N_("Dividends and interest"),
        N_("For the securities list: gross dividends with their withholding tax, and interest received"),
        rule=RULE_SECURITIES,
        income=True,
    ),
)
BY_KEY = {s.key: s for s in SECTIONS}

# income categories that count for the securities list, by their name in any language (compared folded)
INCOME_NAMES = (N_("Dividends"), N_("Interest"), N_("Interest income"))


@cache
def section_of_code(code: str) -> Section | None:
    """The section whose categories cover `code`, if any (at most one: the code lists don't overlap)."""
    return next((s for s in SECTIONS if s.covers(code)), None)
