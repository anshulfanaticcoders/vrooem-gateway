"""Redis cache service for search results and location data."""

import json
import logging
from typing import Any

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_pool: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get or create the Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        url = settings.redis_url
        # Coolify's managed Redis uses self-signed TLS certs — skip verification
        ssl_kwargs = {"ssl_cert_reqs": None} if url.startswith("rediss://") else {}
        _redis_pool = redis.from_url(url, decode_responses=True, **ssl_kwargs)
    return _redis_pool


async def close_redis() -> None:
    """Close the Redis connection pool."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


class CacheService:
    """Redis-backed cache for search results and general data."""

    # Default TTLs in seconds
    SEARCH_TTL = 60  # 1 minute for search results
    LOCATION_TTL = 21600  # 6 hours for location data
    VEHICLE_TTL = 600  # 10 minutes for individual vehicle details

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        settings = get_settings()
        self.search_ttl = max(0, int(getattr(settings, "search_cache_ttl", self.SEARCH_TTL)))

    async def get(self, key: str) -> Any | None:
        """Get a cached value, returns None if not found."""
        try:
            raw = await self.redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            logger.warning("Cache get failed for key: %s", key, exc_info=True)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set a cached value with optional TTL."""
        try:
            raw = json.dumps(value, default=str)
            if ttl:
                await self.redis.setex(key, ttl, raw)
            else:
                await self.redis.set(key, raw)
        except Exception:
            logger.warning("Cache set failed for key: %s", key, exc_info=True)

    async def delete(self, key: str) -> None:
        """Delete a cached value."""
        try:
            await self.redis.delete(key)
        except Exception:
            logger.warning("Cache delete failed for key: %s", key, exc_info=True)

    async def health_check(self) -> bool:
        """Check if Redis is reachable."""
        try:
            return await self.redis.ping()
        except Exception:
            return False

    # ─── Search cache helpers ───

    def search_key(self, **params: Any) -> str:
        """Build a cache key from search parameters."""
        sorted_params = sorted(params.items())
        param_str = "&".join(f"{k}={v}" for k, v in sorted_params if v is not None)
        return f"search:{param_str}"

    async def get_search(self, **params: Any) -> dict | None:
        """Get cached search results."""
        return await self.get(self.search_key(**params))

    async def set_search(self, results: dict, **params: Any) -> None:
        """Cache search results."""
        await self.set(self.search_key(**params), results, self.search_ttl)

    # ─── Vehicle cache helpers ───

    async def get_vehicle(self, vehicle_id: str) -> dict | None:
        """Get a cached vehicle by gateway ID."""
        return await self.get(f"vehicle:{vehicle_id}")

    async def set_vehicle(self, vehicle_id: str, data: dict) -> None:
        """Cache a vehicle for booking retrieval."""
        await self.set(f"vehicle:{vehicle_id}", data, self.VEHICLE_TTL)
