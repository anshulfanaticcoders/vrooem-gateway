"""Location search and lookup endpoints backed by unified_locations.json."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import verify_api_key
from app.schemas.location import Location, LocationSearchResponse
from app.services.json_location_repository import JsonLocationRepository

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])

_repository = JsonLocationRepository()


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


@router.get("/{unified_location_id}", response_model=Location)
async def get_location(
    unified_location_id: int,
    _api_key: str = Depends(verify_api_key),
) -> Location:
    location = _repository.get_location_by_unified_id(unified_location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")

    return Location.model_validate(location)
