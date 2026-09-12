# backend/app/routes/events.py
import asyncio
import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
from ..broadcast import subscribe, unsubscribe
from ..config import get_settings

router = APIRouter(prefix="/api")

@router.get("/events/{pin}")
async def event_stream(pin: str):
    settings = get_settings()
    q = subscribe(pin)

    async def generate():
        try:
            while True:
                msg = await q.get()
                yield {"event": msg["event"], "data": json.dumps(msg["data"])}
        finally:
            unsubscribe(pin, q)

    return EventSourceResponse(generate(), ping=settings.sse_keepalive_s)
