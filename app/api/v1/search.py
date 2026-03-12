"""Vehicle search endpoint — queries all suppliers at a location in parallel."""

import logging
from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.db.session import get_db
from app.schemas.search import SearchResponse
from app.schemas.search import SearchRequest
from app.services.cache_service import CacheService, get_redis
from app.services.circuit_breaker import CircuitBreakerRegistry
from app.services.location_repository import LocationRepository
from app.services.search_service import search_vehicles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicles"])

# Country name → ISO 3166-1 alpha-2 code mapping
_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "albania": "AL", "antigua and barbuda": "AG", "argentina": "AR",
    "armenia": "AM", "australia": "AU", "austria": "AT", "azerbaijan": "AZ",
    "belgium": "BE", "belgië": "BE", "bonaire": "BQ",
    "bosnia and herzegovina": "BA", "bulgaria": "BG",
    "canada": "CA", "colombia": "CO", "costa rica": "CR", "croatia": "HR",
    "curacao": "CW", "cyprus": "CY", "czech republic": "CZ",
    "denmark": "DK", "dominican republic": "DO",
    "egypt": "EG", "estonia": "EE", "ethiopia": "ET",
    "finland": "FI", "france": "FR", "georgia": "GE", "germany": "DE",
    "greece": "GR", "guadeloupe": "GP", "hungary": "HU",
    "iceland": "IS", "ireland": "IE", "israel": "IL", "italy": "IT",
    "jamaica": "JM", "japan": "JP", "jordan": "JO",
    "kenya": "KE", "kosovo": "XK", "kuwait": "KW",
    "latvia": "LV", "lebanon": "LB", "lithuania": "LT", "luxembourg": "LU",
    "malaysia": "MY", "malta": "MT", "martinique": "MQ", "mauritius": "MU",
    "mexico": "MX", "montenegro": "ME", "morocco": "MA",
    "namibia": "NA", "netherlands": "NL", "new zealand": "NZ",
    "north macedonia": "MK", "norway": "NO",
    "oman": "OM", "panama": "PA", "peru": "PE", "philippines": "PH",
    "poland": "PL", "portugal": "PT", "qatar": "QA",
    "romania": "RO", "rwanda": "RW",
    "saudi arabia": "SA", "serbia": "RS", "singapore": "SG",
    "slovakia": "SK", "slovenia": "SI", "south africa": "ZA",
    "south korea": "KR", "spain": "ES", "españa": "ES",
    "sri lanka": "LK", "sweden": "SE", "switzerland": "CH",
    "tanzania": "TZ", "thailand": "TH", "trinidad and tobago": "TT",
    "tunisia": "TN", "turkey": "TR", "türkiye": "TR",
    "united arab emirates": "AE", "united kingdom": "GB",
    "united states": "US", "usa": "US", "uruguay": "UY", "uzbekistan": "UZ",
}


def _resolve_country_code(country: str) -> str | None:
    """Resolve country name or code to ISO 2-letter code."""
    if not country:
        return None
    country = country.strip()
    # Already a 2-letter code
    if len(country) == 2 and country.isalpha():
        return country.upper()
    # Look up by name
    return _COUNTRY_NAME_TO_CODE.get(country.lower())

# Reference to app-level circuit breaker registry (set from main.py)
_cb_registry: CircuitBreakerRegistry | None = None

_location_repository = LocationRepository()


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
    pickup_time: str = "09:00"
    dropoff_time: str = "09:00"
    currency: str = "EUR"
    driver_age: int = 30
    dropoff_unified_location_id: int | None = None
    providers: str | None = None
    provider_locations: list[ProviderLocationEntry] | None = None
    country_code: str | None = None


def _parse_time(value: str) -> time:
    parts = value.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


async def _do_search(
    body: VehicleSearchBody,
    db: AsyncSession,
) -> SearchResponse:
    if _cb_registry is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")

    if body.dropoff_date <= body.pickup_date:
        raise HTTPException(status_code=400, detail="Dropoff date must be after pickup date")

    pickup_time = _parse_time(body.pickup_time)
    dropoff_time = _parse_time(body.dropoff_time)

    # If Laravel passed provider_locations from the JSON file, use them directly.
    # Otherwise fall back to Supabase lookup.
    if body.provider_locations:
        provider_entries = [pl.model_dump() for pl in body.provider_locations]
        country_code = body.country_code or None
        logger.info(
            "Using %d provider locations from Laravel (unified_location_id=%s)",
            len(provider_entries),
            body.unified_location_id,
        )
    else:
        location = await _location_repository.get_location_by_unified_id(
            db, body.unified_location_id, include_internal_provider=True,
        )
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
        country_code = _resolve_country_code(location.get("country", ""))

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

    return await search_vehicles(request, provider_entries, cache, _cb_registry)


@router.post("/search", response_model=SearchResponse)
async def vehicle_search_post(
    body: VehicleSearchBody,
    _api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Search vehicles — accepts provider_locations from Laravel's JSON file."""
    return await _do_search(body, db)


@router.get("/search", response_model=SearchResponse)
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
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Search vehicles (GET — backward compatible, uses Supabase lookup)."""
    body = VehicleSearchBody(
        unified_location_id=unified_location_id,
        pickup_date=pickup_date,
        dropoff_date=dropoff_date,
        pickup_time=pickup_time.strftime("%H:%M"),
        dropoff_time=dropoff_time.strftime("%H:%M"),
        currency=currency,
        driver_age=driver_age,
        dropoff_unified_location_id=dropoff_unified_location_id,
        providers=providers,
    )
    return await _do_search(body, db)
