"""Booking endpoints — create and cancel bookings through provider adapters."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.core.auth import verify_api_key
from app.schemas.booking import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.services.booking_service import create_booking, cancel_booking
from app.services.cache_service import CacheService, get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bookings", tags=["bookings"])


@router.post("", response_model=BookingResponse)
async def create_booking_endpoint(
    request: CreateBookingRequest,
    _api_key: str = Depends(verify_api_key),
) -> BookingResponse:
    """Create a booking for a vehicle from search results."""

    redis = await get_redis()
    cache = CacheService(redis)

    try:
        return await create_booking(request, cache)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{gateway_booking_id}",
    response_model=CancelBookingResponse,
)
async def cancel_booking_endpoint(
    gateway_booking_id: str = Path(description="Gateway booking ID (bk_...)"),
    supplier_id: str = Query(..., description="Supplier ID (e.g. renteon)"),
    supplier_booking_id: str = Query(..., description="Supplier's booking reference"),
    reason: str = Query("", description="Cancellation reason"),
    _api_key: str = Depends(verify_api_key),
) -> CancelBookingResponse:
    """Cancel an existing booking."""

    try:
        return await cancel_booking(
            gateway_booking_id,
            supplier_id,
            supplier_booking_id,
            CancelBookingRequest(reason=reason),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
