"""Location search and lookup endpoints backed by unified_locations.json."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import verify_api_key
from app.schemas.location import Location, LocationSearchResponse
from app.services.json_location_repository import JsonLocationRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])

_repository = JsonLocationRepository()
_sync_lock = asyncio.Lock()


@router.get("", response_model=list[Location])
async def list_locations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _api_key: str = Depends(verify_api_key),
) -> list[Location]:
    locations = _repository.list_locations()
    page = locations[offset:offset + limit]
    return [Location.model_validate(location) for location in page]


@router.get("/search", response_model=LocationSearchResponse)
async def search_locations(
    query: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=50),
    _api_key: str = Depends(verify_api_key),
) -> LocationSearchResponse:
    results = _repository.search_locations(query, limit)

    return LocationSearchResponse(
        query=query,
        results=[
            {
                "location": location,
                "score": 100,
                "match_type": "canonical",
            }
            for location in results
        ],
        total=len(results),
    )


@router.get("/status")
async def location_status(
    _api_key: str = Depends(verify_api_key),
) -> dict[str, str | int | float | None]:
    return _repository.metadata()


@router.get("/by-provider", response_model=Location)
async def get_location_by_provider(
    provider: str = Query(..., min_length=1),
    pickup_id: str = Query(..., min_length=1),
    _api_key: str = Depends(verify_api_key),
) -> Location:
    location = _repository.get_location_by_provider_id(provider, pickup_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")

    return Location.model_validate(location)


@router.get("/dropoffs", response_model=list[Location])
async def list_dropoff_candidates(
    provider: str = Query(..., min_length=1, description="Provider public ID (e.g. 'greenmotion')"),
    pickup_unified_id: int = Query(..., description="Pickup location unified_location_id"),
    country_code: str | None = Query(None, min_length=2, max_length=2, description="Override country filter (ISO-2)"),
    limit: int = Query(100, ge=1, le=500),
    _api_key: str = Depends(verify_api_key),
) -> list[Location]:
    """List one-way dropoff candidates for a given provider + pickup.

    Returns locations where the provider has a presence, filtered to the same
    country as the pickup (unless overridden). The pickup itself is excluded.
    """
    candidates = _repository.find_dropoff_candidates(
        provider=provider,
        pickup_unified_id=pickup_unified_id,
        country_code=country_code,
        limit=limit,
    )
    return [Location.model_validate(location) for location in candidates]


@router.post("/sync")
async def sync_locations(
    _api_key: str = Depends(verify_api_key),
) -> dict:
    """Trigger a location sync from all providers."""
    if _sync_lock.locked():
        return {"status": "already_running", "message": "A sync is already in progress"}

    async with _sync_lock:
        try:
            from app.adapters.registry import load_supplier_configs
            from app.services.location_json_refresh_service import LocationJsonRefreshService
            load_supplier_configs()
            service = LocationJsonRefreshService()
            summary = await service.refresh()
            _repository.reload()
            logger.info("Location sync completed: %s", summary)
            return {"status": "completed", "summary": summary}
        except Exception as exc:
            logger.exception("Location sync failed: %s", exc)
            return {"status": "failed", "error": str(exc)}


@router.get("/{unified_location_id}", response_model=Location)
async def get_location(
    unified_location_id: int,
    _api_key: str = Depends(verify_api_key),
) -> Location:
    location = _repository.get_location_by_unified_id(unified_location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")

    return Location.model_validate(location)
