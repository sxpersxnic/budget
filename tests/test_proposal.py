"""Budget from history (F16), the pure part: basis months, COICOP levels, average and median, rounding."""

import pytest

from budget_app.domain import proposal
from budget_app.domain.coicop import ADV, TAX
from budget_app.domain.proposal import AVERAGE, MEDIAN


# ------------------------------------------------------------------ basis months
def test_basis_is_the_complete_months_before_the_current_one():
    assert proposal.basis_months("2026-10", 3, "2020-01") == ["2026-07", "2026-08", "2026-09"]
    assert proposal.basis_months("2026-10", 6, "2026-04") == [f"2026-0{m}" for m in range(4, 10)]
    assert len(proposal.basis_months("2026-02", 12, "2020-01")) == 12
    assert proposal.basis_months("2026-02", 12, "2020-01")[0] == "2025-02"  # across the year


def test_basis_starts_with_the_first_record_when_that_is_later():
    assert proposal.basis_months("2026-10", 12, "2026-08") == ["2026-08", "2026-09"]
    assert proposal.basis_months("2026-10", 6, "2026-09") == ["2026-09"]


def test_no_basis_without_complete_months_with_records():
    assert proposal.basis_months("2026-10", 6, "2026-10") == []  # records only in the current month
    assert proposal.basis_months("2026-10", 6, None) == []  # no records at all
    assert proposal.basis_months("2026-10", 0, "2020-01") == []


# ------------------------------------------------------------------ COICOP levels
def test_codes_count_in_their_ancestor_at_the_level():
    assert [proposal.at_level("01.1.1.1", level) for level in (1, 2, 3)] == ["01", "01.1", "01.1.1"]
    assert proposal.at_level("04.1.1", 2) == "04.1"


def test_codes_not_deeper_than_the_level_stay_where_they_are():
    assert proposal.at_level("01", 2) == "01"
    assert proposal.at_level("01.1", 3) == "01.1"
    assert proposal.at_level(TAX, 1) == TAX
    assert proposal.at_level(TAX, 3) == TAX
    assert proposal.at_level(ADV, 2) == ADV
    assert proposal.at_level("not a code", 2) == "not a code"


def test_amounts_are_summed_at_the_level():
    amounts = {"01.1.1": 10.0, "01.1.2.1": 5.0, "01.2": 3.0, "01": 1.0, TAX: 7.0}
    assert proposal.by_level(amounts, 1) == {"01": 19.0, TAX: 7.0}
    assert proposal.by_level(amounts, 2) == {"01.1": 15.0, "01.2": 3.0, "01": 1.0, TAX: 7.0}
    assert proposal.by_level(amounts, 3) == {"01.1.1": 10.0, "01.1.2": 5.0, "01.2": 3.0, "01": 1.0, TAX: 7.0}


# ------------------------------------------------------------------ average and median
def test_average_and_median():
    assert proposal.statistic([100, 200, 600], AVERAGE) == 300
    assert proposal.statistic([100, 200, 600], MEDIAN) == 200
    assert proposal.statistic([100, 200, 300, 600], MEDIAN) == 250  # the middle two
    assert proposal.statistic([], AVERAGE) == 0
    assert proposal.statistic([], MEDIAN) == 0
    with pytest.raises(ValueError, match="unknown method"):
        proposal.statistic([1], "mode")


def test_months_without_spending_count_as_zero():
    once = [600, 0, 0, 0, 0, 0]
    assert proposal.propose(once, AVERAGE, 0.01) == 100
    assert proposal.propose(once, MEDIAN, 0.01) == 0  # spent in one month of six: the median month spent nothing


# ------------------------------------------------------------------ rounding
@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (12.5, 1, 13),
        (12.49, 1, 12),
        (2.5, 5, 5),
        (7.49, 5, 5),
        (7.5, 5, 10),
        (123.4567, 0.01, 123.46),
        (0.125, 0.01, 0.13),  # half up, not to even, and no float surprise
        (1234, 100, 1200),
        (1250, 100, 1300),
        (333.333, 10, 330),
        (-12.5, 1, -13),
        (0, 10, 0),
    ],
)
def test_round_to_the_nearest_step(value, step, expected):
    assert proposal.round_to(value, step) == pytest.approx(expected)


def test_a_proposal_is_rounded_and_never_negative():
    assert proposal.propose([333, 388, 326, 326, 398, 400], AVERAGE, 10) == 360
    assert proposal.propose([333, 388, 326, 326, 398, 400], MEDIAN, 5) == 360  # (333 + 388) / 2 = 360.5
    assert proposal.propose([1, 2, 3], AVERAGE, 5) == 0  # rounds to nothing
    assert proposal.propose([-50, 10], AVERAGE, 1) == 0  # refunds outweigh spending


def test_activity_needs_a_cent():
    assert not proposal.has_activity([0, 0.004, -0.004])
    assert proposal.has_activity([0, 0.01])
    assert proposal.has_activity([-5])
    assert not proposal.has_activity([])
