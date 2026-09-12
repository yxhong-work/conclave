"""High-level orchestration: before_task and after_task flows."""
from datetime import datetime
from typing import Any

from reasoning_bank.config import Settings
from reasoning_bank.embedder import embed
from reasoning_bank.formatter import format_context
from reasoning_bank.models import MemoryItem
from reasoning_bank.reflector import reflect
from reasoning_bank.retriever import retrieve
from reasoning_bank.store import Store


async def before_task(task: str, store: Store, settings: Settings) -> str:
    """Retrieve top-k memories and format them as an injectable context string.
    Fail-open: returns "" on any error."""
    try:
        memories = await retrieve(
            task,
            store,
            settings.embedding_api_base,
            settings.embedding_api_key,
            settings.embedding_model,
            settings.retrieval_top_k,
            settings.similarity_threshold,
        )
        return format_context(memories)
    except Exception:
        return ""


async def after_task(
    task: str,
    trace: Any,
    result: Any,
    success: bool | None,
    store: Store,
    settings: Settings,
) -> tuple[bool, str | None]:
    """Reflect -> gate -> dedup -> store. Returns (stored, reason).
    Fail-open: returns (False, "reflection_failed") on any error.
    Enforces 0-or-1 memory per task."""
    try:
        candidate = await reflect(task, trace, result, success, settings)
    except Exception:
        return False, "reflection_failed"

    if candidate is None:
        return False, "should_store_false"

    # Compute the candidate's embedding for dedup + storage.
    try:
        candidate.embedding = await embed(
            candidate.trigger,
            settings.embedding_api_base,
            settings.embedding_api_key,
            settings.embedding_model,
        )
    except Exception:
        return False, "embedding_failed"

    # Dedup against nearest existing memory.
    nearest = store.search(candidate.embedding, top_k=1)
    if nearest and nearest[0][1] >= settings.dedup_threshold:
        return False, "dedup_hit"

    store.insert(candidate)
    return True, None
