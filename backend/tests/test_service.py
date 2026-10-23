from decimal import Decimal as D

import pytest

from helperpay.models import Helper
from helperpay.service import Conflict, DomainError, Household
from helperpay.store import MemoryStore

from helperpay.devdata import LUPE, LUPE_BILL, PRIYA, PRIYA_BILL


def agreement_payload(version, new_helper=None):
    d = version.model_dump(mode="json", exclude={"version", "confirmed_at"})
    return {"new_helper": new_helper, "agreement": d}


@pytest.fixture
def hh():
    return Household(MemoryStore())


def add_lupe(hh, versions=LUPE):
    lupe = Helper(helper_id="lupe", name="Lupe", role="cleaner").model_dump()
    for i, v in enumerate(versions):
        p = hh.propose("agreement", agreement_payload(v, lupe if i == 0 else None), f"Lupe v{i+1}")
        hh.confirm_proposal(p["proposal_id"])


def test_proposal_writes_nothing_until_confirmed(hh):
    lupe = Helper(helper_id="lupe", name="Lupe", role="cleaner").model_dump()
    p = hh.propose("agreement", agreement_payload(LUPE[0], lupe), "Add Lupe")
    assert hh.helpers() == [] and hh.versions("lupe") == []
    hh.confirm_proposal(p["proposal_id"])
    assert [h.name for h in hh.helpers()] == ["Lupe"]
    assert [v.version for v in hh.versions("lupe")] == [1]
    with pytest.raises(Conflict):
        hh.confirm_proposal(p["proposal_id"])


def test_rejected_proposal_cannot_be_confirmed(hh):
    lupe = Helper(helper_id="lupe", name="Lupe", role="cleaner").model_dump()
    p = hh.propose("agreement", agreement_payload(LUPE[0], lupe), "Add Lupe")
    hh.reject_proposal(p["proposal_id"], "wrong rate")
    with pytest.raises(Conflict):
        hh.confirm_proposal(p["proposal_id"])
    assert hh.helpers() == []


def test_agreement_preview_shows_impact_on_open_bills(hh):
    add_lupe(hh, LUPE[:1])
    hh.record_bill(LUPE_BILL)
    assert hh.check(LUPE_BILL.bill_id).expected_total == D(780)  # regular visits all at $140
    p = hh.propose("agreement", agreement_payload(LUPE[1]), "Lupe raise from Sep 15")
    impact = p["preview"]["open_bill_impact"]
    assert impact == [{"bill_id": "lupe-2026-09", "before": "780.00", "after": "800.00", "overall_after": "discrepancies"}]


def test_malformed_terms_are_refused(hh):
    lupe = Helper(helper_id="lupe", name="Lupe", role="cleaner").model_dump()
    payload = agreement_payload(LUPE[0], lupe)
    payload["agreement"]["terms"] = [{"kind": "per_unit", "code": "x"}]  # no amount
    with pytest.raises(DomainError):
        hh.propose("agreement", payload, "bad")


def test_approve_locks_the_bill(hh):
    add_lupe(hh)
    hh.record_bill(LUPE_BILL)
    with pytest.raises(Conflict):  # Jordan saw a stale amount
        hh.approve_bill(LUPE_BILL.bill_id, {}, D(820))
    hh.approve_bill(LUPE_BILL.bill_id, {}, D(800), note="Paid $140 for Sep 1 and 8")
    b = hh.bill_record(LUPE_BILL.bill_id)
    assert b["status"] == "approved" and b["approval"]["amount"] == "800.00"
    assert b["approval"]["agreement_versions"] == [1, 2]
    with pytest.raises(Conflict):
        hh.approve_bill(LUPE_BILL.bill_id, {}, D(800))
    with pytest.raises(Conflict):
        hh.correct_bill_lines(LUPE_BILL.bill_id, LUPE_BILL.lines[:1])


def test_pay_as_billed_is_an_explicit_choice(hh):
    add_lupe(hh)
    hh.record_bill(LUPE_BILL)
    amount, _, missing = hh.payable(LUPE_BILL.bill_id, {1: "billed", 2: "billed"})
    assert amount == D(820) and not missing


