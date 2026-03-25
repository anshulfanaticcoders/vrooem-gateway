"""Vehicle search endpoint — queries all suppliers at a location in parallel."""

import logging
from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.core.auth import verify_api_key
from app.schemas.search import SearchRequest, SearchResponse
from app.schemas.search_vehicle_payload import SearchVehicleResponsePayload
from app.services.cache_service import CacheService, get_redis
from app.services.circuit_breaker import CircuitBreakerRegistry
from app.services.country_codes import resolve_country_code
from app.services.json_location_repository import JsonLocationRepository
from app.services.search_service import search_vehicles
from app.services.search_vehicle_payload_builder import build_search_vehicle_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicles"])


def _resolve_country_code(country: str) -> str | None:
    """Resolve country name or code to ISO 2-letter code."""
    return resolve_country_code(country)


_cb_registry: CircuitBreakerRegistry | None = None
_json_location_repository = JsonLocationRepository()


def set_circuit_breaker_registry(registry: CircuitBreakerRegistry) -> None:
    global _cb_registry
    _cb_registry = registry


class ProviderLocationEntry(BaseModel):
    model_config = {"extra": "allow"}

    provider: str
    pickup_id: str
    original_name: str | None = None
    dropoffs: list = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    supports_one_way: bool = False
    extended_location_code: str | None = None
    extended_dropoff_code: str | None = None
    country_code: str | None = None
    iata: str | None = None
    provider_code: str | None = None


class VehicleSearchBody(BaseModel):
    unified_location_id: int
    pickup_date: date
    dropoff_date: date
    pickup_time: time = time(9, 0)
    dropoff_time: time = time(9, 0)
    currency: str = "EUR"
    driver_age: int = 30
    dropoff_unified_location_id: int | None = None
    providers: str | None = None
    provider_locations: list[ProviderLocationEntry] | None = None
    country_code: str | None = None


async def _do_search(body: VehicleSearchBody) -> SearchResponse:
    if _cb_registry is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")

    if body.dropoff_date <= body.pickup_date:
        raise HTTPException(status_code=400, detail="Dropoff date must be after pickup date")

    pickup_time = body.pickup_time
    dropoff_time = body.dropoff_time

    if body.provider_locations:
        provider_entries = [pl.model_dump() for pl in body.provider_locations]
        country_code = body.country_code or None
        logger.info(
            "Using %d provider locations from Laravel (unified_location_id=%s)",
            len(provider_entries),
            body.unified_location_id,
        )
    else:
        location = _json_location_repository.get_location_by_unified_id(body.unified_location_id)
        if not location:
            raise HTTPException(
                status_code=404,
                detail=f"Unified location {body.unified_location_id} not found",
            )
        provider_entries = location.get("providers", [])
        if not provider_entries:
            raise HTTPException(
                status_code=404,
                detail=f"No providers serve location {body.unified_location_id}",
            )
        country_code = body.country_code or _resolve_country_code(location.get("country", ""))

    request = SearchRequest(
        unified_location_id=body.unified_location_id,
        pickup_date=body.pickup_date,
        pickup_time=pickup_time,
        dropoff_date=body.dropoff_date,
        dropoff_time=dropoff_time,
        currency=body.currency,
        driver_age=body.driver_age,
        dropoff_unified_location_id=body.dropoff_unified_location_id,
        providers=body.providers.split(",") if body.providers else None,
        country_code=country_code,
    )

    redis = await get_redis()
    cache = CacheService(redis)

    return await search_vehicles(
        request=request,
        provider_entries=provider_entries,
        cache=cache,
        cb_registry=_cb_registry,
    )


@router.post("/search", response_model=SearchVehicleResponsePayload)
async def vehicle_search_post(
    body: VehicleSearchBody,
    _api_key: str = Depends(verify_api_key),
) -> SearchVehicleResponsePayload:
    """Search vehicles — accepts provider_locations from Laravel's JSON file."""
    return build_search_vehicle_response(await _do_search(body))


@router.get("/search", response_model=SearchVehicleResponsePayload)
async def vehicle_search(
    unified_location_id: int = Query(..., description="Unified location ID"),
    pickup_date: date = Query(..., description="Pickup date (YYYY-MM-DD)"),
    dropoff_date: date = Query(..., description="Dropoff date (YYYY-MM-DD)"),
    pickup_time: time = Query(time(9, 0), description="Pickup time (HH:MM)"),
    dropoff_time: time = Query(time(9, 0), description="Dropoff time (HH:MM)"),
    currency: str = Query("EUR", description="Currency code"),
    driver_age: int = Query(30, ge=18, le=99, description="Driver age"),
    dropoff_unified_location_id: int | None = Query(None, description="Dropoff location (if one-way)"),
    providers: str | None = Query(None, description="Comma-separated provider IDs, or omit for all"),
    _api_key: str = Depends(verify_api_key),
) -> SearchVehicleResponsePayload:
    """Search vehicles (GET — backward compatible, uses JSON lookup)."""
    body = VehicleSearchBody(
        unified_location_id=unified_location_id,
        pickup_date=pickup_date,
        dropoff_date=dropoff_date,
        pickup_time=pickup_time,
        dropoff_time=dropoff_time,
        currency=currency,
        driver_age=driver_age,
        dropoff_unified_location_id=dropoff_unified_location_id,
        providers=providers,
    )
    return build_search_vehicle_response(await _do_search(body))
