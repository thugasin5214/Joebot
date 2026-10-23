"""Claude on Amazon Bedrock.

Claude Opus 5.5 and the other current models are served on Bedrock's
Messages-API endpoint (https://bedrock-mantle.{region}.api.aws/anthropic), not
through the Converse API, so the agent uses Strands' Anthropic provider with the
SDK's SigV4 Mantle client (Lambda role credentials, no API key). Earlier Claude
models (e.g. us.anthropic.claude-opus-4-6-v1) go through Converse; see uses_converse().
"""

from __future__ import annotations

import re
from typing import Any

from anthropic import AnthropicBedrockMantle, AsyncAnthropicBedrockMantle
from strands.models.anthropic import AnthropicModel

THINKING_TYPES = {"thinking", "redacted_thinking"}

# Messages-API (Mantle) ids look like "anthropic.claude-opus-5-5". Ids with a
# cross-region prefix ("us.anthropic..."), a version suffix ("...-v1", "...-v1:0")
# or an ARN are Bedrock runtime models, called through the Converse API.
_CONVERSE_ID = re.compile(r"^(arn:|(us|eu|apac|jp|au|global|us-gov)\.)|-v\d+(:\d+)?$")


def uses_converse(model_id: str) -> bool:
    return bool(_CONVERSE_ID.search(model_id))


def strip_prior_turn_thinking(messages: list[dict]) -> list[dict]:
    """Drop thinking blocks from turns before the current one.

    Restored history (AgentCore Memory) and injected long-term memory can differ
    byte-for-byte from what the model saw, and current models reject replayed
    thinking blocks whose prefix changed. Removing them from the front of the
    history is allowed; the current turn's blocks (needed mid tool loop) stay.
    """
    turn_start = 0
    for i, m in enumerate(messages):
        content = m.get("content")
        if m.get("role") == "user" and isinstance(content, list) and any(
            b.get("type") != "tool_result" for b in content
        ):
            turn_start = i
    out = []
    for i, m in enumerate(messages):
        if i < turn_start and m.get("role") == "assistant" and isinstance(m.get("content"), list):
            kept = [b for b in m["content"] if b.get("type") not in THINKING_TYPES]
            m = {**m, "content": kept or [{"type": "text", "text": "(no text)"}]}
        out.append(m)
    return out


class BedrockMantleModel(AnthropicModel):
    def __init__(self, *, model_id: str, region: str, effort: str = "high", max_tokens: int = 16000) -> None:
        # Current models reject temperature/top_p and run adaptive thinking by default.
        super().__init__(
            client_args={"api_key": "unused"},  # replaced below; Mantle signs with SigV4
            model_id=model_id,
            max_tokens=max_tokens,
            params={"output_config": {"effort": effort}},
        )
        self.client = AsyncAnthropicBedrockMantle(aws_region=region)

    def format_request(self, messages, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super().format_request(messages, *args, **kwargs)
        request["messages"] = strip_prior_turn_thinking(request["messages"])
        return request


def mantle_client(region: str) -> AnthropicBedrockMantle:
    return AnthropicBedrockMantle(aws_region=region)
