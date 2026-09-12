"""Pydantic models for ReasoningBank-Lite."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class MemoryItem(BaseModel):
    """A single reasoning memory. `embedding` is server-side only and
    never travels over the wire in responses."""
    id: str = ""
    title: str
    trigger: str
    guidance: str
    outcome: Literal["success", "failure"]
    embedding: list[float] | None = None
    created_at: datetime | None = None


class BeforeTaskRequest(BaseModel):
    task: str


class BeforeTaskResponse(BaseModel):
    context: str


class AfterTaskRequest(BaseModel):
    task: str
    trace: Any = None        # opaque; passed through to the reflection LLM
    result: Any = None       # { output, success?, error? } or similar
    success: bool | None = None


class AfterTaskResponse(BaseModel):
    stored: bool
    reason: str | None = None
