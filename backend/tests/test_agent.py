"""Agent wiring, offline: real Strands loop, real tools, scripted model."""

from decimal import Decimal as D

import pytest

from helperpay.agent.runtime import Settings, run_turn, transcript
from helperpay.agent.tools import TurnContext, build_tools
from helperpay.service import Household
from helperpay.store import MemoryStore

from .scripted_model import ScriptedModel
from helperpay.devdata import LUPE

LUPE_V1 = LUPE[0].model_dump(mode="json")


@pytest.fixture
def settings(tmp_path):
    return Settings(memory_id=None, local_sessions=str(tmp_path / "sessions"))


def test_agent_has_no_way_to_confirm_or_approve():
    names = {t.tool_name for t in build_tools(TurnContext(hh=Household(MemoryStore()), session_id="s"))}
    assert names == {
        "list_household",
        "get_agreements",
        "read_document",
        "propose_agreement",
        "record_bill",
        "check_bill",
        "propose_bill_approval",
    }
    assert not any("confirm" in n or n.startswith("approve") for n in names)


def test_turn_proposes_then_jordan_confirms(settings):
    hh = Household(MemoryStore())
    src = hh.add_source(kind="text", title="Lupe thread", text="Lupe: For your size house $140 each visit...")
    model = ScriptedModel(
        [
            [("list_household", {})],
            [
                (
                    "propose_agreement",
                    {
                        "helper_id": "lupe",
                        "new_helper_name": "Lupe",
                        "new_helper_role": "cleaner",
                        "effective_from": LUPE_V1["effective_from"],
                        "agreed_on": LUPE_V1["agreed_on"],
                        "terms": LUPE_V1["terms"],
                        "schedule": LUPE_V1["schedule"],
                        "sources": [{"source_id": src["source_id"], "quote": "$140 each visit"}],
                        "summary": "Add Lupe: $140 regular, $220 deep clean",
                    },
                )
            ],
            "I've proposed Lupe's terms. Please review and confirm.",
        ]
    )
    out = run_turn(hh, "s1", "Here's Lupe's thread", [src["source_id"]], settings=settings, model=model)

    assert out["tools_used"] == ["list_household", "propose_agreement"]
    assert len(out["proposals"]) == 1 and hh.helpers() == []  # nothing saved yet
    first_user_msg = model.seen[0][-1]["content"][0]["text"]
    assert f'<document id="{src["source_id"]}"' in first_user_msg and "$140 each visit" in first_user_msg

    hh.confirm_proposal(out["proposals"][0]["proposal_id"])
    assert [v.terms[0].amount for v in hh.versions("lupe")] == [D(140)]

    history = transcript("s1", settings)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["documents"] == [{"source_id": src["source_id"], "title": "Lupe thread"}]
    assert "<document" not in history[0]["text"]


def test_conversation_continues_across_turns(settings):
    hh = Household(MemoryStore())
    run_turn(hh, "s2", "Remember: flag anything over $10.", settings=settings, model=ScriptedModel(["Noted."]))
    model = ScriptedModel(["You asked me to flag anything over $10."])
    run_turn(hh, "s2", "What did I ask?", settings=settings, model=model)
    shown = " ".join(b.get("text", "") for m in model.seen[0] for b in m["content"])
    assert "flag anything over $10" in shown  # earlier turn restored from the session store


def test_tool_errors_go_back_to_the_model_not_to_the_db(settings):
    hh = Household(MemoryStore())
    model = ScriptedModel(
        [
            [("propose_bill_approval", {"bill_id": "nope", "amount": 10, "summary": "pay"})],
            "That bill doesn't exist.",
        ]
    )
    out = run_turn(hh, "s3", "approve it", settings=settings, model=model)
    assert out["proposals"] == []
    tool_result = model.seen[1][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert tool_result.startswith("Not proposed")
