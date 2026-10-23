"""Drive the starter pack through a running API, the way Jordan would in the UI.

    uv run --project backend python scripts/e2e_starter_pack.py STARTER_PACK_DIR [--api http://localhost:8787]

Uploads the agreement documents, confirms every proposal the assistant makes,
uploads the September bills, and prints each bill's verdict next to the answer
key. Exit code 1 if any bill's checked amount differs from the answer key.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

EXPECTED = {"priya": 556.0, "lupe": 800.0, "theo": 580.0}  # amount the agreements support


def call(api: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        api + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def upload(api: str, path: Path) -> str:
    if path.suffix == ".png":
        body = {"kind": "image", "title": path.name, "content_type": "image/png",
                "data_base64": base64.b64encode(path.read_bytes()).decode()}
    else:
        body = {"kind": "text", "title": path.name, "text": path.read_text()}
    return call(api, "POST", "/api/sources", body)["source_id"]


def chat(api: str, text: str, source_ids: list[str], session_id: str | None) -> tuple[dict, str]:
    started = time.time()
    job = call(api, "POST", "/api/chat", {"text": text, "source_ids": source_ids, "session_id": session_id})
    seen = 0
    while True:
        time.sleep(2)
        st = call(api, "GET", f"/api/chat/jobs/{job['job_id']}")
        for p in st["progress"][seen:]:
            print(f"   … {p}")
        seen = len(st["progress"])
        if st["status"] != "running":
            break
    print(f"   ({time.time() - started:.0f}s)")
    if st["status"] == "error":
        sys.exit(f"chat failed: {st['error']}")
    return st["result"], job["session_id"]


def confirm_terms(api: str) -> None:
    """Jordan clicks Confirm on every pending terms proposal (the e2e run trusts the assistant)."""
    pending = [p for p in call(api, "GET", "/api/state")["proposals"] if p["status"] == "pending" and p["kind"] == "agreement"]
    for p in sorted(pending, key=lambda p: p["payload"]["agreement"]["effective_from"]):
        print(f"   confirm: {p['summary']}")
        call(api, "POST", f"/api/proposals/{p['proposal_id']}/confirm", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pack", type=Path)
    ap.add_argument("--api", default="http://localhost:8787")
    a = ap.parse_args()

    print("1. Agreements")
    docs = ["01-household-notes.txt", "02-priya-rates.png", "03-lupe-rates.png", "04-lupe-rate-change.txt", "05-theo-rates.png"]
    result, session = chat(a.api, "Here's what we agreed with our helpers.", [upload(a.api, a.pack / d) for d in docs], None)
    print(result["reply"], "\n")

    # Older versions first, so each later version supersedes the one before it.
    confirm_terms(a.api)

    print("\n2. Bills")
    bills = ["06-priya-september-bill.txt", "07-lupe-september-bill.png", "08-theo-september-bill.txt"]
    result, _ = chat(a.api, "September's bills came in.", [upload(a.api, a.pack / d) for d in bills], session)
    print(result["reply"], "\n")
    confirm_terms(a.api)  # Jordan confirms any terms the assistant proposed while reading the bills

    print("\n3. Verdicts vs. answer key")
    ok = True
    for b in call(a.api, "GET", "/api/state")["bills"]:
        if b["status"] != "open":
            continue
        got = float(b["expected_total"])
        want = EXPECTED.get(b["helper_id"])
        mark = "✓" if want is not None and abs(got - want) < 0.005 else "✗"
        ok &= mark == "✓"
        print(f"   {mark} {b['helper_id']:6} supported ${got:,.2f}  expected ${want}  — {b['headline']}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
