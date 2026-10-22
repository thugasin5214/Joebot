"""Offline helpers for local UI work: a stub model and a starter-pack seed.

Never used in the deployed app.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from strands.models.model import Model

from .devdata import LUPE, LUPE_BILL, PRIYA, PRIYA_BILL, THEO, THEO_BILL
from .models import Helper
from .service import Household

# Hand transcriptions of the starter-pack screenshots, in the format the
# transcriber is asked to produce. Also the golden answers for extraction evals.
SCREENSHOT_TRANSCRIPTS = {
    "02-priya-rates.png": (
        "Thread with: Priya (babysitter)\nDate shown: Sun, Jun 14\n"
        "Jordan: Hi Priya! So glad you can start. What's your rate?\n"
        "Priya: Hi!! I usually do $22/hr. After 10pm it's $28/hr, and Sundays are time and a half\n"
        "Jordan: Totally fair. Sam and I want to do a date night most months, how would that work?\n"
        "Priya: For date nights I can do a flat $100 until 11! If you're out past 11 it's my late rate after that\n"
        "Jordan: Perfect, deal 🙌"
    ),
    "03-lupe-rates.png": (
        "Thread with: Lupe\nDate shown: Wed, Jan 14\n"
        "Lupe: Hello Jordan, this is Lupe, Carmen gave me your number\n"
        "Jordan: Hi Lupe! Carmen says you're amazing. We'd love weekly Tuesdays. What do you charge?\n"
        "Lupe: For your size house $140 each visit. Deep clean (inside oven, fridge, baseboards) is $220\n"
        "Jordan: Great, let's start next Tuesday\n"
        "Lupe: ok perfect 👍"
    ),
    "05-theo-rates.png": (
        "Thread with: Theo 🐕\nDate shown: Thu, Aug 27\n"
        "Theo: Hey Jordan! Quick note, I'm updating my rates for September onward: $28 for a 30-min walk, $38 for an hour.\n"
        "Jordan: No problem, thanks for the heads up\n"
        "Theo: Thanks! Biscuit's a good boy 🐕"
    ),
    "07-lupe-september-bill.png": (
        "Thread with: Lupe\nDate shown: Thu, Oct 1\n"
        "Lupe: Hi Jordan, September:\nSep 1 – $150\nSep 8 – $150\nSep 15 – $150\nSep 22 deep – $220\nSep 29 – $150\nTotal $820. Thank you!\n"
        "Jordan: Thanks Lupe! Will send today"
    ),
}


class _Stub(Model):
    def update_config(self, **kw):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, *a, **kw):
        raise NotImplementedError

    async def stream(self, *a, **kw):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {
            "contentBlockDelta": {
                "delta": {"text": "I'm running offline without a model, so I can't read documents right now. "
                "Start the server with --bedrock to use Claude."}
            }
        }
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


def offline_model() -> Model:
    return _Stub()


def _payload(version, helper=None, conflicts=()):
    return {
        "new_helper": helper.model_dump() if helper else None,
        "agreement": version.model_dump(mode="json", exclude={"version", "confirmed_at"}),
        "conflicts": list(conflicts),
    }


def seed_starter_pack(hh: Household, starter_dir: str | Path | None = None) -> None:
    """A mid-review state: some terms confirmed, some waiting, bills open."""
    if hh.helpers():
        return
    if starter_dir:
        for f in sorted(Path(starter_dir).glob("0*")):
            if f.suffix == ".txt":
                hh.add_source(kind="text", title=f.stem, text=f.read_text())
            elif f.name in SCREENSHOT_TRANSCRIPTS:
                src = hh.add_source(kind="image", title=f.stem, blob_key=None, content_type="image/png")
                hh.set_transcript(src["source_id"], SCREENSHOT_TRANSCRIPTS[f.name])

    priya = Helper(helper_id="priya", name="Priya", role="babysitter", payment_method="Venmo")
    lupe = Helper(helper_id="lupe", name="Lupe", role="cleaner", payment_method="Zelle")
    theo = Helper(helper_id="theo", name="Theo", role="dog walker", payment_method="Venmo")

    for v, h, s in [
        (PRIYA[0], priya, "Add Priya: $22/hr, $28/hr after 10pm, Sundays ×1.5, date night $100 flat until 11pm"),
        (LUPE[0], lupe, "Add Lupe: regular visit $140, deep clean $220"),
        (THEO[0], theo, "Add Theo: $25 per walk (from your notes)"),
    ]:
        hh.confirm_proposal(hh.propose("agreement", _payload(v, h), s)["proposal_id"])

    hh.propose("agreement", _payload(LUPE[1]), "Lupe: regular visit $140 → $150 from Sep 15; deep clean on Sep 22 instead")
    hh.propose(
        "agreement",
        _payload(THEO[1], conflicts=["Your notes say $25 a walk; Theo's Aug 27 text (which you accepted) sets $28 / $38 from September."]),
        "Theo: $28 per 30-min walk, $38 per hour, from Sep 1",
    )
    for b in (PRIYA_BILL, LUPE_BILL, THEO_BILL):
        hh.record_bill(b)


if __name__ == "__main__":  # print the seed for inspection
    from .store import MemoryStore

    h = Household(MemoryStore(), now=lambda: datetime.now(UTC))
    seed_starter_pack(h)
    print(json.dumps([p["summary"] for p in h.proposals()], indent=1))
