from datetime import date, time
from decimal import Decimal as D

import pytest

from helperpay.checker import LineStatus, check_bill, price_hourly
from helperpay.models import Bill, BillLine, DayMultiplier, HourlyTerm, RateTier

from helperpay.devdata import LUPE, LUPE_BILL, PRIYA, PRIYA_BILL, THEO, THEO_BILL


def statuses(check):
    return [l.status for l in check.lines]


# ------------------------------------------------------------ starter pack


def test_priya_september():
    c = check_bill(PRIYA_BILL, PRIYA)
    assert [l.expected for l in c.lines] == [D(114), D(88), D(122), D(132), None, D(100)]
    assert statuses(c) == [LineStatus.MATCH] * 4 + [LineStatus.NEEDS_DECISION, LineStatus.MATCH]
    assert c.lines_sum == D(596)
    assert not c.arithmetic_ok
    assert c.expected_total == D(556)
    assert c.undecided_total == D(40)
    assert c.overall == "needs_review"
    assert any("Sam said" in f.message for f in c.lines[4].flags)
    assert any("$596" in f.message and "$626" in f.message for f in c.flags)


def test_lupe_september():
    c = check_bill(LUPE_BILL, LUPE)
    assert [l.expected for l in c.lines] == [D(140), D(140), D(150), D(220), D(150)]
    assert [l.agreement_version for l in c.lines] == [1, 1, 2, 2, 2]
    assert statuses(c)[:2] == [LineStatus.OVERCHARGE] * 2
    assert c.arithmetic_ok
    assert c.expected_total == D(800)
    assert c.overall == "discrepancies"
    assert any("Deep clean instead" in f.message for f in c.lines[3].flags)


def test_theo_september_uses_newer_rates():
    c = check_bill(THEO_BILL, THEO)
    assert statuses(c) == [LineStatus.MATCH, LineStatus.MATCH]
    assert c.expected_total == D(580)
    assert c.overall == "matches"
    # 21 weekdays in Sept 2026 minus Labor Day
    assert any("20 billed vs 21 possible" in f.message and "Labor Day" in f.message for f in c.flags)


# ----------------------------------------------------------- hourly engine

SITTER = HourlyTerm(
    code="hourly",
    label="Babysitting",
    base_rate=D(22),
    tiers=[RateTier(start=time(22), end=time(6), rate=D(28))],
    day_multipliers=[DayMultiplier(day="sun", multiplier=D("1.5"))],
)


@pytest.mark.parametrize(
    "on,start,end,expected",
    [
        (date(2026, 9, 9), time(15), time(19), D(88)),
        (date(2026, 9, 12), time(19), time(0), D(122)),  # ends at midnight
        (date(2026, 9, 12), time(21), time(1), D(22 + 3 * 28)),  # crosses midnight
        (date(2026, 9, 12), time(23), time(7), D(7 * 28 + 22)),  # past the late window
        (date(2026, 9, 9), time(15, 15), time(16), D("16.50")),  # partial hour
    ],
)
def test_price_hourly(on, start, end, expected):
    amount, _, ambiguous = price_hourly(SITTER, on, start, end)
    assert amount == expected
    assert not ambiguous


def test_tier_written_as_ending_2359_runs_to_midnight():
    term = SITTER.model_copy(update={"tiers": [RateTier(start=time(22), end=time(23, 59), rate=D(28))]})
    assert price_hourly(term, date(2026, 9, 12), time(19), time(0))[0] == D(122)


def test_sunday_late_is_flagged_ambiguous():
    amount, steps, ambiguous = price_hourly(SITTER, date(2026, 9, 20), time(20), time(23))
    assert ambiguous
    assert amount == D(2 * 33 + 42)
    sure = SITTER.model_copy(
        update={"day_multipliers": [DayMultiplier(day="sun", multiplier=D("1.5"), applies_to="base")]}
    )
    amount, _, ambiguous = price_hourly(sure, date(2026, 9, 20), time(20), time(23))
    assert not ambiguous and amount == D(2 * 33 + 28)


# --------------------------------------------------------------- edge cases


def _bill(lines, helper="lupe", total=None):
    return Bill(
        bill_id="x",
        helper_id=helper,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        lines=lines,
        stated_total=total,
    )


def test_no_agreement_yet():
    c = check_bill(_bill([BillLine(line_no=1, raw="Jan 2", date=date(2026, 1, 2), amount=D(140), term_code="regular")]), LUPE)
    assert statuses(c) == [LineStatus.CANNOT_CHECK]
    assert c.overall == "needs_review"


def test_undated_line_across_rate_change_cannot_be_priced():
    c = check_bill(_bill([BillLine(line_no=1, raw="5 cleans", quantity=D(5), amount=D(750), term_code="regular")]), LUPE)
    assert statuses(c) == [LineStatus.CANNOT_CHECK]


def test_unknown_term_code_needs_decision():
    c = check_bill(_bill([BillLine(line_no=1, raw="windows", date=date(2026, 9, 15), amount=D(60), term_code="windows")]), LUPE)
    assert statuses(c) == [LineStatus.NEEDS_DECISION]


def test_off_schedule_and_duplicate_days_flagged():
    lines = [
        BillLine(line_no=1, raw="Sep 17", date=date(2026, 9, 17), amount=D(150), term_code="regular"),
        BillLine(line_no=2, raw="Sep 15", date=date(2026, 9, 15), amount=D(150), term_code="regular"),
        BillLine(line_no=3, raw="Sep 15", date=date(2026, 9, 15), amount=D(150), term_code="regular"),
    ]
    c = check_bill(_bill(lines), LUPE)
    msgs = " ".join(f.message for f in c.flags)
    assert "Thu Sep 17 isn't a usual day" in msgs
    assert "billed 2 times" in msgs


def test_line_math_error_on_unit_line():
    c = check_bill(
        _bill([BillLine(line_no=1, raw="18 x $28 = $514", quantity=D(18), unit_price=D(28), amount=D(514), term_code="walk_30")], helper="theo"),
        THEO,
    )
    assert c.lines[0].status == LineStatus.OVERCHARGE
    assert any(f.level == "error" for f in c.lines[0].flags)


def test_too_many_walks_warns():
    c = check_bill(
        _bill([BillLine(line_no=1, raw="25 walks", quantity=D(25), unit_price=D(28), amount=D(700), term_code="walk_30")], helper="theo"),
        THEO,
    )
    assert any(f.level == "warn" and "25 billed vs 21 possible" in f.message for f in c.flags)
