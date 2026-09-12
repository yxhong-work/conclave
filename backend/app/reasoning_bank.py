"""ReasoningBank-Lite client: thin async HTTP shim with fail-open wrappers.

Wraps the two lifecycle hooks (before_task / after_task) that the
ReasoningBank-Lite sidecar exposes. Every call degrades gracefully —
before_task returns "" on any error, after_task returns {stored: False} —
so the host agent is never blocked by a ReasoningBank outage (SPEC §21).
"""
import logging
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger("conclave.rbank")

# Module-level shared client (lazy). Created on first use; lives for the
# process. Timeouts are set per-request, not on the client, so different
# endpoints can have different ceilings.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=get_settings().rbank_base_url,
            timeout=httpx.Timeout(30.0, connect=3.0),
        )
    return _client


async def before_task(task: str) -> str:
    """Retrieve relevant reasoning memories and return an injectable context
    string. Fail-open: returns "" on any error (timeout, connection, non-2xx)."""
    if not get_settings().rbank_enabled:
        return ""
    try:
        r = await _get_client().post(
            "/v1/before_task",
            json={"task": task},
            timeout=5.0,
        )
        r.raise_for_status()
        return r.json().get("context", "")
    except Exception as e:
        log.debug("rbank before_task fail-open: %s", type(e).__name__)
        return ""


async def after_task(
    task: str,
    trace: Any,
    result: Any,
    success: bool | None = None,
) -> dict[str, Any]:
    """Reflect on the completed task and store at most one memory.
    Fail-open: returns {stored: False} on any error."""
    if not get_settings().rbank_enabled:
        return {"stored": False}
    try:
        r = await _get_client().post(
            "/v1/after_task",
            json={
                "task": task,
                "trace": trace,
                "result": result,
                "success": success,
            },
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("rbank after_task fail-open: %s", type(e).__name__)
        return {"stored": False}
