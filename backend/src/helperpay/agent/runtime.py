"""Builds the assistant for one request and runs a turn.

Memory: with MEMORY_ID set, conversations live in AgentCore Memory (short-term
events per session, long-term facts/preferences/summaries per actor). Without it
(local dev), Strands' file session manager stands in.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from strands import Agent

from ..blobs import Blobs
from ..service import Household
from .prompts import SYSTEM_PROMPT, TRANSCRIBE_PROMPT
from .tools import TurnContext, build_tools

ACTOR_ID = "jordan"  # one household, one user


@dataclass
class Settings:
    model_id: str = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    region: str = os.environ.get("AWS_REGION", "us-east-1")
    memory_id: str | None = os.environ.get("MEMORY_ID") or None
    local_sessions: str = os.environ.get("LOCAL_SESSION_DIR", ".local/sessions")


def namespaces(actor_id: str = ACTOR_ID, session_id: str = "{sessionId}") -> dict[str, str]:
    """Long-term memory namespaces. Must match the strategies created in CDK."""
    return {
        "facts": f"/facts/{actor_id}/",
        "preferences": f"/preferences/{actor_id}/",
        "summaries": f"/summaries/{actor_id}/{session_id}/",
    }


def session_manager(settings: Settings, session_id: str):
    if settings.memory_id:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

        ns = namespaces()
        config = AgentCoreMemoryConfig(
            memory_id=settings.memory_id,
            session_id=session_id,
            actor_id=ACTOR_ID,
            retrieval_config={
                ns["preferences"]: RetrievalConfig(top_k=5, relevance_score=0.4),
                ns["facts"]: RetrievalConfig(top_k=8, relevance_score=0.3),
            },
        )
        return AgentCoreMemorySessionManager(config, region_name=settings.region)

    from strands.session.file_session_manager import FileSessionManager

    return FileSessionManager(session_id=session_id, storage_dir=settings.local_sessions)


def bedrock_model(settings: Settings):
    from strands.models import BedrockModel

    return BedrockModel(model_id=settings.model_id, region_name=settings.region, temperature=0.0, max_tokens=4096)


def make_transcriber(settings: Settings, blobs: Blobs):
    """Screenshot -> verbatim text, via the same Claude model on Bedrock."""

    def transcribe(src: dict) -> str:
        import boto3

        fmt = (src.get("content_type") or "image/png").split("/")[-1].replace("jpg", "jpeg")
        client = boto3.client("bedrock-runtime", region_name=settings.region)
        r = client.converse(
            modelId=settings.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"image": {"format": fmt, "source": {"bytes": blobs.get(src["blob_key"])}}},
                        {"text": TRANSCRIBE_PROMPT},
                    ],
                }
            ],
            inferenceConfig={"temperature": 0.0, "maxTokens": 2000},
        )
        return "".join(b.get("text", "") for b in r["output"]["message"]["content"]).strip()

    return transcribe


def build_agent(ctx: TurnContext, settings: Settings, model: Any = None) -> Agent:
    return Agent(
        model=model or bedrock_model(settings),
        system_prompt=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        tools=build_tools(ctx),
        session_manager=session_manager(settings, ctx.session_id),
        callback_handler=None,
    )


def compose_message(hh: Household, text: str, source_ids: list[str], transcribe=None) -> str:
    """Jordan's message plus any attached documents, fenced as data."""
    parts = [text.strip()] if text.strip() else []
    for sid in source_ids:
        src = hh.source(sid)
        if not src.get("text") and src["kind"] == "image" and transcribe:
            hh.set_transcript(sid, transcribe(src))
            src = hh.source(sid)
        kind = "screenshot (transcribed)" if src["kind"] == "image" else "pasted text"
        parts.append(
            f'<document id="{sid}" title="{src["title"]}" kind="{kind}" added="{src["created_at"][:10]}">\n'
            f'{src.get("text") or "[could not be read]"}\n</document>'
        )
    return "\n\n".join(parts)


def run_turn(
    hh: Household,
    session_id: str,
    text: str,
    source_ids: list[str] | None = None,
    *,
    settings: Settings | None = None,
    model: Any = None,
    transcribe=None,
) -> dict:
    settings = settings or Settings()
    ctx = TurnContext(hh=hh, session_id=session_id, transcribe=transcribe)
    agent = build_agent(ctx, settings, model)
    start = len(agent.messages)
    result = agent(compose_message(hh, text, source_ids or [], transcribe))
    tools_used = [
        block["toolUse"]["name"]
        for m in agent.messages[start:]
        for block in m.get("content", [])
        if "toolUse" in block
    ]
    return {
        "reply": str(result).strip(),
        "proposals": ctx.proposals,
        "bills_recorded": ctx.bills_recorded,
        "tools_used": tools_used,
    }


_CONTEXT_RE = re.compile(r"<user_context>.*?</user_context>\s*", re.S)


def transcript(session_id: str, settings: Settings | None = None) -> list[dict]:
    """The visible conversation for the UI: Jordan's words and the assistant's replies."""
    settings = settings or Settings()
    try:
        stored = session_manager(settings, session_id).list_messages(session_id, "default")
    except Exception:  # no such session yet
        return []
    out = []
    for sm in stored:
        m = sm.to_message()
        texts = [b["text"] for b in m.get("content", []) if "text" in b]
        if not texts:
            continue
        text = _CONTEXT_RE.sub("", "\n".join(texts)).strip()
        docs = re.findall(r'<document id="([^"]+)" title="([^"]*)"', text)
        text = re.sub(r"<document .*?</document>", "", text, flags=re.S).strip()
        if text or docs:
            out.append({"role": m["role"], "text": text, "documents": [{"source_id": i, "title": t} for i, t in docs]})
    return out
