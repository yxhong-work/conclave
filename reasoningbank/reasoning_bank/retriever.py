"""Retrieval: embed the task, cosine-search the store, return top-k memories."""
from reasoning_bank.models import MemoryItem
from reasoning_bank.store import Store
from reasoning_bank.embedder import embed


async def retrieve(
    task: str,
    store: Store,
    api_base: str,
    api_key: str,
    model: str,
    top_k: int,
    threshold: float,
) -> list[MemoryItem]:
    query_vec = await embed(task, api_base, api_key, model)
    scored = store.search(query_vec, top_k, threshold)
    return [mem for mem, _ in scored]
