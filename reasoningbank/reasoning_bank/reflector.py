"""Reflector: merged Judge + Extractor in one structured-output LLM call.

Runs the reflection prompt, enforces the Generalizability Gate (via prompt
rules + the returned `generalizable` flag), and returns at most one memory
candidate. Does NOT write to the store — the caller decides.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from reasoning_bank.config import Settings
from reasoning_bank.models import MemoryItem

REFLECTION_PROMPT = """You are the reflection module of an AI agent memory system.

Given:
- the original task
- the final result
- an observable execution trace
- optional success/failure signal

Determine whether this experience contains one reusable reasoning lesson
that could improve performance on future, similar but non-identical tasks.

Rules:
1. Produce at most one memory.
2. Do not store raw execution steps.
3. Do not store one-off facts.
4. Do not store environment-specific element IDs.
5. Prefer general reasoning strategy, decision criteria, or guardrails.
6. Lessons may come from either success or failure.
7. If no useful transferable lesson exists, set should_store=false.
8. The guidance should be concise, actionable, and reusable.

Respond strictly as a JSON object:
{
  "success": true | false,
  "should_store": true | false,
  "generalizable": true | false,
  "memory": {
    "title": "...",
    "trigger": "...",
    "guidance": "...",
    "outcome": "success" | "failure"
  }
}

If should_store is false, "memory" may be null.
"""


async def reflect(
    task: str,
    trace: Any,
    result: Any,
    success: bool | None,
    settings: Settings,
) -> MemoryItem | None:
    """Run the merged judge+extractor call. Returns a MemoryItem candidate
    (without embedding/id yet) or None. Caller handles exceptions for fail-open."""
    user_content = (
        f"Task: {task}\n\n"
        f"Success signal: {success if success is not None else 'unknown (infer from trace/result)'}\n\n"
        f"Result: {json.dumps(result) if result is not None else 'n/a'}\n\n"
        f"Trace:\n{json.dumps(trace) if trace is not None else 'n/a'}"
    )

    # Note: reasoning models like gpt-5.6-luna only support temperature=1
    # (the default) and may not accept a `reasoning` field — we send neither
    # and let the model's default reasoning behavior apply.
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": REFLECTION_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.llm_api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

    parsed = json.loads(content)

    if not parsed.get("should_store") or not parsed.get("generalizable"):
        return None
    if not parsed.get("memory"):
        return None

    m = parsed["memory"]
    return MemoryItem(
        id=f"mem_{uuid.uuid4().hex[:12]}",
        title=m["title"],
        trigger=m["trigger"],
        guidance=m["guidance"],
        outcome=m["outcome"],
        created_at=datetime.now(timezone.utc),
    )
