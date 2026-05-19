"""Booking orchestration: routes booking requests to the correct adapter."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_adapter
from app.db.models import GatewayBooking
from app.schemas.booking import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.schemas.common import BookingStatus
from app.schemas.vehicle import Vehicle
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


def _booking_response_from_record(record: GatewayBooking) -> BookingResponse:
    try:
        status = BookingStatus(record.status)
    except ValueError:
        status = BookingStatus.PENDING

    return BookingResponse(
        id=record.id,
        supplier_id=record.supplier_id,
        supplier_booking_id=record.supplier_booking_id or "",
        status=status,
        vehicle_name=record.vehicle_name or "",
        pickup_datetime=record.pickup_datetime,
        dropoff_datetime=record.dropoff_datetime,
        pickup_location=record.pickup_location or "",
        dropoff_location=record.dropoff_location or "",
        total_price=record.total_price or 0,
        currency=record.currency or "EUR",
        supplier_data=record.response_data or {},
        created_at=record.created_at,
    )


async def _find_existing_laravel_booking(
    db: AsyncSession,
    laravel_booking_id: int | None,
) -> GatewayBooking | None:
    if not laravel_booking_id:
        return None

    result = await db.execute(
        select(GatewayBooking)
        .where(GatewayBooking.laravel_booking_id == laravel_booking_id)
        .order_by(GatewayBooking.created_at.desc())
    )
    return result.scalars().first()


async def create_booking(
    request: CreateBookingRequest,
    cache: CacheService,
    db: AsyncSession,
) -> BookingResponse:
    """Create a booking by looking up the cached vehicle and calling the correct adapter."""

    existing = await _find_existing_laravel_booking(db, request.laravel_booking_id)
    if existing and existing.supplier_booking_id:
        logger.info(
            "Returning existing gateway booking for Laravel booking %s",
            request.laravel_booking_id,
        )
        return _booking_response_from_record(existing)

    lock_key = None
    lock_acquired = False
    if request.laravel_booking_id:
        lock_key = f"gateway_booking_lock:{request.laravel_booking_id}"
        lock_acquired = bool(await cache.redis.set(lock_key, "1", ex=90, nx=True))
        if not lock_acquired:
            for _ in range(10):
                await asyncio.sleep(0.5)
                existing = await _find_existing_laravel_booking(db, request.laravel_booking_id)
                if existing and existing.supplier_booking_id:
                    return _booking_response_from_record(existing)
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

        response = await adapter.create_booking(request, vehicle)
        record = GatewayBooking(
            id=response.id,
            supplier_id=response.supplier_id,
            supplier_booking_id=response.supplier_booking_id,
            laravel_booking_id=request.laravel_booking_id,
            status=response.status.value,
            vehicle_name=response.vehicle_name,
            pickup_location=response.pickup_location,
            dropoff_location=response.dropoff_location,
            pickup_datetime=response.pickup_datetime,
            dropoff_datetime=response.dropoff_datetime,
            total_price=response.total_price,
            currency=response.currency,
            driver_email=request.driver.email,
            request_data=request.model_dump(mode="json"),
            response_data=response.model_dump(mode="json"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(record)
        await db.commit()

        return response
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