def test_undecided_lines_block_approval(hh):
    priya = Helper(helper_id="priya", name="Priya", role="babysitter").model_dump()
    hh.confirm_proposal(hh.propose("agreement", agreement_payload(PRIYA[0], priya), "Add Priya")["proposal_id"])
    hh.record_bill(PRIYA_BILL)
    with pytest.raises(DomainError, match="last minute fee"):
        hh.approve_bill(PRIYA_BILL.bill_id, {}, D(556))
    with pytest.raises(DomainError):
        hh.propose("approval", {"bill_id": PRIYA_BILL.bill_id, "amount": "556"}, "pay")
    p = hh.propose("approval", {"bill_id": PRIYA_BILL.bill_id, "decisions": {5: "billed"}, "amount": "596"}, "pay incl. fee")
    assert hh.bill(PRIYA_BILL.bill_id).status == "open"  # proposing doesn't approve
    hh.confirm_proposal(p["proposal_id"])
    assert hh.bill_record(PRIYA_BILL.bill_id)["approval"]["amount"] == "596.00"


def test_second_bill_for_paid_period_is_flagged(hh):
    add_lupe(hh)
    hh.record_bill(LUPE_BILL)
    hh.approve_bill(LUPE_BILL.bill_id, {}, D(800))
    _, warnings = hh.record_bill(LUPE_BILL.model_copy(update={"bill_id": "lupe-again"}))
    assert any("already approved" in w for w in warnings)


def test_new_open_bill_supersedes_old_open_one(hh):
    add_lupe(hh)
    hh.record_bill(LUPE_BILL)
    _, warnings = hh.record_bill(LUPE_BILL.model_copy(update={"bill_id": "lupe-corrected"}))
    assert hh.bill(LUPE_BILL.bill_id).status == "superseded"
    assert any("Replaced" in w for w in warnings)


def test_audit_trail_records_who_did_what(hh):
    add_lupe(hh)
    hh.record_bill(LUPE_BILL)
    hh.approve_bill(LUPE_BILL.bill_id, {}, D(800))
    actions = [(a["actor"], a["action"]) for a in reversed(hh.audit_log())]
    assert actions == [
        ("assistant", "proposed_agreement"),
        ("jordan", "added_helper"),
        ("jordan", "confirmed_agreement"),
        ("assistant", "proposed_agreement"),
        ("jordan", "confirmed_agreement"),
        ("assistant", "recorded_bill"),
        ("jordan", "approved_bill"),
    ]


def test_pending_terms_change_blocks_approval(hh):
    add_lupe(hh, LUPE[:1])
    hh.record_bill(LUPE_BILL)
    p = hh.propose("agreement", agreement_payload(LUPE[1]), "Lupe raise")
    with pytest.raises(DomainError, match="waiting for your OK"):
        hh.approve_bill(LUPE_BILL.bill_id, {}, D(780))
    hh.confirm_proposal(p["proposal_id"])
    hh.approve_bill(LUPE_BILL.bill_id, {}, D(800))


def test_new_helper_history_can_be_proposed_and_confirmed_together(hh):
    # First upload: Lupe isn't recorded yet, but her v1 and v2 arrive together.
    lupe = Helper(helper_id="lupe", name="Lupe", role="cleaner").model_dump()
    props = [hh.propose("agreement", agreement_payload(v, lupe), f"Lupe v{i+1}") for i, v in enumerate(LUPE)]
    for p in reversed(props):  # order of confirmation doesn't matter
        hh.confirm_proposal(p["proposal_id"])
    assert len(hh.helpers()) == 1
    assert sorted(str(v.effective_from) for v in hh.versions("lupe")) == ["2026-01-20", "2026-09-15"]
    hh.record_bill(LUPE_BILL)
    assert hh.check(LUPE_BILL.bill_id).expected_total == D("800")


def test_new_helper_id_clash_with_a_different_person_is_refused(hh):
    add_lupe(hh, LUPE[:1])
    other = Helper(helper_id="lupe", name="Guadalupe M.", role="nanny").model_dump()
    # Proposed before Lupe existed would carry new_helper; simulate by writing the proposal directly.
    p = hh.propose("agreement", agreement_payload(LUPE[1]), "Lupe v2")
    hh.db.put(f"PROPOSAL#{p['proposal_id']}", {**p, "payload": {**p["payload"], "new_helper": other}})
    with pytest.raises(Conflict):
        hh.confirm_proposal(p["proposal_id"])
