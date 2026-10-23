"""Builds the assistant for one request and runs a turn.

Memory: with MEMORY_ID set, conversations live in AgentCore Memory (short-term
events per session, long-term facts/preferences/summaries per actor). With
SESSION_BUCKET instead (accounts without AgentCore Memory), conversations are
kept in S3 with no long-term memory. Locally, Strands' file session manager.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Callable

from ..blobs import Blobs
from ..service import Household
from .prompts import SYSTEM_PROMPT, TRANSCRIBE_PROMPT

if TYPE_CHECKING:
    from strands import Agent

    from .tools import TurnContext

# Strands and the model SDKs are imported inside the functions that need them:
# most API requests (state, bills, approvals) never touch the agent, and loading
# it added seconds to every Lambda cold start.

ACTOR_ID = "jordan"  # one household, one user

PROGRESS_LABELS = {
    "list_household": "Looking at what's already recorded",
    "get_agreements": "Reading the agreements on file",
    "read_document": "Reading a document",
    "propose_agreement": "Drafting terms for your OK",
    "record_bill": "Recording the bill",
    "check_bill": "Checking the bill against the agreement",
    "propose_bill_approval": "Preparing an approval for your OK",
}


@dataclass
class Settings:
    model_id: str = os.environ.get("MODEL_ID", "us.anthropic.claude-opus-4-6-v1")
    effort: str = os.environ.get("EFFORT", "high")
    region: str = os.environ.get("AWS_REGION", "us-east-1")
    memory_id: str | None = os.environ.get("MEMORY_ID") or None
    session_bucket: str | None = os.environ.get("SESSION_BUCKET") or None  # fallback without AgentCore Memory
    local_sessions: str = os.environ.get("LOCAL_SESSION_DIR", ".local/sessions")
    # Local development only: an OpenAI-compatible server (LM Studio, Ollama) instead of Bedrock.
    local_model: str | None = os.environ.get("LOCAL_MODEL") or None
    local_model_url: str = os.environ.get("LOCAL_MODEL_URL", "http://localhost:1234/v1")


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

    if settings.session_bucket:
        from strands.session.s3_session_manager import S3SessionManager

        return S3SessionManager(session_id=session_id, bucket=settings.session_bucket, prefix="sessions/", region_name=settings.region)

    from strands.session.file_session_manager import FileSessionManager

    return FileSessionManager(session_id=session_id, storage_dir=settings.local_sessions)


def make_model(settings: Settings):
    if settings.local_model:
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={"base_url": settings.local_model_url, "api_key": "local"},
            model_id=settings.local_model,
            params={"max_tokens": 16000},
        )
    from .model import BedrockMantleModel
    from .model_ids import uses_converse

    if uses_converse(settings.model_id):
        from strands.models.bedrock import BedrockModel

        return BedrockModel(model_id=settings.model_id, region_name=settings.region, max_tokens=16000)
    return BedrockMantleModel(model_id=settings.model_id, region=settings.region, effort=settings.effort)


def make_transcriber(settings: Settings, blobs: Blobs):
    """Screenshot -> verbatim text, via the same model the assistant uses."""
    if settings.local_model:
        return _local_transcriber(settings, blobs)
    from .model_ids import uses_converse

    if uses_converse(settings.model_id):
        return _converse_transcriber(settings, blobs)

    def transcribe(src: dict) -> str:
        import base64

        from .model import mantle_client

        r = mantle_client(settings.region).messages.create(
            model=settings.model_id,
            max_tokens=4000,
            output_config={"effort": "low"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": src.get("content_type") or "image/png",
                                "data": base64.b64encode(blobs.get(src["blob_key"])).decode(),
                            },
                        },
                        {"type": "text", "text": TRANSCRIBE_PROMPT},
                    ],
                }
            ],
        )
        return "".join(b.text for b in r.content if b.type == "text").strip()

    return transcribe


def _converse_transcriber(settings: Settings, blobs: Blobs):
    def transcribe(src: dict) -> str:
        import boto3

        fmt = (src.get("content_type") or "image/png").split("/")[-1].replace("jpg", "jpeg")
        r = boto3.client("bedrock-runtime", region_name=settings.region).converse(
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
            inferenceConfig={"maxTokens": 4000},
        )
        return "".join(b.get("text", "") for b in r["output"]["message"]["content"]).strip()

    return transcribe


def _local_transcriber(settings: Settings, blobs: Blobs):
    def transcribe(src: dict) -> str:
        import base64

        from openai import OpenAI

        data = base64.b64encode(blobs.get(src["blob_key"])).decode()
        r = OpenAI(base_url=settings.local_model_url, api_key="local").chat.completions.create(
            model=settings.local_model,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{src.get('content_type') or 'image/png'};base64,{data}"}},
                        {"type": "text", "text": TRANSCRIBE_PROMPT},
                    ],
                }
            ],
        )
        return (r.choices[0].message.content or "").strip()

    return transcribe


def build_agent(ctx: TurnContext, settings: Settings, model: Any = None) -> Agent:
    from strands import Agent

    from .tools import build_tools

    return Agent(
        model=model or make_model(settings),
        system_prompt=SYSTEM_PROMPT,
        tools=build_tools(ctx),
        session_manager=session_manager(settings, ctx.session_id),
        callback_handler=None,
    )


def compose_message(hh: Household, text: str, source_ids: list[str], transcribe=None) -> str:
    """Jordan's message plus any attached documents, fenced as data.

    Today's date rides on each message rather than the system prompt, which must
    stay byte-identical for the whole conversation.
    """
    parts = [f"(Today is {date.today():%A, %B %-d, %Y}.)"]
    if text.strip():
        parts.append(text.strip())
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
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    from strands.hooks import BeforeToolCallEvent

    from .tools import TurnContext

    settings = settings or Settings()
    ctx = TurnContext(hh=hh, session_id=session_id, transcribe=transcribe)
    agent = build_agent(ctx, settings, model)
    if on_progress:
        agent.hooks.add_callback(
            BeforeToolCallEvent,
            lambda e: on_progress(PROGRESS_LABELS.get(e.tool_use["name"], "Working")),
        )
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
        text = re.sub(r"<document .*?</document>", "", text, flags=re.S)
        text = re.sub(r"^\(Today is [^)]*\.\)\s*", "", text).strip()
        if text or docs:
            out.append({"role": m["role"], "text": text, "documents": [{"source_id": i, "title": t} for i, t in docs]})
    return out
