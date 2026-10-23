"""Property tests for the bill checker: rules that must hold for any agreement and any bill.

Hypothesis generates thousands of random agreements, shifts and bills. Hourly
pricing is compared with a separate minute-by-minute reference implementation,
so a bug in the checker's interval cutting can't hide behind a test written
the same way.
"""

import random
from datetime import date, time, timedelta
from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from helperpay.checker import LineStatus, check_bill, money, price_hourly
from helperpay.models import (
    WEEKDAYS,
    AgreementVersion,
    Bill,
    BillLine,
    DayMultiplier,
    HourlyTerm,
    RateTier,
    UnitTerm,
)

DAY = 24 * 60
QUARTERS = 96  # quarter-hours in a day


def t(quarter: int) -> time:
    m = (quarter % QUARTERS) * 15
    return time(m // 60, m % 60)


# Rates that are multiples of $4 make every quarter-hour amount, even at ×1.5,
# a whole number of cents, so checker and reference must agree to the cent.
exact_rates = st.integers(min_value=1, max_value=50).map(lambda n: D(n * 4))
any_rates = st.decimals(min_value=D("5"), max_value=D("120"), places=2)
multipliers = st.sampled_from([D(1), D("1.5"), D(2)])
days_2026 = st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31))


@st.composite
def tiers(draw, rates):
    """0-2 non-overlapping time-of-day windows; one may wrap past midnight."""
    n = draw(st.integers(0, 2))
    points = sorted(draw(st.lists(st.integers(0, QUARTERS - 1), min_size=2 * n, max_size=2 * n, unique=True)))
    if n and draw(st.booleans()):
        points = points[1:] + points[:1]  # arcs (p1,p2), (p3,p0): the last one wraps
    return [RateTier(start=t(points[i]), end=t(points[i + 1]), rate=draw(rates)) for i in range(0, 2 * n, 2)]


@st.composite
def hourly_terms(draw, rates=exact_rates):
    mults = []
    for day in draw(st.lists(st.sampled_from(WEEKDAYS), max_size=2, unique=True)):
        mults.append(DayMultiplier(day=day, multiplier=draw(multipliers),
                                   applies_to=draw(st.sampled_from(["base", "all", None]))))
    return HourlyTerm(code="h", label="Hourly", base_rate=draw(rates), tiers=draw(tiers(rates)), day_multipliers=mults)


@st.composite
def shifts(draw):
    """(date, start, end, minutes): 15 min to 23h45, possibly past midnight."""
    on = draw(days_2026)
    start = draw(st.integers(0, QUARTERS - 1))
    length = draw(st.integers(1, QUARTERS - 1))
    return on, t(start), t(start + length), length * 15


def _in(m: int, tier: RateTier) -> bool:
    s = tier.start.hour * 60 + tier.start.minute
    e = tier.end.hour * 60 + tier.end.minute
    m %= DAY
    return s <= m < e if s < e else (m >= s or m < e)


def reference_price(term: HourlyTerm, on: date, start: time, end: time) -> tuple[D, bool]:
    """Minute by minute: the slow, obvious way."""
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    if e <= s:
        e += DAY
    mult = next((m for m in term.day_multipliers if m.day == WEEKDAYS[on.weekday()]), None)
    total, ambiguous = D(0), False
    for minute in range(s, e):
        tier = next((x for x in term.tiers if _in(minute, x)), None)
        if tier is None:
            rate = term.base_rate * (mult.multiplier if mult else 1)
        else:
            rate = tier.rate
            if mult and mult.applies_to != "base":
                rate *= mult.multiplier
                ambiguous |= mult.applies_to is None
        total += rate  # rate-minutes; divided once below to stay exact
    return total / 60, ambiguous


# ------------------------------------------------------------ hourly pricing


@settings(max_examples=2000)
@given(hourly_terms(), shifts())
def test_hourly_matches_minute_by_minute_reference(term, shift):
    on, start, end, _ = shift
    amount, steps, ambiguous = price_hourly(term, on, start, end)
    want, want_ambiguous = reference_price(term, on, start, end)
    assert amount == money(want)
    assert ambiguous == want_ambiguous
    assert steps  # every price comes with its arithmetic


@settings(max_examples=1000)
@given(hourly_terms(rates=any_rates), shifts())
def test_hourly_with_cent_rates_is_within_rounding_of_reference(term, shift):
    on, start, end, _ = shift
    amount, steps, _ = price_hourly(term, on, start, end)
    want, _ = reference_price(term, on, start, end)
    # The checker rounds each rate segment to the cent, once per explanation step.
    assert abs(amount - want) <= D("0.005") * len(steps)


