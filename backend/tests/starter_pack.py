"""The Rivera starter pack, as confirmed records and extracted bills.

This is what extraction should produce from the starter-pack files once
Jordan confirms it. The checker tests run against it.
"""

from datetime import date, time
from decimal import Decimal as D

from helperpay.models import (
    AgreementVersion,
    Bill,
    BillLine,
    DateNote,
    DayMultiplier,
    HourlyTerm,
    PackageTerm,
    RateTier,
    Schedule,
    UnitTerm,
)

PRIYA = [
    AgreementVersion(
        helper_id="priya",
        version=1,
        effective_from=date(2026, 6, 14),
        agreed_on=date(2026, 6, 14),
        terms=[
            HourlyTerm(
                code="hourly",
                label="Babysitting",
                base_rate=D(22),
                tiers=[RateTier(start=time(22), end=time(6), rate=D(28))],
                day_multipliers=[DayMultiplier(day="sun", multiplier=D("1.5"))],
            ),
            PackageTerm(
                code="date_night",
                label="Date night",
                amount=D(100),
                covers_until=time(23),
                overflow_term="hourly",
            ),
        ],
        schedule=Schedule(days=["wed", "fri", "sat", "sun"], description="Wednesdays after school, some weekend nights"),
    )
]

LUPE = [
    AgreementVersion(
        helper_id="lupe",
        version=1,
        effective_from=date(2026, 1, 20),
        agreed_on=date(2026, 1, 14),
        terms=[
            UnitTerm(code="regular", label="Regular clean", amount=D(140)),
            UnitTerm(code="deep", label="Deep clean", amount=D(220)),
        ],
        schedule=Schedule(days=["tue"], description="Weekly, Tuesday mornings"),
    ),
    AgreementVersion(
        helper_id="lupe",
        version=2,
        effective_from=date(2026, 9, 15),
        agreed_on=date(2026, 8, 28),
        terms=[
            UnitTerm(code="regular", label="Regular clean", amount=D(150)),
            UnitTerm(code="deep", label="Deep clean", amount=D(220)),
        ],
        schedule=Schedule(days=["tue"], description="Weekly, Tuesday mornings"),
        date_notes=[
            DateNote(on=date(2026, 9, 22), note="Deep clean instead of the regular visit", replaces_code="regular", with_code="deep")
        ],
    ),
]

THEO = [
    AgreementVersion(
        helper_id="theo",
        version=1,
        effective_from=date(2026, 1, 1),
        terms=[UnitTerm(code="walk_30", label="Walk", amount=D(25), unit="walk")],
        schedule=Schedule(days=["mon", "tue", "wed", "thu", "fri"], skips_us_holidays=True),
        notes=["From Jordan's notes; date unknown"],
    ),
    AgreementVersion(
        helper_id="theo",
        version=2,
        effective_from=date(2026, 9, 1),
        agreed_on=date(2026, 8, 27),
        terms=[
            UnitTerm(code="walk_30", label="30-min walk", amount=D(28), unit="walk", duration_minutes=30),
            UnitTerm(code="walk_60", label="60-min walk", amount=D(38), unit="walk", duration_minutes=60),
        ],
        schedule=Schedule(days=["mon", "tue", "wed", "thu", "fri"], skips_us_holidays=True),
    ),
]

SEPT = dict(period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), received_on=date(2026, 10, 1))

PRIYA_BILL = Bill(
    bill_id="priya-2026-09",
    helper_id="priya",
    stated_total=D(626),
    lines=[
        BillLine(line_no=1, raw="Fri 9/4 date night 6–11:30pm — $114", date=date(2026, 9, 4), start=time(18), end=time(23, 30), amount=D(114), term_code="date_night"),
        BillLine(line_no=2, raw="Wed 9/9 after school 3–7pm — $88", date=date(2026, 9, 9), start=time(15), end=time(19), amount=D(88), term_code="hourly"),
        BillLine(line_no=3, raw="Sat 9/12 7pm–12am — $122", date=date(2026, 9, 12), start=time(19), end=time(0), amount=D(122), term_code="hourly"),
        BillLine(line_no=4, raw="Sun 9/20 10am–2pm — $132", date=date(2026, 9, 20), start=time(10), end=time(14), amount=D(132), term_code="hourly"),
        BillLine(line_no=5, raw="+ last minute fee $40", date=date(2026, 9, 20), amount=D(40), term_code=None, claimed_justification="Sam said that was fine since it was same-day!"),
        BillLine(line_no=6, raw="Fri 9/25 date night 6–11pm — $100", date=date(2026, 9, 25), start=time(18), end=time(23), amount=D(100), term_code="date_night"),
    ],
    **SEPT,
)

LUPE_BILL = Bill(
    bill_id="lupe-2026-09",
    helper_id="lupe",
    stated_total=D(820),
    lines=[
        BillLine(line_no=1, raw="Sep 1 – $150", date=date(2026, 9, 1), amount=D(150), term_code="regular"),
        BillLine(line_no=2, raw="Sep 8 – $150", date=date(2026, 9, 8), amount=D(150), term_code="regular"),
        BillLine(line_no=3, raw="Sep 15 – $150", date=date(2026, 9, 15), amount=D(150), term_code="regular"),
        BillLine(line_no=4, raw="Sep 22 deep – $220", date=date(2026, 9, 22), amount=D(220), term_code="deep"),
        BillLine(line_no=5, raw="Sep 29 – $150", date=date(2026, 9, 29), amount=D(150), term_code="regular"),
    ],
    **SEPT,
)

THEO_BILL = Bill(
    bill_id="theo-2026-09",
    helper_id="theo",
    stated_total=D(580),
    lines=[
        BillLine(line_no=1, raw="30-min walks: 18 x $28 = $504", quantity=D(18), unit_price=D(28), amount=D(504), term_code="walk_30"),
        BillLine(line_no=2, raw="60-min walks:  2 x $38 =  $76", quantity=D(2), unit_price=D(38), amount=D(76), term_code="walk_60"),
    ],
    **SEPT,
)
