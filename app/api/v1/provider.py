"""
Provider API v1 — External-facing API for car rental aggregators.

External companies authenticate via X-Api-Key header and can:
- Search available vehicles
- Get extras/insurance for a vehicle
- Create, view, and cancel bookings
"""

import logging
import time
from datetime import date, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.provider_auth import ProviderAuthContext, require_scope
from app.db.provider_models import ProviderApiLog
from app.db.mysql_session import get_mysql_db as get_db
from app.schemas.provider import (
    ProviderBookingResponse,
    ProviderCancelBookingRequest,
    ProviderCancelResponse,
    ProviderCreateBookingRequest,
    ProviderErrorResponse,
    ProviderExtrasResponse,
    ProviderLocationsResponse,
    ProviderSearchRequest,
    ProviderSearchResponse,
)
from app.services.provider_api_service import get_provider_api_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    responses={
        401: {"model": ProviderErrorResponse, "description": "Invalid or missing API key"},
        403: {"model": ProviderErrorResponse, "description": "Suspended or insufficient scope"},
        429: {"description": "Rate limit exceeded"},
    },
)


async def _log_request(
    db: AsyncSession,
    auth: ProviderAuthContext,
    request: Request,
    status_code: int,
    start_time: float,
    payload: dict | None = None,
):
    """Log API request to provider_api_logs table."""
    try:
        log = ProviderApiLog(
            api_consumer_id=auth.consumer.id,
            api_key_id=auth.api_key.id,
            method=request.method,
            endpoint=request.url.path,
            request_payload=payload,
            response_status=status_code,
            ip_address=request.client.host if request.client else "unknown",
            user_agent=(request.headers.get("user-agent") or "")[:500],
            processing_time_ms=int((time.time() - start_time) * 1000),
            created_at=datetime.utcnow(),
        )
        db.add(log)
        await db.commit()
    except Exception as e:
        logger.warning("Failed to log provider API request: %s", e)


@router.get(
    "/locations",
    response_model=ProviderLocationsResponse,
    tags=["Locations"],
    summary="List available locations",
    description="Returns all pickup/dropoff locations where internal vehicles are available.",
)
async def list_locations(
    request: Request,
    auth: ProviderAuthContext = Depends(require_scope("locations:read")),
    db: AsyncSession = Depends(get_db),
):
    start = time.time()
    service = get_provider_api_service()

    try:
        data = await service.get_locations()
        await _log_request(db, auth, request, 200, start)
        return {"data": data.get("data", data)}
    except httpx.HTTPStatusError as e:
        await _log_request(db, auth, request, e.response.status_code, start)
        raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
    except Exception as e:
        await _log_request(db, auth, request, 500, start)
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL_ERROR", "message": str(e), "status": 500}})


@router.get(
    "/vehicles/search",
    response_model=ProviderSearchResponse,
    tags=["Vehicles"],
    summary="Search available vehicles",
    description="Search internal vehicles by location and dates. Returns available vehicles with pricing.",
)
async def search_vehicles(
    request: Request,
    pickup_location_id: int = Query(..., description="Location ID from /locations endpoint", example=326),
    dropoff_location_id: int = Query(..., description="Same as pickup for same-location rental", example=326),
    pickup_date: date = Query(..., description="Pickup date (YYYY-MM-DD)", example="2026-04-15"),
    dropoff_date: date = Query(..., description="Return date, must be after pickup", example="2026-04-20"),
    pickup_time: str = Query("10:00", description="Pickup time (HH:MM)", example="10:00"),
    dropoff_time: str = Query("10:00", description="Return time (HH:MM)", example="10:00"),
    driver_age: int = Query(30, ge=18, le=99, description="Driver age (18-99)", example=30),
    currency: str = Query("EUR", min_length=3, max_length=3, description="3-letter currency code", example="EUR"),
    auth: ProviderAuthContext = Depends(require_scope("vehicles:search")),
    db: AsyncSession = Depends(get_db),
):
    start = time.time()
    service = get_provider_api_service()

    params = {
        "pickup_location_id": pickup_location_id,
        "dropoff_location_id": dropoff_location_id,
        "pickup_date": pickup_date.isoformat(),
        "pickup_time": pickup_time,
        "dropoff_date": dropoff_date.isoformat(),
        "dropoff_time": dropoff_time,
        "driver_age": driver_age,
        "currency": currency,
    }

    try:
        result = await service.search_vehicles(params)
        await _log_request(db, auth, request, 200, start, params)
        return result
    except httpx.HTTPStatusError as e:
        await _log_request(db, auth, request, e.response.status_code, start, params)
        raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
    except Exception as e:
        await _log_request(db, auth, request, 500, start, params)
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL_ERROR", "message": str(e), "status": 500}})


