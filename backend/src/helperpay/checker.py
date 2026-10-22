"""Deterministic bill checker.

Prices every bill line against the agreement version in effect on the line's
date and explains each step. No LLM is involved: every dollar figure Jordan
sees comes from here.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel

from .holidays import us_federal_holidays
from .models import (
    WEEKDAYS,
    AgreementVersion,
    Bill,
    BillLine,
    HourlyTerm,
    PackageTerm,
    UnitTerm,
)

CENT = Decimal("0.01")
DAY = 24 * 60


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(x: Decimal) -> str:
    x = money(x)
    return f"${x:,.0f}" if x == x.to_integral_value() else f"${x:,.2f}"


def fmt_hours(minutes: int) -> str:
    h = Decimal(minutes) / 60
    return f"{h.normalize():f}h"


def fmt_time(t: time) -> str:
    h = t.hour % 12 or 12
    suffix = "am" if t.hour < 12 else "pm"
    return f"{h}{suffix}" if t.minute == 0 else f"{h}:{t.minute:02d}{suffix}"


class LineStatus(StrEnum):
    MATCH = "match"
    OVERCHARGE = "overcharge"
    UNDERCHARGE = "undercharge"
    NEEDS_DECISION = "needs_decision"  # nothing in the agreement covers it
    CANNOT_CHECK = "cannot_check"  # missing data (no times, no agreement...)
    AMBIGUOUS = "ambiguous"  # agreement allows more than one reading


class Flag(BaseModel):
    level: str  # "info" | "warn" | "error"
    message: str


class LineCheck(BaseModel):
    line_no: int
    raw: str
    billed: Decimal
    expected: Decimal | None
    delta: Decimal | None  # billed - expected
    status: LineStatus
    agreement_version: int | None
    steps: list[str]
    flags: list[Flag] = []


class BillCheck(BaseModel):
    bill_id: str
    lines: list[LineCheck]
    stated_total: Decimal | None
    lines_sum: Decimal
    arithmetic_ok: bool
    expected_total: Decimal  # sum of lines the agreement covers
    undecided_total: Decimal  # lines Jordan must accept or reject
    overall: str  # "matches" | "discrepancies" | "needs_review"
    flags: list[Flag]

    @property
    def headline(self) -> str:
        if self.overall == "matches":
            return f"Matches the agreement: {fmt(self.expected_total)}"
        parts = [f"Agreement says {fmt(self.expected_total)}"]
        if self.stated_total is not None:
            parts.append(f"billed {fmt(self.stated_total)}")
        if self.undecided_total:
            parts.append(f"{fmt(self.undecided_total)} needs your decision")
        return ", ".join(parts)


# ------------------------------------------------------------------ versions


def version_for(versions: list[AgreementVersion], on: date) -> AgreementVersion | None:
    best = None
    for v in sorted(versions, key=lambda v: (v.effective_from, v.version)):
        if v.effective_from <= on:
            best = v
    return best


def versions_in_period(
    versions: list[AgreementVersion], start: date, end: date
) -> list[AgreementVersion]:
    """All versions that are in effect at some point in [start, end]."""
    out = []
    first = version_for(versions, start)
    if first:
        out.append(first)
    out += [v for v in versions if start < v.effective_from <= end]
    return out


# ------------------------------------------------------------- hourly pricing


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def _in_window(m: int, start: time, end: time) -> bool:
    m %= DAY
    s, e = _mins(start), _mins(end)
    return s <= m < e if s < e else (m >= s or m < e)


def price_hourly(
    term: HourlyTerm, on: date, start: time, end: time
) -> tuple[Decimal, list[str], bool]:
    """Returns (amount, explanation steps, ambiguous).

    A shift that ends at or before its start time runs past midnight. Day
    multipliers use the day the shift starts.
    """
    s = _mins(start)
    e = _mins(end)
    if e <= s:
        e += DAY
    cuts = {s, e}
    for tier in term.tiers:
        for b in (_mins(tier.start), _mins(tier.end)):
            for off in (0, DAY):
                if s < b + off < e:
                    cuts.add(b + off)
    cuts = sorted(cuts)

    weekday = WEEKDAYS[on.weekday()]
    mult = next((m for m in term.day_multipliers if m.day == weekday), None)
    ambiguous = False

    # (rate label, rate) -> minutes, keeping first-seen order
    buckets: dict[tuple[str, Decimal], int] = {}
    for a, b in zip(cuts, cuts[1:]):
        tier = next((t for t in term.tiers if _in_window(a, t.start, t.end)), None)
        if tier is None:
            rate, label = term.base_rate, f"{fmt(term.base_rate)}/hr"
            if mult:
                rate = term.base_rate * mult.multiplier
                label = f"{fmt(term.base_rate)}/hr × {mult.multiplier.normalize():f} ({weekday.title()})"
        else:
            rate = tier.rate
            label = f"{fmt(tier.rate)}/hr after {fmt_time(tier.start)}"
            if mult and mult.applies_to != "base":
                if mult.applies_to is None:
                    ambiguous = True
                rate = tier.rate * mult.multiplier
                label += f" × {mult.multiplier.normalize():f} ({weekday.title()})"
        key = (label, rate)
        buckets[key] = buckets.get(key, 0) + (b - a)

    total = Decimal(0)
    steps = []
    for (label, rate), minutes in buckets.items():
        amt = money(rate * minutes / 60)
        total += amt
        steps.append(f"{fmt_hours(minutes)} at {label} = {fmt(amt)}")
    if ambiguous:
        steps.append(
            f"The agreement doesn't say whether the {weekday.title()} multiplier "
            "also applies to the late rate; multiplied both here."
        )
    return money(total), steps, ambiguous


# -------------------------------------------------------------- line pricing


def _status(billed: Decimal, expected: Decimal) -> LineStatus:
    if billed == expected:
        return LineStatus.MATCH
    return LineStatus.OVERCHARGE if billed > expected else LineStatus.UNDERCHARGE


def check_line(
    line: BillLine, bill: Bill, versions: list[AgreementVersion]
) -> LineCheck:
    billed = money(line.amount)
    flags: list[Flag] = []

    def result(status, expected=None, steps=(), version=None):
        exp = money(expected) if expected is not None else None
        return LineCheck(
            line_no=line.line_no,
            raw=line.raw,
            billed=billed,
            expected=exp,
            delta=(billed - exp) if exp is not None else None,
            status=status,
            agreement_version=version.version if version else None,
            steps=list(steps),
            flags=flags,
        )

    # Which agreement applies?
    if line.date is not None:
        version = version_for(versions, line.date)
        if version is None:
            return result(
                LineStatus.CANNOT_CHECK,
                steps=[f"No agreement was in effect on {line.date:%b %-d}."],
            )
        if not (bill.period_start <= line.date <= bill.period_end):
            flags.append(Flag(level="warn", message="Date is outside the billing period."))
        actual_day = WEEKDAYS[line.date.weekday()]
        if line.stated_weekday and line.stated_weekday != actual_day:
            flags.append(
                Flag(
                    level="warn",
                    message=f"Bill says {line.stated_weekday.title()}, but {line.date:%b %-d, %Y} "
                    f"is a {line.date:%A}. Check the date.",
                )
            )
    else:
        in_period = versions_in_period(versions, bill.period_start, bill.period_end)
        if not in_period:
            return result(
                LineStatus.CANNOT_CHECK,
                steps=["No agreement was in effect during this billing period."],
            )
        if len(in_period) > 1:
            return result(
                LineStatus.CANNOT_CHECK,
                steps=[
                    "The rates changed during this period and this line has no date, "
                    "so it can't be priced. Ask for dates."
                ],
            )
        version = in_period[0]

    since = f"terms from {version.effective_from:%b %-d}"

    if line.claimed_justification:
        flags.append(
            Flag(
                level="warn",
                message=f"Helper says: “{line.claimed_justification}”. Not in the written agreement.",
            )
        )

    term = version.term(line.term_code) if line.term_code else None
    if term is None:
        return result(
            LineStatus.NEEDS_DECISION,
            steps=[f"Nothing in the agreement ({since}) covers this charge."],
            version=version,
        )

    # One-off arrangements for this date
    if line.date:
        for note in version.date_notes:
            if note.on == line.date:
                steps_note = f"Agreed for {note.on:%b %-d}: {note.note}"
                flags.append(Flag(level="info", message=steps_note))

    if isinstance(term, UnitTerm):
        qty = line.quantity
        expected = term.amount * qty
        steps = [
            f"{term.label}: {qty.normalize():f} × {fmt(term.amount)} = {fmt(expected)} ({since})"
        ]
        if line.unit_price is not None:
            if line.unit_price != term.amount:
                steps.append(
                    f"Bill uses {fmt(line.unit_price)} per {term.unit}; agreement says {fmt(term.amount)}."
                )
            if money(line.unit_price * qty) != billed:
                flags.append(
                    Flag(
                        level="error",
                        message=f"Line math is off: {qty.normalize():f} × {fmt(line.unit_price)} is "
                        f"{fmt(line.unit_price * qty)}, not {fmt(billed)}.",
                    )
                )
        return result(_status(billed, money(expected)), expected, steps, version)

    if isinstance(term, PackageTerm):
        steps = [f"{term.label}: flat {fmt(term.amount)}"]
        expected = term.amount
        ambiguous = False
        if term.covers_until:
            steps[0] += f" until {fmt_time(term.covers_until)}"
            if line.end is None:
                steps.append("No end time given, so any overtime can't be checked.")
            elif _ends_after(line.start, line.end, term.covers_until):
                over = version.term(term.overflow_term) if term.overflow_term else None
                if not isinstance(over, HourlyTerm):
                    return result(
                        LineStatus.CANNOT_CHECK,
                        steps=steps + ["Ran past the flat window but no overtime rate is agreed."],
                        version=version,
                    )
                amt, sub, ambiguous = price_hourly(over, line.date, term.covers_until, line.end)
                expected += amt
                steps += [f"After {fmt_time(term.covers_until)}: {s}" for s in sub]
        steps.append(f"Total {fmt(expected)} ({since})")
        status = LineStatus.AMBIGUOUS if ambiguous and billed != money(expected) else _status(billed, money(expected))
        return result(status, expected, steps, version)

    # Hourly
    if line.start is None or line.end is None or line.date is None:
        return result(
            LineStatus.CANNOT_CHECK,
            steps=["Hourly work needs a date with start and end times to check."],
            version=version,
        )
    expected, steps, ambiguous = price_hourly(term, line.date, line.start, line.end)
    steps.append(f"Total {fmt(expected)} ({since})")
    status = _status(billed, expected)
    if ambiguous and status != LineStatus.MATCH:
        status = LineStatus.AMBIGUOUS
    return result(status, expected, steps, version)


def _ends_after(start: time | None, end: time, limit: time) -> bool:
    e = _mins(end)
    if start is not None and e <= _mins(start):
        e += DAY  # crosses midnight
    lim = _mins(limit)
    if start is not None and lim <= _mins(start):
        lim += DAY
    return e > lim


# --------------------------------------------------------------- bill checks


def _schedule_flags(bill: Bill, versions: list[AgreementVersion]) -> list[Flag]:
    flags = []
    version = version_for(versions, bill.period_end)
    if version is None or not version.schedule.days:
        return flags
    sched = version.schedule
    day_names = ", ".join(d.title() for d in sched.days)

    for line in bill.lines:
        if line.date and line.term_code and WEEKDAYS[line.date.weekday()] not in sched.days:
            flags.append(
                Flag(
                    level="info",
                    message=f"Line {line.line_no}: {line.date:%a %b %-d} isn't a usual day ({day_names}).",
                )
            )

    undated = [l for l in bill.lines if l.date is None and l.term_code]
    if undated:
        holidays = us_federal_holidays(bill.period_start.year) if sched.skips_us_holidays else {}
        possible = 0
        d = bill.period_start
        while d <= bill.period_end:
            if WEEKDAYS[d.weekday()] in sched.days and d not in holidays:
                possible += 1
            d += timedelta(days=1)
        billed_units = sum(l.quantity for l in undated)
        level = "warn" if billed_units > possible else "info"
        skipped = [f"{n} ({h:%b %-d})" for h, n in holidays.items() if bill.period_start <= h <= bill.period_end]
        msg = f"{billed_units.normalize():f} billed vs {possible} possible {day_names} in the period"
        if skipped:
            msg += f", excluding {', '.join(skipped)}"
        flags.append(Flag(level=level, message=msg + "."))

    dated = Counter((l.date, l.term_code) for l in bill.lines if l.date and l.term_code)
    for (d, code), n in dated.items():
        if n > 1:
            flags.append(Flag(level="warn", message=f"{d:%b %-d} is billed {n} times for the same service."))
    return flags


def check_bill(bill: Bill, versions: list[AgreementVersion]) -> BillCheck:
    lines = [check_line(l, bill, versions) for l in bill.lines]
    lines_sum = money(sum((l.billed for l in lines), Decimal(0)))
    flags: list[Flag] = []

    arithmetic_ok = bill.stated_total is None or money(bill.stated_total) == lines_sum
    if not arithmetic_ok:
        diff = money(bill.stated_total) - lines_sum
        flags.append(
            Flag(
                level="error",
                message=f"The lines add up to {fmt(lines_sum)}, but the bill's total says "
                f"{fmt(bill.stated_total)} ({'+' if diff > 0 else '−'}{fmt(abs(diff))}).",
            )
        )
    flags += _schedule_flags(bill, versions)

    covered = {LineStatus.MATCH, LineStatus.OVERCHARGE, LineStatus.UNDERCHARGE}
    expected_total = money(sum((l.expected for l in lines if l.status in covered), Decimal(0)))
    undecided_total = money(
        sum((l.billed for l in lines if l.status not in covered), Decimal(0))
    )

    if any(l.status not in covered for l in lines):
        overall = "needs_review"
    elif not arithmetic_ok or any(l.status != LineStatus.MATCH for l in lines):
        overall = "discrepancies"
    elif any(f.level == "error" for l in lines for f in l.flags):
        overall = "discrepancies"
    else:
        overall = "matches"

    return BillCheck(
        bill_id=bill.bill_id,
        lines=lines,
        stated_total=money(bill.stated_total) if bill.stated_total is not None else None,
        lines_sum=lines_sum,
        arithmetic_ok=arithmetic_ok,
        expected_total=expected_total,
        undecided_total=undecided_total,
        overall=overall,
        flags=flags,
    )
