"""Base adapter class — all provider adapters inherit from this."""

import logging
import time
from abc import ABC, abstractmethod

import httpx

from app.schemas.booking import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.schemas.vehicle import Vehicle

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract base class for all supplier adapters.

    Each provider implements:
    - search_vehicles() — query the provider API and return canonical vehicles
    - create_booking() — create a reservation with the provider
    - cancel_booking() — cancel a reservation

    Optional overrides:
    - get_locations() — fetch the provider's location list for syncing
    - modify_booking() — update an existing reservation
    """

    # Subclasses MUST set these
    supplier_id: str = ""
    supplier_name: str = ""
    supports_one_way: bool = False
    default_timeout: float = 30.0

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self.http_client = http_client or httpx.AsyncClient(timeout=self.default_timeout)

    @abstractmethod
    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        """Search this provider for available vehicles.

        Args:
            request: The canonical search request.
            pickup_entry: This provider's location entry for the pickup location.
            dropoff_entry: This provider's location entry for dropoff (if one-way).

        Returns:
            List of canonical Vehicle objects.
        """
        ...

    @abstractmethod
    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        """Create a booking/reservation with this provider."""
        ...

    @abstractmethod
    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        """Cancel a booking with this provider."""
        ...

    async def get_locations(self) -> list[dict]:
        """Fetch this provider's location list for syncing.

        Returns raw location dicts — the gateway normalizes them.
        Default: empty (provider doesn't expose a location list).
        """
        return []

    async def modify_booking(
        self, supplier_booking_id: str, request: CreateBookingRequest, vehicle: Vehicle
    ) -> BookingResponse:
        """Modify an existing booking. Default: not supported."""
        raise NotImplementedError(f"{self.supplier_id} does not support booking modification")

    # ─── Helpers for subclasses ───

    async def _request(
        self,
        method: str,
        url: str,
        timeout: float | None = None,
        **kwargs,
    ) -> httpx.Response:
        """Make an HTTP request with logging and timing."""
        if timeout is not None:
            kwargs["timeout"] = timeout
        start = time.time()
        try:
            response = await self.http_client.request(method, url, **kwargs)
            elapsed_ms = int((time.time() - start) * 1000)
            body_preview = response.text[:500] if response.text else "(empty)"
            logger.info(
                "[%s] %s %s → %d (%dms) body=%s",
                self.supplier_id,
                method.upper(),
                url,
                response.status_code,
                elapsed_ms,
                body_preview,
            )
            return response
        except httpx.TimeoutException:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.error("[%s] %s %s → TIMEOUT (%dms)", self.supplier_id, method.upper(), url, elapsed_ms)
            raise
        except Exception:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.error(
                "[%s] %s %s → ERROR (%dms)", self.supplier_id, method.upper(), url, elapsed_ms, exc_info=True
            )
            raise

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.http_client.aclose()
