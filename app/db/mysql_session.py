"""MySQL session management for Provider API tables (Laravel's database)."""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_mysql_engine = None
_mysql_session_factory = None


def get_mysql_engine():
    """Get or create the async MySQL engine."""
    global _mysql_engine
    if _mysql_engine is None:
        settings = get_settings()
        _mysql_engine = create_async_engine(
            settings.mysql_url,
            echo=settings.gateway_debug,
            pool_size=5,
            max_overflow=10,
        )
    return _mysql_engine


def get_mysql_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the MySQL session factory."""
    global _mysql_session_factory
    if _mysql_session_factory is None:
        _mysql_session_factory = async_sessionmaker(
            get_mysql_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _mysql_session_factory


async def get_mysql_db() -> AsyncSession:
    """Dependency that yields a MySQL database session."""
    factory = get_mysql_session_factory()
    async with factory() as session:
        yield session


async def check_mysql_health() -> bool:
    """Check if MySQL is reachable."""
    try:
        engine = get_mysql_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("MySQL health check failed", exc_info=True)
        return False


async def close_mysql() -> None:
    """Close the MySQL engine."""
    global _mysql_engine, _mysql_session_factory
    if _mysql_engine is not None:
        await _mysql_engine.dispose()
        _mysql_engine = None
        _mysql_session_factory = None
