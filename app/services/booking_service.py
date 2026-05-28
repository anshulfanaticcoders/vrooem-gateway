"""Booking orchestration: routes booking requests to the correct adapter."""

import asyncio
import logging

from app.adapters.registry import get_adapter
from app.schemas.booking import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.schemas.vehicle import Vehicle
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


async def create_booking(
    request: CreateBookingRequest,
    cache: CacheService,
) -> BookingResponse:
    """Create a booking by looking up the cached vehicle and calling the correct adapter."""

    lock_key = None
    lock_acquired = False
    if request.laravel_booking_id:
        lock_key = f"gateway_booking_lock:{request.laravel_booking_id}"
        lock_acquired = bool(await cache.redis.set(lock_key, "1", ex=90, nx=True))
        if not lock_acquired:
            for _ in range(10):
                await asyncio.sleep(0.5)
            raise ValueError("Booking is already being confirmed. Please retry shortly.")

    try:
        vehicle_data = await cache.get_vehicle(request.vehicle_id)
        if not vehicle_data:
            raise ValueError(f"Vehicle {request.vehicle_id} not found in cache (expired or invalid)")

        vehicle = Vehicle(**vehicle_data)
        adapter = get_adapter(vehicle.supplier_id)
        if adapter is None:
            raise ValueError(f"No adapter for supplier: {vehicle.supplier_id}")

        logger.info(
            "Creating booking: vehicle=%s supplier=%s",
            request.vehicle_id,
            vehicle.supplier_id,
        )

        return await adapter.create_booking(request, vehicle)
    finally:
        if lock_key and lock_acquired:
            try:
                await cache.redis.delete(lock_key)
            except Exception:
                logger.warning("Failed to release gateway booking lock: %s", lock_key, exc_info=True)


async def cancel_booking(
    gateway_booking_id: str,
    supplier_id: str,
    supplier_booking_id: str,
    request: CancelBookingRequest,
) -> CancelBookingResponse:
    """Cancel a booking through the correct adapter."""

    adapter = get_adapter(supplier_id)
    if adapter is None:
        raise ValueError(f"No adapter for supplier: {supplier_id}")

    logger.info(
        "Cancelling booking: gateway=%s supplier=%s ref=%s",
        gateway_booking_id,
        supplier_id,
        supplier_booking_id,
    )

    return await adapter.cancel_booking(supplier_booking_id, request)
