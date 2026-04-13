"""Service that proxies Provider API requests to Laravel's internal endpoints."""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ProviderApiService:
    """Calls Laravel's /api/internal/provider/* endpoints."""

    BOOKING_TIMEOUT_SECONDS = 90.0

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.laravel_base_url.rstrip("/")
        self.token = settings.laravel_api_token
        self.client = httpx.AsyncClient(timeout=30.0)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Gateway-Token": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def search_vehicles(self, params: dict) -> dict:
        """POST /api/internal/provider/vehicles/search"""
        response = await self.client.post(
            f"{self.base_url}/api/internal/provider/vehicles/search",
            json=params,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_vehicle_extras(self, vehicle_id: int) -> dict:
        """GET /api/internal/provider/vehicles/{id}/extras"""
        response = await self.client.get(
            f"{self.base_url}/api/internal/provider/vehicles/{vehicle_id}/extras",
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_locations(self) -> dict:
        """GET /api/internal/locations"""
        response = await self.client.get(
            f"{self.base_url}/api/internal/locations",
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def create_booking(self, payload: dict) -> dict:
        """POST /api/internal/provider/bookings"""
        response = await self.client.post(
            f"{self.base_url}/api/internal/provider/bookings",
            json=payload,
            headers=self._headers(),
            timeout=self.BOOKING_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    async def get_booking(self, booking_number: str, consumer_id: int) -> dict:
        """GET /api/internal/provider/bookings/{number}"""
        response = await self.client.get(
            f"{self.base_url}/api/internal/provider/bookings/{booking_number}",
            params={"api_consumer_id": consumer_id},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def cancel_booking(self, booking_number: str, consumer_id: int, reason: str = "") -> dict:
        """POST /api/internal/provider/bookings/{number}/cancel"""
        response = await self.client.post(
            f"{self.base_url}/api/internal/provider/bookings/{booking_number}/cancel",
            params={"api_consumer_id": consumer_id},
            json={"reason": reason},
            headers=self._headers(),
            timeout=self.BOOKING_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()


# Singleton
_service: ProviderApiService | None = None


def get_provider_api_service() -> ProviderApiService:
    global _service
    if _service is None:
        _service = ProviderApiService()
    return _service


async def close_provider_api_service():
    global _service
    if _service:
        await _service.close()
        _service = None
