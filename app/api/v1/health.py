"""Health and readiness endpoints."""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from app.db.session import check_db_health
from app.services.cache_service import CacheService, get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check — is the gateway process running?"""
    return {"status": "healthy", "service": "vrooem-gateway"}


@router.get("/ready")
async def readiness_check():
    """Deep readiness check — are all dependencies reachable?"""
    redis_ok = False
    db_ok = False

    try:
        redis_client = await get_redis()
        cache = CacheService(redis_client)
        redis_ok = await cache.health_check()
    except Exception as exc:
        logger.warning("Health check: Redis unreachable: %s", exc)

    try:
        db_ok = await check_db_health()
    except Exception as exc:
        logger.warning("Health check: Database unreachable: %s", exc)

    status = "ready" if (redis_ok and db_ok) else "degraded"
    return {
        "status": status,
        "redis": "connected" if redis_ok else "disconnected",
        "database": "connected" if db_ok else "disconnected",
    }
