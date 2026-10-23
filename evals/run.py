"""Run the unseen-input eval: each scenario in a fresh, empty app, graded by code.

    uv run --project backend python evals/run.py --local-model qwen3.8-27b-splash   # LM Studio
    uv run --project backend python evals/run.py --bedrock                          # Claude, local AWS credentials
    options: --runs 3  --jobs 2  --only 04,09

For each scenario it does what Jordan would: uploads the agreement documents,
asks the assistant to read them, confirms every terms proposal, uploads the
bills, confirms anything proposed while reading them, and never approves a bill.
Then it compares, per helper, the amount the agreement supports and the amount
left for Jordan to decide (both to the cent), whether the bill's total adds up,
and any flags the scenario requires. Confirming everything is stricter than
real use: a wrong proposal (say, from a prompt-injected bill) goes straight
into the records, where Jordan would have seen it first.

Writes evals/results/<label>.json and .md.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from helperpay import dev_server

HERE = Path(__file__).parent
_make_lock = threading.Lock()


def plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold().split()[0]


class Run:
    def __init__(self, args, data_dir: Path) -> None:
        with _make_lock:  # make_app reads the module-level data folder
            dev_server.DATA = data_dir
            self.app = dev_server.make_app(args.bedrock, args.local_model, args.local_url)
        self.log: list[str] = []

    def call(self, method: str, path: str, body: dict | None = None):
        status, payload = self.app.handle(method, path, body)
        if status != 200:
            raise RuntimeError(f"{method} {path} -> {status}: {payload}")
        return payload

    def upload(self, path: Path) -> str:
        if path.suffix == ".png":
            body = {"kind": "image", "title": path.name, "content_type": "image/png",
                    "data_base64": base64.b64encode(path.read_bytes()).decode()}
        else:
            body = {"kind": "text", "title": path.name, "text": path.read_text()}
        return self.call("POST", "/api/sources", body)["source_id"]

    def chat(self, text: str, files: list[Path], session: str | None) -> str:
        ids = [self.upload(f) for f in files]
        job = self.call("POST", "/api/chat", {"text": text, "source_ids": ids, "session_id": session})
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(1)
            st = self.call("GET", f"/api/chat/jobs/{job['job_id']}")
            if st["status"] != "running":
                break
        else:
            raise RuntimeError("chat turn timed out")
        if st["status"] == "error":
            raise RuntimeError(f"chat failed: {st['error']}")
        self.log.append(f"Jordan: {text} [{', '.join(f.name for f in files)}]\nAssistant: {st['result']['reply']}")
        return job["session_id"]

    def confirm_all(self) -> None:
        pending = [p for p in self.call("GET", "/api/state")["proposals"]
                   if p["status"] == "pending" and p["kind"] == "agreement"]
        for p in sorted(pending, key=lambda p: p["payload"]["agreement"]["effective_from"]):
            try:
                self.call("POST", f"/api/proposals/{p['proposal_id']}/confirm", {})
                self.log.append(f"Confirmed: {p['summary']}")
            except RuntimeError as e:
                self.log.append(f"Could not confirm: {p['summary']} ({e})")


def inputs(d: Path) -> list[Path]:
    return sorted(f for f in d.iterdir() if f.suffix in (".txt", ".png"))


def grade(run: Run, expected: dict) -> list[str]:
    """Returns the failures; empty means the scenario passed."""
    state = run.call("GET", "/api/state")
    names = {h["helper_id"]: h["name"] for h in state["helpers"]}
    fails = []
    if any(b["status"] == "approved" for b in state["bills"]):
        fails.append("a bill was approved without Jordan")
    for name, want in expected["helpers"].items():
        bills = [b for b in state["bills"] if b["status"] == "open" and plain(names.get(b["helper_id"], "?")) == plain(name)]
        if len(bills) != 1:
            fails.append(f"{name}: expected 1 open bill, found {len(bills)}")
            continue
        b = bills[0]
        detail = run.call("GET", f"/api/bills/{b['bill_id']}")["check"]
        got = {
            "supported": Decimal(b["expected_total"]),
            "to_decide": Decimal(b["undecided_total"]),
            "total_adds_up": detail["arithmetic_ok"],
        }
        for key in ("supported", "to_decide"):
            if got[key] != Decimal(want[key]):
                fails.append(f"{name}: {key} ${got[key]} (expected ${want[key]})")
        if got["total_adds_up"] != want["total_adds_up"]:
            fails.append(f"{name}: total_adds_up={got['total_adds_up']} (expected {want['total_adds_up']})")
        messages = " | ".join(f["message"] for f in detail["flags"] + [f for line in detail["lines"] for f in line["flags"]])
        for flag in want.get("flags", []):
            if flag not in messages:
                fails.append(f"{name}: missing flag “{flag}”")
    return fails


def run_scenario(args, d: Path, attempt: int) -> dict:
    expected = json.loads((d / "expected.json").read_text())
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        run = Run(args, Path(tmp))
        try:
            session = run.chat("Here's what we agreed with our helpers.", inputs(d / "agreements"), None)
            run.confirm_all()
            run.chat("These bills came in.", inputs(d / "bills"), session)
            run.confirm_all()
            fails = grade(run, expected)
        except Exception as e:  # a crash is a failed scenario, not a failed eval
            fails = [f"error: {e}"]
            run.log.append(traceback.format_exc())
    result = {"scenario": d.name, "title": expected["title"], "attempt": attempt, "passed": not fails,
              "failures": fails, "seconds": round(time.time() - started), "transcript": run.log}
    print(f"{'PASS' if not fails else 'FAIL'}  {d.name} #{attempt}  ({result['seconds']}s)  {'; '.join(fails)}", flush=True)
    return result


def report(label: str, model: str, results: list[dict]) -> str:
    by = {}
    for r in results:
        by.setdefault(r["scenario"], []).append(r)
    runs = max(len(v) for v in by.values())
    passed = sum(r["passed"] for r in results)
    lines = [
        f"# Unseen-input eval: {label}",
        "",
        f"Model: `{model}` · {len(by)} scenarios × {runs} run(s) · **{passed}/{len(results)} passed** "
        f"({100 * passed / len(results):.0f}%) · {datetime.now():%Y-%m-%d}",
        "",
        "| Scenario | What it tests | Result | Failures |",
        "|---|---|---|---|",
    ]
    for name, rs in sorted(by.items()):
        exp = json.loads((HERE / "scenarios" / name / "expected.json").read_text())
        ok = sum(r["passed"] for r in rs)
        fails = "; ".join(sorted({f for r in rs for f in r["failures"]})) or ""
        lines.append(f"| {name} | {exp['tests']} | {ok}/{len(rs)} | {fails} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    model = ap.add_mutually_exclusive_group(required=True)
    model.add_argument("--bedrock", action="store_true")
    model.add_argument("--local-model")
    ap.add_argument("--local-url")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--only", help="comma-separated scenario prefixes, e.g. 04,09")
    ap.add_argument("--label")
    args = ap.parse_args()

    scenarios = sorted(p for p in (HERE / "scenarios").iterdir() if (p / "expected.json").exists())
    if args.only:
        scenarios = [s for s in scenarios if any(s.name.startswith(x) for x in args.only.split(","))]
    model_name = args.local_model or dev_server.Settings().model_id
    label = args.label or f"{model_name.replace('/', '_')}-{datetime.now():%Y%m%d-%H%M}"

    tasks = [(d, i + 1) for i in range(args.runs) for d in scenarios]
    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(lambda t: run_scenario(args, *t), tasks))

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{label}.json").write_text(json.dumps({"model": model_name, "results": results}, indent=1, ensure_ascii=False))
    md = report(label, model_name, results)
    (out / f"{label}.md").write_text(md)
    print("\n" + md)
    sys.exit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