@router.get(
    "/vehicles/{vehicle_id}/extras",
    response_model=ProviderExtrasResponse,
    tags=["Vehicles"],
    summary="Get vehicle extras and insurance",
    description="Returns available extras (child seat, GPS, etc.) and insurance options for a vehicle.",
)
async def get_vehicle_extras(
    request: Request,
    vehicle_id: int = Path(description="Vehicle ID from search results"),
    auth: ProviderAuthContext = Depends(require_scope("vehicles:extras")),
    db: AsyncSession = Depends(get_db),
):
    start = time.time()
    service = get_provider_api_service()

    try:
        result = await service.get_vehicle_extras(vehicle_id)
        await _log_request(db, auth, request, 200, start)
        return result
    except httpx.HTTPStatusError as e:
        await _log_request(db, auth, request, e.response.status_code, start)
        raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
    except Exception as e:
        await _log_request(db, auth, request, 500, start)
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL_ERROR", "message": str(e), "status": 500}})


@router.post(
    "/bookings",
    response_model=ProviderBookingResponse,
    status_code=201,
    tags=["Bookings"],
    summary="Create a booking",
    description="Create a booking for a vehicle. Provide driver details and optional extras.",
)
async def create_booking(
    body: ProviderCreateBookingRequest,
    request: Request,
    auth: ProviderAuthContext = Depends(require_scope("bookings:create")),
    db: AsyncSession = Depends(get_db),
):
    start = time.time()
    service = get_provider_api_service()

    payload = {
        "api_consumer_id": auth.consumer.id,
        "api_consumer_name": auth.consumer.name,
        "vehicle_id": body.vehicle_id,
        "pickup_date": body.pickup_date.isoformat(),
        "pickup_time": body.pickup_time.strftime("%H:%M"),
        "dropoff_date": body.dropoff_date.isoformat(),
        "dropoff_time": body.dropoff_time.strftime("%H:%M"),
        "driver": body.driver.model_dump(),
        "extras": [e.model_dump() for e in body.extras],
        "insurance_id": body.insurance_id,
        "flight_number": body.flight_number,
        "special_requests": body.special_requests,
    }

    try:
        result = await service.create_booking(payload)
        await _log_request(db, auth, request, 201, start, {"vehicle_id": body.vehicle_id})
        data = result.get("data", result)
        return data
    except httpx.HTTPStatusError as e:
        await _log_request(db, auth, request, e.response.status_code, start, {"vehicle_id": body.vehicle_id})
        raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
    except Exception as e:
        await _log_request(db, auth, request, 500, start, {"vehicle_id": body.vehicle_id})
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL_ERROR", "message": str(e), "status": 500}})


@router.get(
    "/bookings/{booking_id}",
    response_model=ProviderBookingResponse,
    tags=["Bookings"],
    summary="Get booking details",
    description="Retrieve booking details by booking ID. You can only view your own bookings.",
)
async def get_booking(
    request: Request,
    booking_id: str = Path(description="Booking number (e.g., API-20260415-0042)"),
    auth: ProviderAuthContext = Depends(require_scope("bookings:read")),
    db: AsyncSession = Depends(get_db),
):
    start = time.time()
    service = get_provider_api_service()

    try:
        result = await service.get_booking(booking_id, auth.consumer.id)
        await _log_request(db, auth, request, 200, start)
        data = result.get("data", result)
        return data
    except httpx.HTTPStatusError as e:
        await _log_request(db, auth, request, e.response.status_code, start)
        raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
    except Exception as e:
        await _log_request(db, auth, request, 500, start)
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL_ERROR", "message": str(e), "status": 500}})


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=ProviderCancelResponse,
    tags=["Bookings"],
    summary="Cancel a booking",
    description="Cancel an existing booking. Only pending/confirmed bookings can be cancelled.",
)
async def cancel_booking(
    body: ProviderCancelBookingRequest,
    request: Request,
    booking_id: str = Path(description="Booking number (e.g., API-20260415-0042)"),
    auth: ProviderAuthContext = Depends(require_scope("bookings:cancel")),
    db: AsyncSession = Depends(get_db),
):
    start = time.time()
    service = get_provider_api_service()

    try:
        result = await service.cancel_booking(booking_id, auth.consumer.id, body.reason)
        await _log_request(db, auth, request, 200, start)
        data = result.get("data", result)
        return data
    except httpx.HTTPStatusError as e:
        await _log_request(db, auth, request, e.response.status_code, start)
        raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
    except Exception as e:
        await _log_request(db, auth, request, 500, start)
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL_ERROR", "message": str(e), "status": 500}})
