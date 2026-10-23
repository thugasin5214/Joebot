"""API behaviour through App.handle, with jobs run inline and a scripted model."""

import base64

import pytest

from helperpay.agent.runtime import Settings
from helperpay.api import App
from helperpay.blobs import LocalBlobs
from helperpay.offline import seed_starter_pack
from helperpay.service import Household
from helperpay.store import MemoryStore

from .scripted_model import ScriptedModel


@pytest.fixture
def make(tmp_path):
    def _make(steps=("ok",), seed=True):
        hh = Household(MemoryStore())
        if seed:
            seed_starter_pack(hh)
        app_ref = {}
        app = App(
            hh,
            LocalBlobs(tmp_path / "blobs"),
            Settings(memory_id=None, local_sessions=str(tmp_path / "s")),
            start_job=lambda job: app_ref["app"].run_job(job),
            model=ScriptedModel(list(steps)),
        )
        app_ref["app"] = app
        return app

    return _make


def test_state_after_seed(make):
    status, s = make().handle("GET", "/api/state", None)
    assert status == 200
    assert {h["helper_id"] for h in s["helpers"]} == {"priya", "lupe", "theo"}
    assert sorted(b["overall"] for b in s["bills"]) == ["discrepancies", "needs_review", "needs_review"]  # Theo: no 60-min walk until his raise is confirmed
    assert [p["status"] for p in s["proposals"]].count("pending") == 2


def test_confirming_theo_raise_makes_his_bill_match(make):
    app = make()
    theo = next(p for p in app.hh.proposals("pending") if p["payload"]["agreement"]["helper_id"] == "theo")
    assert app.handle("POST", f"/api/proposals/{theo['proposal_id']}/confirm", {})[0] == 200
    bill = next(b for b in app.handle("GET", "/api/state", None)[1]["bills"] if b["helper_id"] == "theo")
    assert bill["overall"] == "matches" and bill["expected_total"] == "580.00"


def test_approve_requires_explicit_confirm_and_exact_amount(make):
    app = make()
    lupe = next(p for p in app.hh.proposals("pending") if p["payload"]["agreement"]["helper_id"] == "lupe")
    app.handle("POST", f"/api/proposals/{lupe['proposal_id']}/confirm", {})
    bid = "lupe-2026-09"
    detail = app.handle("GET", f"/api/bills/{bid}", None)[1]
    assert detail["default_amount"] == "800.00"
    assert app.handle("POST", f"/api/bills/{bid}/approve", {"amount": "800"})[0] == 400
    assert app.handle("POST", f"/api/bills/{bid}/approve", {"amount": "820", "confirm": True})[0] == 409
    assert app.handle("POST", f"/api/bills/{bid}/approve", {"amount": "800", "confirm": True})[0] == 200
    status, err = app.handle("PUT", f"/api/bills/{bid}/lines", {"lines": [{"line_no": 1, "raw": "x", "amount": 1}]})
    assert status == 409 and "approved" in err["error"]


def test_quote_reflects_line_decisions(make):
    app = make()
    bid = "priya-2026-09"
    q = app.handle("POST", f"/api/bills/{bid}/quote", {"decisions": {}})[1]
    assert q["undecided"] and q["amount"] == "556.00"
    q = app.handle("POST", f"/api/bills/{bid}/quote", {"decisions": {"5": "billed"}})[1]
    assert not q["undecided"] and q["amount"] == "596.00"


def test_chat_job_runs_and_session_is_listed(make):
    app = make(steps=["Hello Jordan."], seed=False)
    src = app.handle("POST", "/api/sources", {"kind": "text", "title": "note", "text": "Theo walks Biscuit"})[1]
    status, started = app.handle("POST", "/api/chat", {"text": "hi", "source_ids": [src["source_id"]]})
    assert status == 200
    job = app.handle("GET", f"/api/chat/jobs/{started['job_id']}", None)[1]
    assert job["status"] == "done" and job["result"]["reply"] == "Hello Jordan."
    sessions = app.handle("GET", "/api/sessions", None)[1]
    assert [s["session_id"] for s in sessions] == [started["session_id"]]
    msgs = app.handle("GET", f"/api/sessions/{started['session_id']}", None)[1]["messages"]
    assert msgs[0]["documents"][0]["title"] == "note"


def test_upload_validation(make):
    app = make(seed=False)
    assert app.handle("POST", "/api/sources", {"kind": "image", "content_type": "application/pdf", "data_base64": "AA=="})[0] == 400
    png = base64.b64encode(b"\x89PNG fake").decode()
    status, src = app.handle("POST", "/api/sources", {"kind": "image", "content_type": "image/png", "data_base64": png})
    assert status == 200
    assert app.handle("GET", f"/api/sources/{src['source_id']}/file", None)[1]["data_base64"] == png


def test_unknown_route_and_errors_dont_leak(make):
    app = make(seed=False)
    assert app.handle("GET", "/api/nope", None)[0] == 404
    assert app.handle("POST", "/api/proposals/prop_x/confirm", {})[0] == 400


def test_line_editor_payload_corrects_bill_and_rechecks(make):
    # Exactly what the web editor sends: strings from form fields, HH:MM times.
    app = make()
    bid = "priya-2026-09"
    lines = app.handle("GET", f"/api/bills/{bid}", None)[1]["bill"]["lines"]
    edited = [
        {**l, "start": (l["start"] or "")[:5] or None, "end": (l["end"] or "")[:5] or None,
         "quantity": str(l["quantity"]), "amount": str(l["amount"])}
        for l in lines if l["term_code"]  # drop the $40 fee line
    ]
    for i, l in enumerate(edited, 1):
        l["line_no"] = i
    status, d = app.handle("PUT", f"/api/bills/{bid}/lines", {"lines": edited})
    assert status == 200
    assert d["undecided"] == [] and d["check"]["undecided_total"] == "0.00"
    assert d["bill"]["corrected_at"]
    assert any(a["action"] == "corrected_bill" for a in app.handle("GET", "/api/audit", None)[1])
