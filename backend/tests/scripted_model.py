"""A stand-in for Claude that replays a script, so the agent loop runs offline."""

import json
from typing import Any

from strands.models.model import Model


class ScriptedModel(Model):
    """Each call to stream() plays the next step: a list of tool calls, or a final text."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.seen: list[list[dict]] = []  # messages the model was shown, per call

    def update_config(self, **kw):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, *a, **kw):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.seen.append(messages)
        step = self.steps.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(step, str):
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        for i, (name, args) in enumerate(step):
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{len(self.seen)}_{i}", "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
