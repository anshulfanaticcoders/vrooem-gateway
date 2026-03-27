"""Search request/response schemas."""

from datetime import date, time

from pydantic import BaseModel, Field

from app.schemas.vehicle import Vehicle


class SearchRequest(BaseModel):
    """Vehicle search parameters."""

    unified_location_id: int | None = Field(
        default=None, description="Gateway unified location ID"
    )
    pickup_latitude: float | None = None
    pickup_longitude: float | None = None
    pickup_date: date
    pickup_time: time = time(9, 0)
    dropoff_date: date
    dropoff_time: time = time(9, 0)
    dropoff_unified_location_id: int | None = Field(
        default=None, description="If different from pickup (one-way)"
    )
    currency: str = "EUR"
    driver_age: int = 30
    country_code: str | None = None
    providers: list[str] | None = Field(
        default=None, description="Filter to specific providers, or None for all"
    )


class SupplierResult(BaseModel):
    """Result from a single supplier in a search."""

    supplier_id: str
    vehicles: list[Vehicle] = Field(default_factory=list)
    vehicle_count: int = 0
    response_time_ms: int = 0
    error: str | None = None
    from_cache: bool = False


class ProviderFailure(BaseModel):
    """Structured provider failure surfaced to API consumers."""

    provider: str
    stage: str = "vehicle_search"
    failure_type: str
    http_status: int | None = None
    provider_code: str | None = None
    message: str
    retryable: bool = False
    raw_excerpt: str | None = None


class SearchResponse(BaseModel):
    """Aggregated search response from all suppliers."""

    search_id: str = Field(description="Unique search ID for caching/reference")
    vehicles: list[Vehicle] = Field(default_factory=list)
    total_vehicles: int = 0
    suppliers_queried: int = 0
    suppliers_responded: int = 0
    supplier_results: list[SupplierResult] = Field(default_factory=list)
    provider_status: list[ProviderFailure] = Field(default_factory=list)
    from_cache: bool = False
    response_time_ms: int = 0
