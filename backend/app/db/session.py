"""Async engine + session factory (ADR-0001). One engine per process; sessions per request.

The engine is created lazily on first use so the app (and the Stage-1 tests, which need no DB)
can import without opening a connection. `pool_pre_ping` guards against Supabase dropping idle
connections.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Lazily build the process-wide async session factory.

    NullPool: open a fresh asyncpg connection per checkout rather than pooling. This avoids
    asyncpg's loop-bound-connection issues (connections can't cross event loops) at the cost of a
    connect per request — fine for a v1 control plane; revisit with a real pool when throughput
    demands it.
    """
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine(settings.sqlalchemy_url, poolclass=NullPool)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a session, closes it after the request."""
    async with get_sessionmaker()() as session:
        yield session