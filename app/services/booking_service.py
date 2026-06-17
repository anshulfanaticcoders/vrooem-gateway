"""Booking orchestration: routes booking requests to the correct adapter."""

import asyncio
import logging
from datetime import datetime, timezone

from app.adapters.registry import get_adapter
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
        restore_vehicle_cache = False
        vehicle_data = await cache.get_vehicle(request.vehicle_id)
        if not vehicle_data:
            vehicle_data = vehicle_context_fallback(request)
            if not vehicle_data:
                raise ValueError(
                    f"Vehicle {request.vehicle_id} not found in cache (expired or invalid)"
                )
            restore_vehicle_cache = True
        elif not str(vehicle_data.get("search_id") or "").strip():
            logger.warning(
                "Cached vehicle %s is missing search_id; requiring Laravel context fallback",
                request.vehicle_id,
            )
            vehicle_data = vehicle_context_fallback(request)
            if not vehicle_data:
                raise ValueError(
                    f"Vehicle {request.vehicle_id} cache entry is missing search_id "
                    "and no valid vehicle context was provided"
                )
            restore_vehicle_cache = True

        cached_search_id = str(vehicle_data.get("search_id") or "").strip()
        if cached_search_id and cached_search_id != request.search_id:
            raise ValueError(
                f"Vehicle {request.vehicle_id} does not belong to search {request.search_id}"
            )

        vehicle = Vehicle(**vehicle_data)
        if restore_vehicle_cache:
            logger.warning(
                "Using Laravel vehicle_context fallback for cache miss: vehicle=%s search=%s",
                request.vehicle_id,
                request.search_id,
            )
            await cache.set_vehicle(request.vehicle_id, vehicle_data)

        adapter = get_adapter(vehicle.supplier_id)
        if adapter is None:
            raise ValueError(f"No adapter for supplier: {vehicle.supplier_id}")

        logger.info(
            "Creating booking: vehicle=%s supplier=%s",
            request.vehicle_id,
            vehicle.supplier_id,
        )

        try:
            response = await adapter.create_booking(request, vehicle)
        except Exception as exc:
            logger.exception(
                "Provider booking adapter failed: vehicle=%s supplier=%s",
                request.vehicle_id,
                vehicle.supplier_id,
            )

            return provider_failure_response(request, vehicle, exc)

        return normalize_booking_response(response)
    finally:
        if lock_key and lock_acquired:
            try:
                await cache.redis.delete(lock_key)
            except Exception:
                logger.warning(
                    "Failed to release gateway booking lock: %s",
                    lock_key,
                    exc_info=True,
                )


def vehicle_context_fallback(request: CreateBookingRequest) -> dict | None:
    """Return guarded Laravel vehicle context when Redis lost the cached vehicle."""

    context = request.vehicle_context
    if not isinstance(context, dict) or not context:
        return None

    context_vehicle_id = str(context.get("id") or context.get("gateway_vehicle_id") or "").strip()
    if context_vehicle_id != request.vehicle_id:
        raise ValueError(f"Vehicle context does not match vehicle {request.vehicle_id}")

    context_search_id = str(
        context.get("search_id") or context.get("gateway_search_id") or ""
    ).strip()
    if context_search_id != request.search_id:
        raise ValueError(
            f"Vehicle context for {request.vehicle_id} does not belong to "
            f"search {request.search_id}"
        )

    expires_at = str(context.get("context_valid_until") or "").strip()
    if not expires_at:
        raise ValueError(f"Vehicle context for {request.vehicle_id} is missing expiry")

    if vehicle_context_expired(expires_at):
        raise ValueError(f"Vehicle context for {request.vehicle_id} has expired")

    vehicle_data = dict(context)
    vehicle_data["id"] = request.vehicle_id
    vehicle_data["search_id"] = request.search_id

    return vehicle_data


def vehicle_context_expired(value: str) -> bool:
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Vehicle context expiry is invalid") from exc

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at <= datetime.now(timezone.utc)


def normalize_booking_response(response: BookingResponse) -> BookingResponse:
    """Ensure every adapter follows the same success contract."""

    supplier_booking_id = (response.supplier_booking_id or "").strip()
    provider_status = response.provider_status or str(response.status.value)

    if response.status == BookingStatus.CONFIRMED and not supplier_booking_id:
        response.status = BookingStatus.FAILED
        response.failure_reason = (
            response.failure_reason or "Confirmed status without supplier booking id"
        )

    if response.status != BookingStatus.CONFIRMED and not response.failure_reason:
        response.failure_reason = "Supplier did not return a confirmed reservation"

    response.provider_status = provider_status

    return response


def provider_failure_response(
    request: CreateBookingRequest,
    vehicle: Vehicle,
    exc: Exception,
) -> BookingResponse:
    """Return a structured provider failure instead of losing context behind a 502."""

    reason = safe_failure_reason(exc)

    return BookingResponse(
        id=f"bk_failed_{request.laravel_booking_id or request.vehicle_id}",
        supplier_id=vehicle.supplier_id,
        supplier_booking_id="",
        status=BookingStatus.FAILED,
        vehicle_name=vehicle.name,
        total_price=vehicle.pricing.total_price,
        currency=vehicle.pricing.currency,
        provider_status="failed",
        failure_reason=reason,
        supplier_data={
            "error": reason,
            "exception_type": type(exc).__name__,
            "vehicle_id": request.vehicle_id,
            "search_id": request.search_id,
        },
    )


def safe_failure_reason(exc: Exception) -> str:
    """Sanitize provider/adapter exceptions for Laravel/admin visibility."""

    message = str(exc).strip() or type(exc).__name__
    message = " ".join(message.split())
    if len(message) > 500:
        return message[:500]

    return message


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
