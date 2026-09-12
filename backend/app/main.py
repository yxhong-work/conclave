import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .db import get_pool, init_db, close_pool
from .tasks import deadline_scan_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    wc = os.environ.get("WEB_CONCURRENCY", "1")
    try:
        if int(wc) > 1:
            print("WARN: in-process SSE broadcast does not cross workers; use Redis or single worker")
    except ValueError:
        pass
    pool = await get_pool()
    await init_db(pool)
    # Startup sweep: any group stranded in 'analyzing' from a previous crash is
    # unrecoverable (its task is gone). Flip it to 'error' so the creator can retry
    # via POST /start (error->analyzing gate already supports this). (SPEC §10.3)
    async with pool.acquire() as conn:
        stranded = await conn.execute(
            "UPDATE groups SET status='error' WHERE status='analyzing'"
        )
        if stranded != "UPDATE 0":
            print(f"WARN: recovered {stranded.split()[-1]} stranded 'analyzing' group(s) -> error")
    scan = asyncio.create_task(deadline_scan_loop(pool))
    yield
    scan.cancel()
    from .llm import shutdown_analysis_tasks
    await shutdown_analysis_tasks()
    await close_pool()

app = FastAPI(title="Conclave", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {"ok": True}

from .routes import groups, events, responses
app.include_router(groups.router)
app.include_router(events.router)
app.include_router(responses.router)
