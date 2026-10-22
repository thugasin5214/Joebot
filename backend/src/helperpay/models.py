"""Domain records: helpers, versioned agreements, bills.

These are the system of record. The LLM proposes them; only a confirmed
proposal writes them. All money is Decimal.
"""

from __future__ import annotations

import datetime as dt
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Money = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ---------------------------------------------------------------- agreements


class RateTier(BaseModel):
    """A time-of-day window with its own hourly rate, e.g. 22:00-06:00 at $28."""

    start: time
    end: time  # may wrap past midnight (end <= start)
    rate: Money


class DayMultiplier(BaseModel):
    """E.g. Sundays are time and a half."""

    day: Weekday
    multiplier: Decimal
    # "base": multiplies only the base rate; "all": multiplies tier rates too.
    # None means the agreement never said; the checker flags overlaps.
    applies_to: Literal["base", "all"] | None = None


class HourlyTerm(BaseModel):
    kind: Literal["hourly"] = "hourly"
    code: str
    label: str
    base_rate: Money
    tiers: list[RateTier] = []
    day_multipliers: list[DayMultiplier] = []


class PackageTerm(BaseModel):
    """A flat price that covers a window; time past `covers_until` is billed hourly."""

    kind: Literal["package"] = "package"
    code: str
    label: str
    amount: Money
    covers_until: time | None = None
    overflow_term: str | None = None  # code of the HourlyTerm used for overflow


class UnitTerm(BaseModel):
    """A fixed price per visit / walk / item."""

    kind: Literal["per_unit"] = "per_unit"
    code: str
    label: str
    amount: Money
    unit: str = "visit"
    duration_minutes: int | None = None


Term = Annotated[HourlyTerm | PackageTerm | UnitTerm, Field(discriminator="kind")]


class Schedule(BaseModel):
    """The usual pattern. Used only for soft, informational checks."""

    days: list[Weekday] = []
    description: str = ""
    skips_us_holidays: bool = False


class DateNote(BaseModel):
    """A one-off arrangement, e.g. 'deep clean on Sep 22 instead of regular'."""

    on: date
    note: str
    replaces_code: str | None = None
    with_code: str | None = None


class SourceRef(BaseModel):
    source_id: str
    quote: str = ""  # the exact words this was read from


class AgreementVersion(BaseModel):
    """A complete snapshot of a helper's terms from `effective_from` onward."""

    helper_id: str
    version: int
    effective_from: date
    agreed_on: date | None = None
    terms: list[Term]
    schedule: Schedule = Schedule()
    date_notes: list[DateNote] = []
    notes: list[str] = []  # conditions we do not price, kept for Jordan
    sources: list[SourceRef] = []
    confirmed_at: datetime | None = None

    def term(self, code: str) -> Term | None:
        return next((t for t in self.terms if t.code == code), None)


class Helper(BaseModel):
    helper_id: str
    name: str
    role: str
    payment_method: str = ""
    notes: str = ""


# ---------------------------------------------------------------------- bills


class BillLine(BaseModel):
    """One line as the helper wrote it, normalised by extraction."""

    line_no: int
    raw: str
    date: dt.date | None = None
    start: time | None = None
    end: time | None = None
    quantity: Decimal = Decimal(1)
    unit_price: Money | None = None
    amount: Money
    term_code: str | None = None  # extraction's guess; None = no matching term
    claimed_justification: str = ""  # e.g. "Sam said that was fine"


class BillStatus(StrEnum):
    DRAFT = "draft"
    CHECKED = "checked"
    APPROVED = "approved"
    REJECTED = "rejected"


class Bill(BaseModel):
    bill_id: str
    helper_id: str
    period_start: date
    period_end: date
    received_on: date | None = None
    lines: list[BillLine]
    stated_total: Money | None = None
    source_id: str | None = None
    status: BillStatus = BillStatus.DRAFT
