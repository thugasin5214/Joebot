"""Household operations: the only code that writes the system of record.

Two paths write money or agreements, and neither is reachable from the agent:
  * confirm_proposal(): Jordan clicks Confirm on something the assistant proposed.
  * approve_bill():     Jordan clicks Approve on a bill.
The agent can read, record bills as open drafts, and create proposals.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from .checker import LineStatus, check_bill, fmt, money
from .models import AgreementVersion, Bill, BillLine, BillStatus, Helper
from .store import ConditionFailed, Store


class DomainError(Exception):
    """A request that breaks a household rule. The message is shown to Jordan."""


class Conflict(DomainError):
    """The world changed since Jordan looked (e.g. the bill was already approved)."""


# ------------------------------------------------------------------ payloads


class AgreementDraft(BaseModel):
    """What the assistant proposes. Version number is assigned on confirm."""

    helper_id: str
    effective_from: date
    agreed_on: date | None = None
    terms: list[Any]
    schedule: dict = {}
    date_notes: list[dict] = []
    notes: list[str] = []
    sources: list[dict] = []
    basis: Literal["written", "recollection"] = "written"

    def as_version(self, version: int) -> AgreementVersion:
        return AgreementVersion(version=version, **self.model_dump())


class AgreementProposal(BaseModel):
    new_helper: Helper | None = None
    agreement: AgreementDraft
    conflicts: list[str] = []  # where sources disagree and how this proposal resolves it


LineChoice = Literal["agreement", "billed", "exclude"]


class ApprovalProposal(BaseModel):
    bill_id: str
    decisions: dict[int, LineChoice] = {}
    amount: Decimal
    note: str = ""


PROPOSAL_KINDS = {"agreement": AgreementProposal, "approval": ApprovalProposal}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "helper"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ------------------------------------------------------------------- service


class Household:
    def __init__(self, store: Store, now: Callable[[], datetime] | None = None) -> None:
        self.db = store
        self.now = now or (lambda: datetime.now(UTC))

    # ---- audit

    def _audit(self, actor: str, action: str, ref: str, detail: str = "") -> None:
        ts = self.now().isoformat()
        self.db.put(
            f"AUDIT#{ts}#{uuid.uuid4().hex[:6]}",
            {"ts": ts, "actor": actor, "action": action, "ref": ref, "detail": detail},
        )

    def audit_log(self, limit: int = 200) -> list[dict]:
        return list(reversed(self.db.query("AUDIT#")))[:limit]

    # ---- sources (what Jordan gave us)

    def add_source(
        self,
        *,
        kind: Literal["text", "image"],
        title: str,
        text: str | None = None,
        blob_key: str | None = None,
        content_type: str | None = None,
    ) -> dict:
        src = {
            "source_id": new_id("src"),
            "kind": kind,
            "title": title.strip() or ("Pasted text" if kind == "text" else "Screenshot"),
            "text": text,
            "blob_key": blob_key,
            "content_type": content_type,
            "created_at": self.now().isoformat(),
        }
        self.db.put(f"SOURCE#{src['source_id']}", src, if_absent=True)
        self._audit("jordan", "added_source", src["source_id"], src["title"])
        return src

    def set_transcript(self, source_id: str, text: str) -> None:
        src = self.source(source_id)
        self.db.put(f"SOURCE#{source_id}", {**src, "text": text})

    def source(self, source_id: str) -> dict:
        src = self.db.get(f"SOURCE#{source_id}")
        if not src:
            raise DomainError(f"No document {source_id}.")
        return src

    def sources(self) -> list[dict]:
        return sorted(self.db.query("SOURCE#"), key=lambda s: s["created_at"])

    # ---- helpers and agreements

    def helpers(self) -> list[Helper]:
        return [Helper(**h) for h in self.db.query("HELPER#")]

    def helper(self, helper_id: str) -> Helper | None:
        h = self.db.get(f"HELPER#{helper_id}")
        return Helper(**h) if h else None

    def versions(self, helper_id: str) -> list[AgreementVersion]:
        return [AgreementVersion(**v) for v in self.db.query(f"AGREEMENT#{helper_id}#")]

    # ---- bills

    def record_bill(self, bill: Bill, actor: str = "assistant") -> tuple[Bill, list[str]]:
        """Store a bill as an open draft. Not money-moving, so no confirmation.

        Returns the bill and warnings (e.g. this period is already paid).
        """
        if not self.helper(bill.helper_id):
            raise DomainError(f"Unknown helper '{bill.helper_id}'. Propose their agreement first.")
        warnings = []
        for other in self.bills(bill.helper_id):
            if other.period_start <= bill.period_end and bill.period_start <= other.period_end:
                if other.status == BillStatus.APPROVED:
                    warnings.append(
                        f"{other.period_start:%B %Y} is already approved for this helper "
                        f"(bill {other.bill_id}). This may be a duplicate; don't pay it twice."
                    )
                elif other.status == BillStatus.OPEN:
                    self.db.update_if(
                        f"BILL#{other.bill_id}",
                        {"status": BillStatus.SUPERSEDED.value, "superseded_by": bill.bill_id},
                        field="status",
                        expected=BillStatus.OPEN.value,
                    )
                    warnings.append(f"Replaced the earlier open bill {other.bill_id} for the same period.")
        bill = bill.model_copy(update={"status": BillStatus.OPEN})
        item = {**bill.model_dump(mode="json"), "created_at": self.now().isoformat()}
        self.db.put(f"BILL#{bill.bill_id}", item, if_absent=True)
        self._audit(actor, "recorded_bill", bill.bill_id, f"{bill.helper_id}, {len(bill.lines)} lines")
        return bill, warnings

    def bill(self, bill_id: str) -> Bill:
        b = self.db.get(f"BILL#{bill_id}")
        if not b:
            raise DomainError(f"No bill {bill_id}.")
        return Bill(**b)

    def bill_record(self, bill_id: str) -> dict:
        b = self.db.get(f"BILL#{bill_id}")
        if not b:
            raise DomainError(f"No bill {bill_id}.")
        return b

    def bills(self, helper_id: str | None = None) -> list[Bill]:
        out = [Bill(**b) for b in self.db.query("BILL#")]
        return [b for b in out if helper_id is None or b.helper_id == helper_id]

    def correct_bill_lines(self, bill_id: str, lines: list[BillLine], actor: str = "jordan") -> Bill:
        """Fix how a bill was read. Refused once the bill is approved."""
        b = self.bill_record(bill_id)
        try:
            self.db.update_if(
                f"BILL#{bill_id}",
                {"lines": [l.model_dump(mode="json") for l in lines], "corrected_at": self.now().isoformat()},
                field="status",
                expected=BillStatus.OPEN.value,
            )
        except ConditionFailed:
            raise Conflict(f"Bill {bill_id} is {b['status']} and can't be edited.") from None
        self._audit(actor, "corrected_bill", bill_id)
        return self.bill(bill_id)

    def check(self, bill_id: str):
        b = self.bill(bill_id)
        return check_bill(b, self.versions(b.helper_id))

    # ---- payable amount

    def payable(self, bill_id: str, decisions: dict[int, LineChoice]) -> tuple[Decimal, list[dict], list[str]]:
        """Amount to pay given Jordan's line decisions. Returns (amount, lines, missing)."""
        check = self.check(bill_id)
        covered = {LineStatus.MATCH, LineStatus.OVERCHARGE, LineStatus.UNDERCHARGE}
        total = Decimal(0)
        lines, missing = [], []
        for l in check.lines:
            choice = decisions.get(l.line_no)
            if choice is None:
                if l.status in covered:
                    choice = "agreement"
                else:
                    missing.append(f"line {l.line_no} ({l.raw})")
                    continue
            if choice == "agreement" and l.expected is None:
                missing.append(f"line {l.line_no} has no agreed price; choose billed or exclude")
                continue
            pay = {"agreement": l.expected, "billed": l.billed, "exclude": Decimal(0)}[choice]
            total += pay
            lines.append({"line_no": l.line_no, "choice": choice, "pay": str(money(pay))})
        return money(total), lines, missing

    # ---- proposals (assistant -> Jordan)

    def propose(self, kind: str, payload: dict, summary: str, session_id: str | None = None) -> dict:
        model = PROPOSAL_KINDS.get(kind)
        if not model:
            raise DomainError(f"Unknown proposal kind {kind}.")
        try:
            parsed = model(**payload)
        except ValidationError as e:
            raise DomainError(f"Proposal is malformed: {e}") from None

        preview: dict = {}
        if kind == "agreement":
            preview = self._preview_agreement(parsed)
        else:
            amount, lines, missing = self.payable(parsed.bill_id, parsed.decisions)
            if missing:
                raise DomainError("Can't propose approval yet; undecided: " + "; ".join(missing))
            if amount != money(parsed.amount):
                raise DomainError(f"Amount {fmt(parsed.amount)} doesn't match the decisions; it should be {fmt(amount)}.")
            if self.bill(parsed.bill_id).status != BillStatus.OPEN:
                raise DomainError("That bill isn't open.")
            preview = {"lines": lines}

        prop = {
            "proposal_id": new_id("prop"),
            "kind": kind,
            "status": "pending",
            "summary": summary,
            "payload": parsed.model_dump(mode="json"),
            "preview": preview,
            "session_id": session_id,
            "created_at": self.now().isoformat(),
        }
        self.db.put(f"PROPOSAL#{prop['proposal_id']}", prop, if_absent=True)
        self._audit("assistant", f"proposed_{kind}", prop["proposal_id"], summary)
        return prop

    def _preview_agreement(self, p: AgreementProposal) -> dict:
        a = p.agreement
        existing = self.helper(a.helper_id)
        if existing and p.new_helper:
            raise DomainError(f"Helper '{a.helper_id}' already exists; don't send new_helper.")
        if not existing and not p.new_helper:
            raise DomainError(f"Helper '{a.helper_id}' doesn't exist; include new_helper.")
        if p.new_helper and p.new_helper.helper_id != a.helper_id:
            raise DomainError("new_helper.helper_id must match agreement.helper_id.")
        current = self.versions(a.helper_id)
        try:
            candidate = a.as_version(len(current) + 1)
        except ValidationError as e:
            raise DomainError(f"Terms are malformed: {e}") from None
        same_day = [v for v in current if v.effective_from == a.effective_from]
        impact = []
        for b in self.bills(a.helper_id):
            if b.status != BillStatus.OPEN:
                continue
            before = check_bill(b, current)
            after = check_bill(b, [v for v in current if v not in same_day] + [candidate])
            if (before.expected_total, before.overall) != (after.expected_total, after.overall):
                impact.append(
                    {
                        "bill_id": b.bill_id,
                        "before": str(before.expected_total),
                        "after": str(after.expected_total),
                        "overall_after": after.overall,
                    }
                )
        approved_affected = [
            b.bill_id
            for b in self.bills(a.helper_id)
            if b.status == BillStatus.APPROVED and b.period_end >= a.effective_from
        ]
        return {
            "version": candidate.version,
            "replaces_same_day": [v.version for v in same_day],
            "previous": current[-1].model_dump(mode="json") if current else None,
            "open_bill_impact": impact,
            "approved_bills_unchanged": approved_affected,
        }

    def pending_terms(self, helper_id: str) -> list[dict]:
        return [
            p
            for p in self.proposals("pending")
            if p["kind"] == "agreement" and p["payload"]["agreement"]["helper_id"] == helper_id
        ]

    def proposals(self, status: str | None = None) -> list[dict]:
        out = sorted(self.db.query("PROPOSAL#"), key=lambda p: p["created_at"])
        return [p for p in out if status is None or p["status"] == status]

    def confirm_proposal(self, proposal_id: str) -> dict:
        """Jordan said yes. Only the UI calls this; the agent has no path to it."""
        sk = f"PROPOSAL#{proposal_id}"
        prop = self.db.get(sk)
        if not prop:
            raise DomainError(f"No proposal {proposal_id}.")
        if prop["status"] != "pending":
            raise Conflict(f"This proposal is already {prop['status']}.")

        if prop["kind"] == "agreement":
            result = self._apply_agreement(AgreementProposal(**prop["payload"]), proposal_id)
        else:
            p = ApprovalProposal(**prop["payload"])
            result = self.approve_bill(p.bill_id, p.decisions, p.amount, p.note, via=proposal_id)

        try:
            self.db.update_if(
                sk,
                {"status": "confirmed", "decided_at": self.now().isoformat()},
                field="status",
                expected="pending",
            )
        except ConditionFailed:
            raise Conflict("This proposal was decided in the meantime.") from None
        return result

    def reject_proposal(self, proposal_id: str, reason: str = "") -> None:
        try:
            self.db.update_if(
                f"PROPOSAL#{proposal_id}",
                {"status": "rejected", "decided_at": self.now().isoformat(), "reason": reason},
                field="status",
                expected="pending",
            )
        except ConditionFailed:
            raise Conflict("This proposal isn't pending.") from None
        self._audit("jordan", "rejected_proposal", proposal_id, reason)

    def _apply_agreement(self, p: AgreementProposal, proposal_id: str) -> dict:
        a = p.agreement
        existing = self.helper(a.helper_id)
        if p.new_helper and existing:
            # A new helper's history arrives as several proposals at once (Lupe v1 and v2);
            # confirming the first one adds her, the rest just add versions.
            if existing.name.casefold() != p.new_helper.name.casefold():
                raise Conflict(f"A different helper '{existing.name}' already uses the id '{a.helper_id}'.")
        elif p.new_helper:
            try:
                self.db.put(f"HELPER#{a.helper_id}", p.new_helper.model_dump(mode="json"), if_absent=True)
            except ConditionFailed:
                raise Conflict(f"{p.new_helper.name} was added in the meantime. Ask for a fresh proposal.") from None
            self._audit("jordan", "added_helper", a.helper_id, p.new_helper.name)
        n = len(self.versions(a.helper_id)) + 1
        v = a.as_version(n).model_copy(update={"confirmed_at": self.now()})
        try:
            self.db.put(
                f"AGREEMENT#{a.helper_id}#{n:04d}",
                {**v.model_dump(mode="json"), "proposal_id": proposal_id},
                if_absent=True,
            )
        except ConditionFailed:
            raise Conflict("The agreement changed in the meantime. Ask for a fresh proposal.") from None
        self._audit("jordan", "confirmed_agreement", f"{a.helper_id} v{n}", f"effective {a.effective_from}")
        return {"helper_id": a.helper_id, "version": n}

    # ---- approval (Jordan only)

    def approve_bill(
        self,
        bill_id: str,
        decisions: dict[int, LineChoice],
        amount: Decimal,
        note: str = "",
        via: str | None = None,
    ) -> dict:
        """Lock a bill for payment at exactly the amount Jordan saw."""
        record = self.bill_record(bill_id)
        if record["status"] != BillStatus.OPEN.value:
            raise Conflict(f"This bill is {record['status']}; it can't be approved again.")
        if self.pending_terms(record["helper_id"]):
            raise DomainError(
                "A change to this helper's terms is waiting for your OK. Save or reject it first, "
                "so the bill is checked against the right terms."
            )
        computed, lines, missing = self.payable(bill_id, decisions)
        if missing:
            raise DomainError("Decide these first: " + "; ".join(missing))
        if computed != money(Decimal(str(amount))):
            raise Conflict(
                f"The amount is now {fmt(computed)}, not {fmt(Decimal(str(amount)))}. "
                "Something changed since you looked; review again."
            )
        check = self.check(bill_id)
        approval = {
            "amount": str(computed),
            "lines": lines,
            "note": note,
            "approved_at": self.now().isoformat(),
            "approved_by": "jordan",
            "via_proposal": via,
            "check_snapshot": check.model_dump(mode="json"),
            "agreement_versions": sorted({l.agreement_version for l in check.lines if l.agreement_version}),
        }
        try:
            self.db.update_if(
                f"BILL#{bill_id}",
                {"status": BillStatus.APPROVED.value, "approval": approval},
                field="status",
                expected=BillStatus.OPEN.value,
            )
        except ConditionFailed:
            raise Conflict("This bill was approved or replaced in the meantime.") from None
        self._audit("jordan", "approved_bill", bill_id, f"{fmt(computed)}" + (f" ({note})" if note else ""))
        return {"bill_id": bill_id, "amount": str(computed)}
