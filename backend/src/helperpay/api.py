"""HTTP API used by the web app.

`App.handle(method, path, body)` is transport-free. The Lambda entry point and
the local dev server both call it. Authentication happens before this layer
(API Gateway's Cognito JWT authorizer in AWS).

Chat turns can outlast an API Gateway request, so POST /api/chat starts a job and
the UI polls GET /api/chat/jobs/{id}, which also reports progress.
"""

from __future__ import annotations

import base64
import re
import traceback
import uuid
from decimal import Decimal
from typing import Any, Callable

from pydantic import TypeAdapter, ValidationError

from .agent.runtime import Settings, run_turn, transcript
from .blobs import Blobs
from .checker import LineStatus
from .models import BillLine
from .service import Conflict, DomainError, Household, new_id

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


Route = tuple[str, re.Pattern, Callable[..., Any]]


class App:
    def __init__(
        self,
        hh: Household,
        blobs: Blobs,
        settings: Settings,
        start_job: Callable[[dict], None],
        transcribe: Callable[[dict], str] | None = None,
        model: Any = None,
    ) -> None:
        self.hh, self.blobs, self.settings = hh, blobs, settings
        self.start_job = start_job  # runs run_job(job) somewhere else (async Lambda, thread)
        self.transcribe = transcribe
        self.model = model
        r = lambda m, p, f: (m, re.compile("^" + p + "$"), f)  # noqa: E731
        self.routes: list[Route] = [
            r("GET", "/api/state", self.state),
            r("POST", "/api/sources", self.add_source),
            r("GET", "/api/sources/(?P<sid>[\\w-]+)/file", self.source_file),
            r("POST", "/api/chat", self.chat),
            r("GET", "/api/chat/jobs/(?P<jid>[\\w-]+)", self.job_status),
            r("GET", "/api/sessions", self.sessions),
            r("GET", "/api/sessions/(?P<sid>[\\w-]+)", self.session),
            r("POST", "/api/proposals/(?P<pid>[\\w-]+)/confirm", self.confirm),
            r("POST", "/api/proposals/(?P<pid>[\\w-]+)/reject", self.reject),
            r("GET", "/api/bills/(?P<bid>[\\w-]+)", self.bill),
            r("PUT", "/api/bills/(?P<bid>[\\w-]+)/lines", self.correct_lines),
            r("POST", "/api/bills/(?P<bid>[\\w-]+)/quote", self.quote),
            r("POST", "/api/bills/(?P<bid>[\\w-]+)/approve", self.approve),
            r("GET", "/api/audit", self.audit),
        ]

    # ---------------------------------------------------------------- dispatch

    def handle(self, method: str, path: str, body: dict | None) -> tuple[int, Any]:
        for m, pattern, fn in self.routes:
            match = pattern.match(path)
            if match and m == method:
                try:
                    return 200, fn(body or {}, **match.groupdict())
                except HttpError as e:
                    return e.status, {"error": str(e)}
                except Conflict as e:
                    return 409, {"error": str(e)}
                except (DomainError, ValidationError) as e:
                    return 400, {"error": str(e)}
                except Exception:
                    traceback.print_exc()
                    return 500, {"error": "Something went wrong on our side. Nothing was changed."}
        return 404, {"error": f"No route {method} {path}"}

    # ------------------------------------------------------------------ reads

    def state(self, _body) -> dict:
        hh = self.hh
        helpers = []
        for h in hh.helpers():
            helpers.append({**h.model_dump(), "versions": [v.model_dump(mode="json") for v in hh.versions(h.helper_id)]})
        bills = []
        for b in hh.bills():
            rec = hh.bill_record(b.bill_id)
            c = hh.check(b.bill_id)
            bills.append(
                {
                    "bill_id": b.bill_id,
                    "helper_id": b.helper_id,
                    "period_start": str(b.period_start),
                    "period_end": str(b.period_end),
                    "status": b.status.value,
                    "stated_total": str(b.stated_total) if b.stated_total is not None else None,
                    "overall": c.overall,
                    "headline": c.headline,
                    "expected_total": str(c.expected_total),
                    "undecided_total": str(c.undecided_total),
                    "approved_amount": rec.get("approval", {}).get("amount"),
                    "created_at": rec.get("created_at"),
                }
            )
        sources = [{k: s.get(k) for k in ("source_id", "kind", "title", "created_at")} for s in hh.sources()]
        return {
            "helpers": helpers,
            "bills": bills,
            "proposals": hh.proposals(),
            "sources": sources,
        }

    def source_file(self, _body, sid: str) -> dict:
        src = self.hh.source(sid)
        out = {"source": src}
        if src.get("blob_key"):
            out["data_base64"] = base64.b64encode(self.blobs.get(src["blob_key"])).decode()
        return out

    def bill(self, _body, bid: str) -> dict:
        rec = self.hh.bill_record(bid)
        check = self.hh.check(bid)
        covered = {LineStatus.MATCH, LineStatus.OVERCHARGE, LineStatus.UNDERCHARGE}
        defaults = {l.line_no: "agreement" for l in check.lines if l.status in covered}
        amount, _, missing = self.hh.payable(bid, defaults)
        source = None
        if rec.get("source_id"):
            try:
                s = self.hh.source(rec["source_id"])
                source = {k: s.get(k) for k in ("source_id", "kind", "title", "text")}
            except DomainError:
                pass
        return {
            "bill": rec,
            "check": check.model_dump(mode="json"),
            "default_decisions": defaults,
            "default_amount": str(amount),
            "undecided": missing,
            "source": source,
            "versions": [v.model_dump(mode="json") for v in self.hh.versions(rec["helper_id"])],
        }

    def sessions(self, _body) -> list[dict]:
        return sorted(self.hh.db.query("SESSION#"), key=lambda s: s["updated_at"], reverse=True)

    def session(self, _body, sid: str) -> dict:
        meta = self.hh.db.get(f"SESSION#{sid}")
        if not meta:
            raise HttpError(404, "No such conversation.")
        return {"session": meta, "messages": transcript(sid, self.settings)}

    def audit(self, _body) -> list[dict]:
        return self.hh.audit_log()

    # ----------------------------------------------------------------- writes

    def add_source(self, body) -> dict:
        kind = body.get("kind")
        title = (body.get("title") or "").strip()[:120]
        if kind == "text":
            text = (body.get("text") or "").strip()
            if not text:
                raise HttpError(400, "Paste some text first.")
            if len(text) > 50_000:
                raise HttpError(400, "That's too long to be a message or bill (50,000 characters max).")
            return self.hh.add_source(kind="text", title=title, text=text)
        if kind == "image":
            ctype = body.get("content_type", "")
            if ctype not in IMAGE_TYPES:
                raise HttpError(400, "Screenshots must be PNG, JPEG, GIF or WebP.")
            try:
                data = base64.b64decode(body.get("data_base64", ""), validate=True)
            except ValueError:
                raise HttpError(400, "The image didn't upload correctly. Try again.") from None
            if not data or len(data) > MAX_UPLOAD_BYTES:
                raise HttpError(400, "Images must be under 5 MB.")
            key = f"uploads/{uuid.uuid4().hex}"
            self.blobs.put(key, data, ctype)
            return self.hh.add_source(kind="image", title=title, blob_key=key, content_type=ctype)
        raise HttpError(400, "kind must be 'text' or 'image'.")

    def chat(self, body) -> dict:
        text = (body.get("text") or "").strip()
        source_ids = body.get("source_ids") or []
        if not text and not source_ids:
            raise HttpError(400, "Say something or attach a document.")
        for sid in source_ids:
            self.hh.source(sid)  # 400 if unknown
        session_id = body.get("session_id") or new_id("chat")
        now = self.hh.now().isoformat()
        meta = self.hh.db.get(f"SESSION#{session_id}")
        title = (text or "Documents")[:60]
        self.hh.db.put(
            f"SESSION#{session_id}",
            {"session_id": session_id, "title": meta["title"] if meta else title, "created_at": meta["created_at"] if meta else now, "updated_at": now},
        )
        job = {
            "job_id": new_id("job"),
            "session_id": session_id,
            "text": text,
            "source_ids": source_ids,
            "status": "running",
            "progress": [],
            "created_at": now,
        }
        self.hh.db.put(f"JOB#{job['job_id']}", job, if_absent=True)
        self.start_job(job)
        return {"job_id": job["job_id"], "session_id": session_id}

    def run_job(self, job: dict) -> None:
        """Runs the agent turn. Called by the async worker, not over HTTP."""
        sk = f"JOB#{job['job_id']}"
        progress: list[str] = []

        def on_progress(label: str) -> None:
            if not progress or progress[-1] != label:
                progress.append(label)
                self.hh.db.put(sk, {**job, "progress": progress})

        try:
            if any(self.hh.source(s)["kind"] == "image" and not self.hh.source(s).get("text") for s in job["source_ids"]):
                on_progress("Reading your screenshots")
            result = run_turn(
                self.hh,
                job["session_id"],
                job["text"],
                job["source_ids"],
                settings=self.settings,
                model=self.model,
                transcribe=self.transcribe,
                on_progress=on_progress,
            )
            self.hh.db.put(sk, {**job, "progress": progress, "status": "done", "result": result})
        except Exception as e:
            traceback.print_exc()
            self.hh.db.put(
                sk,
                {**job, "progress": progress, "status": "error", "error": f"The assistant hit an error: {type(e).__name__}. Nothing was changed. Try again."},
            )

    def job_status(self, _body, jid: str) -> dict:
        job = self.hh.db.get(f"JOB#{jid}")
        if not job:
            raise HttpError(404, "No such job.")
        return job

    def confirm(self, _body, pid: str) -> dict:
        return {"ok": True, "result": self.hh.confirm_proposal(pid)}

    def reject(self, body, pid: str) -> dict:
        self.hh.reject_proposal(pid, (body.get("reason") or "")[:500])
        return {"ok": True}

    def correct_lines(self, body, bid: str) -> dict:
        lines = TypeAdapter(list[BillLine]).validate_python(body.get("lines") or [])
        if not lines:
            raise HttpError(400, "A bill needs at least one line.")
        self.hh.correct_bill_lines(bid, lines)
        return self.bill({}, bid)

    def quote(self, body, bid: str) -> dict:
        decisions = {int(k): v for k, v in (body.get("decisions") or {}).items()}
        amount, lines, missing = self.hh.payable(bid, decisions)
        return {"amount": str(amount), "lines": lines, "undecided": missing}

    def approve(self, body, bid: str) -> dict:
        if body.get("confirm") is not True:
            raise HttpError(400, "Approval needs explicit confirmation.")
        decisions = {int(k): v for k, v in (body.get("decisions") or {}).items()}
        try:
            amount = Decimal(str(body["amount"]))
        except Exception:
            raise HttpError(400, "Missing the amount you're approving.") from None
        return self.hh.approve_bill(bid, decisions, amount, (body.get("note") or "")[:500])
