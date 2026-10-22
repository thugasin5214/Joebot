"""Request shape sent to Claude on Bedrock (no network)."""

from helperpay.agent.model import BedrockMantleModel, strip_prior_turn_thinking
from helperpay.agent.prompts import SYSTEM_PROMPT


def _thinking(sig):
    return {"reasoningContent": {"reasoningText": {"text": "hmm", "signature": sig}}}


HISTORY = [
    {"role": "user", "content": [{"text": "turn 1"}]},
    {"role": "assistant", "content": [_thinking("old"), {"text": "answer 1"}]},
    {"role": "user", "content": [{"text": "turn 2"}]},
    {"role": "assistant", "content": [_thinking("new"), {"toolUse": {"toolUseId": "t1", "name": "list_household", "input": {}}}]},
    {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "{}"}], "status": "success"}}]},
]


def test_request_strips_only_prior_turn_thinking():
    m = BedrockMantleModel(model_id="anthropic.claude-opus-5-5", region="us-east-1")
    req = m.format_request(HISTORY, system_prompt=SYSTEM_PROMPT)
    kinds = [[b["type"] for b in msg["content"]] for msg in req["messages"]]
    assert kinds[1] == ["text"]  # turn 1's thinking dropped
    assert kinds[3] == ["thinking", "tool_use"]  # current tool loop keeps its signed block
    assert req["messages"][3]["content"][0]["signature"] == "new"
    assert req["model"] == "anthropic.claude-opus-5-5"
    assert req["output_config"] == {"effort": "high"}
    assert not {"temperature", "top_p", "top_k"} & req.keys()


def test_strip_is_noop_without_history():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert strip_prior_turn_thinking(msgs) == msgs


def test_system_prompt_is_stable():
    # It must be byte-identical across a conversation, so nothing time-varying in it.
    assert "{" not in SYSTEM_PROMPT and "Today is" not in SYSTEM_PROMPT
