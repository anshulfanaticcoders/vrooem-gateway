"""Database session management for async SQLAlchemy + PostgreSQL."""

import logging
import ssl

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None


def _build_connect_args(database_url: str) -> dict:
    """Build connect_args with SSL context if the URL targets a remote host."""
    if "localhost" in database_url or "127.0.0.1" in database_url:
        return {}
    # asyncpg doesn't understand ?ssl=require — pass ssl context explicitly
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return {"ssl": ssl_ctx}


def _clean_database_url(url: str) -> str:
    """Strip ?ssl=require from URL since asyncpg handles SSL via connect_args."""
    for suffix in ["?ssl=require", "&ssl=require"]:
        url = url.replace(suffix, "")
    return url


def get_engine():
    """Get or create the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = _clean_database_url(settings.database_url)
        connect_args = _build_connect_args(settings.database_url)
        _engine = create_async_engine(
            db_url,
            echo=settings.gateway_debug,
            pool_size=10,
            max_overflow=20,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncSession:
    """Dependency that yields a database session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def check_db_health() -> bool:
    """Check if the database is reachable."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Database health check failed", exc_info=True)
        return False


async def close_db() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
