# backend/tests/conftest.py
import asyncio
import asyncpg
import pytest
import pytest_asyncio
from app.db import init_db

TEST_DSN = "postgresql://conclave:conclave@localhost:5432/conclave"


@pytest.fixture(autouse=True)
def _reset_global_pool(request):
    """Reset the app module's global asyncpg pool and clean tables before each test.

    Route tests drive the app through a TestClient. Starlette's TestClient (1.6)
    spins up a fresh blocking portal — and therefore a fresh event loop — per
    request when used without a ``with`` block, and a fresh portal per ``with``
    block. asyncpg connections are bound to the loop they were created on, so a
    cached global pool from a previous test's loop raises "Future attached to a
    different loop". Dropping the reference forces ``get_pool()`` to build a fresh
    pool on the current loop. For integration tests we also TRUNCATE all tables
    so each test starts from a clean DB (the app lifespan only runs migrations,
    not cleanup). Unit tests (no ``integration`` marker) keep running without a DB.
    """
    import asyncio
    from app import db

    db._pool = None
    has_db = request.node.get_closest_marker("integration") is not None
    if has_db:
        async def _truncate():
            conn = await asyncpg.connect(dsn=TEST_DSN)
            try:
                await conn.execute("TRUNCATE groups, participants, responses, rounds, member_questions CASCADE")
            finally:
                await conn.close()
        asyncio.run(_truncate())
    yield
    db._pool = None


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=5)
    # clean all tables for test isolation
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE groups, participants, responses, rounds, member_questions CASCADE")
    await init_db(p)
    yield p
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE groups, participants, responses, rounds, member_questions CASCADE")
    await p.close()
