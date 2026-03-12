import unittest
from unittest.mock import AsyncMock, patch

from app.services.cache_service import CacheService


class CacheServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_set_search_uses_one_minute_default_ttl(self) -> None:
        redis_client = AsyncMock()

        with patch("app.services.cache_service.get_settings") as get_settings:
            get_settings.return_value.search_cache_ttl = 60
            cache = CacheService(redis_client)

        cache.set = AsyncMock()

        await cache.set_search({"vehicles": []}, loc=2929145933)

        cache.set.assert_awaited_once_with(
            "search:loc=2929145933",
            {"vehicles": []},
            60,
        )

