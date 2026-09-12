# backend/app/broadcast.py
import asyncio
from asyncio import Queue, QueueFull

_subscribers: dict[str, set[Queue]] = {}

def subscribe(pin: str) -> Queue:
    q: Queue = Queue(maxsize=16)
    _subscribers.setdefault(pin, set()).add(q)
    return q

def unsubscribe(pin: str, q: Queue) -> None:
    subs = _subscribers.get(pin)
    if subs:
        subs.discard(q)
        if not subs:
            del _subscribers[pin]

async def broadcast(pin: str, event_type: str, data: dict) -> None:
    msg = {"event": event_type, "data": data}
    for q in list(_subscribers.get(pin, ())):
        try:
            q.put_nowait(msg)
        except QueueFull:
            unsubscribe(pin, q)  # drop slow consumer; EventSource will reconnect
