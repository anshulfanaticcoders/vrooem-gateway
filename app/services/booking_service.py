"""Booking orchestration — routes booking requests to the correct adapter."""

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

    # Retrieve the vehicle from cache (stored during search)
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
