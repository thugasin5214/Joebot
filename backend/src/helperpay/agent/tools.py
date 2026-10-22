"""Tools the assistant can call.

Read tools and proposal tools only. There is deliberately no tool that confirms
a proposal or approves a bill; those exist only behind Jordan's clicks in the UI.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel
from strands import tool

from ..checker import fmt
from ..models import Bill, BillLine, DateNote, Helper, Schedule, SourceRef, Term
from ..service import DomainError, Household


@dataclass
class TurnContext:
    """Per-request state shared with the tools."""

    hh: Household
    session_id: str
    transcribe: Any = None  # callable(source) -> str, for images not yet read
    proposals: list[dict] = field(default_factory=list)
    bills_recorded: list[str] = field(default_factory=list)


def _dump(x: Any) -> str:
    return json.dumps(x, default=str, indent=1)


def _plain(x: Any) -> Any:
    return x.model_dump(mode="json") if isinstance(x, BaseModel) else x


def _check_summary(hh: Household, bill_id: str) -> dict:
    c = hh.check(bill_id)
    return {
        "bill_id": bill_id,
        "verdict": c.overall,
        "headline": c.headline,
        "stated_total": str(c.stated_total) if c.stated_total is not None else None,
        "sum_of_lines": str(c.lines_sum),
        "arithmetic_ok": c.arithmetic_ok,
        "agreement_supports": str(c.expected_total),
        "needs_decision_total": str(c.undecided_total),
        "lines": [
            {
                "line_no": l.line_no,
                "raw": l.raw,
                "billed": str(l.billed),
                "expected": str(l.expected) if l.expected is not None else None,
                "status": l.status.value,
                "agreement_version": l.agreement_version,
                "steps": l.steps,
                "flags": [f.message for f in l.flags],
            }
            for l in c.lines
        ],
        "bill_flags": [f"{f.level}: {f.message}" for f in c.flags],
    }


def build_tools(ctx: TurnContext) -> list:
    hh = ctx.hh

    @tool
    def list_household() -> str:
        """Overview: helpers, their current agreement version, bills and pending proposals.

        Call this first in a conversation to see what is already recorded.
        """
        out = {"helpers": [], "pending_proposals": []}
        for h in hh.helpers():
            versions = hh.versions(h.helper_id)
            out["helpers"].append(
                {
                    **h.model_dump(),
                    "agreement_versions": [
                        {"version": v.version, "effective_from": str(v.effective_from), "basis": v.basis}
                        for v in versions
                    ],
                    "bills": [
                        {"bill_id": b.bill_id, "period": f"{b.period_start}..{b.period_end}", "status": b.status.value}
                        for b in hh.bills(h.helper_id)
                    ],
                }
            )
        for p in hh.proposals("pending"):
            out["pending_proposals"].append({"proposal_id": p["proposal_id"], "kind": p["kind"], "summary": p["summary"]})
        return _dump(out)

    @tool
    def get_agreements(helper_id: str) -> str:
        """All confirmed agreement versions for a helper, oldest first, with terms and sources.

        Args:
            helper_id: lowercase helper id, e.g. "lupe"
        """
        versions = hh.versions(helper_id)
        if not versions:
            return f"No confirmed agreements for '{helper_id}'."
        return _dump([v.model_dump(mode="json") for v in versions])

    @tool
    def read_document(source_id: str) -> str:
        """Full text of a document Jordan added (pasted text or a transcribed screenshot).

        Args:
            source_id: id like "src_ab12cd34ef"
        """
        try:
            src = hh.source(source_id)
        except DomainError as e:
            return str(e)
        if not src.get("text") and src["kind"] == "image" and ctx.transcribe:
            hh.set_transcript(source_id, ctx.transcribe(src))
            src = hh.source(source_id)
        return f'<document id="{source_id}" title="{src["title"]}" added="{src["created_at"][:10]}">\n{src.get("text") or "[not transcribed]"}\n</document>'

    @tool
    def propose_agreement(
        helper_id: str,
        effective_from: date,
        terms: list[Term],
        summary: str,
        agreed_on: date | None = None,
        new_helper_name: str | None = None,
        new_helper_role: str | None = None,
        payment_method: str = "",
        schedule: Schedule | None = None,
        date_notes: list[DateNote] | None = None,
        notes: list[str] | None = None,
        sources: list[SourceRef] | None = None,
        basis: Literal["written", "recollection"] = "written",
        conflicts: list[str] | None = None,
    ) -> str:
        """Propose a complete agreement version for Jordan to confirm. Saves nothing by itself.

        Args:
            helper_id: lowercase first name, e.g. "priya"
            effective_from: first date these terms apply
            terms: the COMPLETE set of priced terms from effective_from (copy unchanged ones forward, keep codes stable)
            summary: one line Jordan will read on the confirmation card, e.g. "Lupe: regular visit $140 -> $150 from Sep 15"
            agreed_on: date both sides agreed
            new_helper_name: only when this helper is not recorded yet
            new_helper_role: only for a new helper, e.g. "babysitter"
            payment_method: e.g. "Venmo" (new helper only)
            schedule: usual days, for sanity checks
            date_notes: one-off arrangements on specific dates
            notes: conditions that are not prices
            sources: source_id plus the exact quote each term came from
            basis: "written" if agreed in messages, "recollection" if only from Jordan's notes
            conflicts: where inputs disagree and how this proposal resolves it
        """
        new_helper = None
        if not hh.helper(helper_id):
            if not new_helper_name:
                return f"'{helper_id}' is not recorded yet; pass new_helper_name and new_helper_role."
            new_helper = Helper(
                helper_id=helper_id, name=new_helper_name, role=new_helper_role or "", payment_method=payment_method
            ).model_dump()
        payload = {
            "new_helper": new_helper,
            "conflicts": conflicts or [],
            "agreement": {
                "helper_id": helper_id,
                "effective_from": effective_from,
                "agreed_on": agreed_on,
                "terms": [_plain(t) for t in terms],
                "schedule": _plain(schedule) if schedule else {},
                "date_notes": [_plain(d) for d in date_notes or []],
                "notes": notes or [],
                "sources": [_plain(s) for s in sources or []],
                "basis": basis,
            },
        }
        try:
            prop = hh.propose("agreement", payload, summary, session_id=ctx.session_id)
        except DomainError as e:
            return f"Not proposed: {e}"
        ctx.proposals.append(prop)
        impact = prop["preview"]["open_bill_impact"]
        msg = f"Proposal {prop['proposal_id']} is waiting for Jordan to confirm (would become version {prop['preview']['version']})."
        for i in impact:
            msg += f"\nIf confirmed, open bill {i['bill_id']} would be checked at {fmt(Decimal(i['after']))} instead of {fmt(Decimal(i['before']))}."
        return msg

    @tool
    def record_bill(
        helper_id: str,
        period_start: date,
        period_end: date,
        lines: list[BillLine],
        stated_total: Decimal | None = None,
        received_on: date | None = None,
        source_id: str | None = None,
    ) -> str:
        """Record a helper's bill as an open draft, then return the checker's result.

        Recording is not approval. A new bill for the same helper and period replaces an open one.

        Args:
            helper_id: lowercase helper id
            period_start: first day the bill covers
            period_end: last day the bill covers
            lines: one entry per billed item, as written; term_code null if nothing in the agreement covers it
            stated_total: the total exactly as the helper wrote it
            received_on: date the bill arrived
            source_id: the document the bill came from
        """
        try:
            bill = Bill(
                bill_id=f"{helper_id}-{period_start:%Y-%m}-{uuid.uuid4().hex[:4]}",
                helper_id=helper_id,
                period_start=period_start,
                period_end=period_end,
                received_on=received_on,
                lines=[BillLine.model_validate(_plain(l)) for l in lines],
                stated_total=stated_total,
                source_id=source_id,
            )
            bill, warnings = hh.record_bill(bill)
        except (DomainError, ValueError) as e:
            return f"Not recorded: {e}"
        ctx.bills_recorded.append(bill.bill_id)
        return _dump({"warnings": warnings, "check": _check_summary(hh, bill.bill_id)})

    @tool
    def check_bill(bill_id: str) -> str:
        """Run the deterministic checker on a recorded bill: per-line expected amounts, steps and flags.

        Args:
            bill_id: id returned by record_bill or list_household
        """
        try:
            rec = hh.bill_record(bill_id)
            out = _check_summary(hh, bill_id)
        except DomainError as e:
            return str(e)
        out["status"] = rec["status"]
        if rec.get("approval"):
            out["approved_amount"] = rec["approval"]["amount"]
        return _dump(out)

    @tool
    def propose_bill_approval(
        bill_id: str,
        amount: Decimal,
        summary: str,
        decisions: dict[str, Literal["agreement", "billed", "exclude"]] | None = None,
        note: str = "",
    ) -> str:
        """Propose approving a bill for payment. Only when Jordan asked. Jordan must confirm in the app.

        Args:
            bill_id: the bill
            amount: total to pay, consistent with the decisions
            summary: one line for the confirmation card
            decisions: line_no -> "agreement" (pay agreed price), "billed" (pay as billed) or "exclude"; required for every line the agreement doesn't cover
            note: why, in Jordan's words
        """
        payload = {"bill_id": bill_id, "amount": amount, "decisions": decisions or {}, "note": note}
        try:
            prop = hh.propose("approval", payload, summary, session_id=ctx.session_id)
        except DomainError as e:
            return f"Not proposed: {e}"
        ctx.proposals.append(prop)
        return f"Proposal {prop['proposal_id']} is waiting for Jordan to confirm payment of {fmt(Decimal(str(amount)))}."

    return [list_household, get_agreements, read_document, propose_agreement, record_bill, check_bill, propose_bill_approval]
