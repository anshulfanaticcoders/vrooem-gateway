"""Health and readiness endpoints."""

import logging

from fastapi import APIRouter

from app.db.mysql_session import check_mysql_health
from app.services.cache_service import CacheService, get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check — is the gateway process running?"""
    return {"status": "healthy", "service": "vrooem-gateway"}


@router.get("/ready")
async def readiness_check():
    """Deep readiness check — are all dependencies reachable?"""
    redis_ok = False
    mysql_ok = False

    try:
        redis_client = await get_redis()
        cache = CacheService(redis_client)
        redis_ok = await cache.health_check()
    except Exception as exc:
        logger.warning("Health check: Redis unreachable: %s", exc)

    try:
        mysql_ok = await check_mysql_health()
    except Exception as exc:
        logger.warning("Health check: MySQL unreachable: %s", exc)

    status = "ready" if (redis_ok and mysql_ok) else "degraded"
    return {
        "status": status,
        "redis": "connected" if redis_ok else "disconnected",
        "mysql": "connected" if mysql_ok else "disconnected",
    }
