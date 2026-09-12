"""FastAPI app: three endpoints — /v1/before_task, /v1/after_task, /v1/health."""
import logging

from fastapi import FastAPI

from reasoning_bank.bank import after_task, before_task
from reasoning_bank.config import settings
from reasoning_bank.models import (
    AfterTaskRequest,
    AfterTaskResponse,
    BeforeTaskRequest,
    BeforeTaskResponse,
)
from reasoning_bank.store import Store

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("reasoning_bank")

app = FastAPI(title="ReasoningBank-Lite")
store = Store(settings.data_dir)


@app.get("/v1/health")
async def health():
    """Liveness probe — always 200 if the process is up."""
    return {"status": "ok"}


@app.post("/v1/before_task", response_model=BeforeTaskResponse)
async def before_task_endpoint(req: BeforeTaskRequest) -> BeforeTaskResponse:
    """Retrieve relevant memories and return an injectable context string.
    Fail-open: returns empty context on any error."""
    context = await before_task(req.task, store, settings)
    return BeforeTaskResponse(context=context)


@app.post("/v1/after_task", response_model=AfterTaskResponse)
async def after_task_endpoint(req: AfterTaskRequest) -> AfterTaskResponse:
    """Reflect on the completed task and store at most one memory.
    Fail-open: returns stored=False on any error."""
    stored, reason = await after_task(
        req.task, req.trace, req.result, req.success, store, settings
    )
    return AfterTaskResponse(stored=stored, reason=reason)
