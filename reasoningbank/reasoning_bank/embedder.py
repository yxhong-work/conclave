"""External embedding client (OpenAI-compatible /v1/embeddings)."""
import httpx


async def embed(text: str, api_base: str, api_key: str, model: str) -> list[float]:
    """Return the embedding vector for `text`. Caller handles exceptions
    for fail-open."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{api_base.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": text, "model": model},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