@settings(max_examples=1000)
@given(hourly_terms(), shifts(), st.integers(1, QUARTERS - 2))
def test_splitting_a_shift_does_not_change_the_price(term, shift, cut):
    term = term.model_copy(update={"day_multipliers": []})  # multipliers follow the start day
    on, start, end, length = shift
    cut = cut % (length // 15) if length > 15 else 0
    if cut == 0:
        return
    s = (start.hour * 60 + start.minute) // 15
    mid = t(s + cut)
    whole = price_hourly(term, on, start, end)[0]
    parts = price_hourly(term, on, start, mid)[0] + price_hourly(term, on + timedelta(days=(s + cut) // QUARTERS), mid, end)[0]
    assert whole == parts


@settings(max_examples=500)
@given(hourly_terms(), shifts())
def test_flat_rate_is_rate_times_hours(term, shift):
    term = term.model_copy(update={"tiers": [], "day_multipliers": []})
    on, start, end, minutes = shift
    assert price_hourly(term, on, start, end)[0] == money(term.base_rate * minutes / 60)


# ------------------------------------------------------------- whole bills

PERIOD = (date(2026, 9, 1), date(2026, 9, 30))


@st.composite
def unit_agreements(draw):
    """1-3 versions of a per-visit agreement, effective on distinct dates."""
    n = draw(st.integers(1, 3))
    starts = sorted(draw(st.lists(st.dates(date(2026, 8, 1), date(2026, 9, 30)), min_size=n, max_size=n, unique=True)))
    return [
        AgreementVersion(
            helper_id="h", version=i + 1, effective_from=d,
            terms=[UnitTerm(code="visit", label="Visit", amount=draw(any_rates)),
                   UnitTerm(code="deep", label="Deep clean", amount=draw(any_rates))],
        )
        for i, d in enumerate(starts)
    ]


def in_effect(versions, on):
    """The version a line on this date is priced with: latest effective_from on or before it."""
    started = [v for v in versions if v.effective_from <= on]
    return max(started, key=lambda v: v.effective_from) if started else None


@st.composite
def honest_bills(draw, versions):
    """Lines billed exactly at the rate in effect that day, plus some uncovered extras."""
    lines = []
    for i in range(draw(st.integers(1, 8))):
        on = draw(st.dates(*PERIOD) | st.sampled_from([v.effective_from for v in versions if v.effective_from >= PERIOD[0]] or [PERIOD[0]]))
        code = draw(st.sampled_from(["visit", "deep", "supplies"]))
        v = in_effect(versions, on)
        amount = v.term(code).amount if v and v.term(code) else draw(any_rates)
        lines.append(BillLine(line_no=i + 1, raw=f"{on} {code}", date=on, amount=amount, term_code=code))
    total = sum((line.amount for line in lines), D(0))
    return Bill(bill_id="b", helper_id="h", period_start=PERIOD[0], period_end=PERIOD[1], lines=lines, stated_total=total)


@settings(max_examples=1000)
@given(st.data())
def test_bill_totals_add_up_and_honest_lines_match(data):
    versions = data.draw(unit_agreements())
    bill = data.draw(honest_bills(versions))
    c = check_bill(bill, versions)

    assert c.lines_sum == money(sum(line.amount for line in bill.lines))
    assert c.arithmetic_ok
    covered = [lc for lc in c.lines if lc.status == LineStatus.MATCH]
    uncovered = [lc for lc in c.lines if lc.status != LineStatus.MATCH]
    # Every dollar billed is either supported by the agreement or waiting for Jordan.
    assert c.expected_total + c.undecided_total == c.lines_sum
    assert c.expected_total == sum((lc.expected for lc in covered), D(0))
    for line, lc in zip(bill.lines, c.lines):
        v = in_effect(versions, line.date)
        if v is None:
            assert lc.status == LineStatus.CANNOT_CHECK  # before any agreement
        elif line.term_code == "supplies":
            assert lc.status == LineStatus.NEEDS_DECISION  # never auto-accepted
        else:
            assert lc.status == LineStatus.MATCH and lc.agreement_version == v.version
    assert (c.overall == "matches") == (not uncovered)


@settings(max_examples=500)
@given(st.data(), st.decimals(min_value=D("0.01"), max_value=D("100"), places=2))
def test_an_overcharge_is_caught_to_the_cent(data, extra):
    versions = data.draw(unit_agreements())
    bill = data.draw(honest_bills(versions))
    before = check_bill(bill, versions)
    i = data.draw(st.integers(0, len(bill.lines) - 1))
    if before.lines[i].status != LineStatus.MATCH:
        return
    lines = list(bill.lines)
    lines[i] = lines[i].model_copy(update={"amount": lines[i].amount + extra})
    after = check_bill(bill.model_copy(update={"lines": lines, "stated_total": bill.stated_total + extra}), versions)
    assert after.lines[i].status == LineStatus.OVERCHARGE
    assert after.lines[i].delta == extra
    assert after.expected_total == before.expected_total  # the agreement, not the bill, sets the amount
    assert after.overall != "matches"


@settings(max_examples=500)
@given(st.data(), st.decimals(min_value=D("0.01"), max_value=D("50"), places=2))
def test_a_wrong_stated_total_is_flagged(data, off):
    versions = data.draw(unit_agreements())
    bill = data.draw(honest_bills(versions))
    c = check_bill(bill.model_copy(update={"stated_total": bill.stated_total + off}), versions)
    assert not c.arithmetic_ok
    assert c.overall != "matches"
    assert c.expected_total == check_bill(bill, versions).expected_total  # pay by the lines


@settings(max_examples=300)
@given(st.data(), st.randoms())
def test_result_does_not_depend_on_record_order(data, rnd: random.Random):
    versions = data.draw(unit_agreements())
    bill = data.draw(honest_bills(versions))
    shuffled = versions[:]
    rnd.shuffle(shuffled)
    assert check_bill(bill, versions) == check_bill(bill, shuffled)
