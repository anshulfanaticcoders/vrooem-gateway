"""Location schemas for unified location system."""

from pydantic import BaseModel, Field

from app.schemas.common import LocationType


class ProviderLocationEntry(BaseModel):
    """A single provider's presence at a unified location."""

    model_config = {"extra": "allow"}

    provider: str
    pickup_id: str
    original_name: str | None = ""
    dropoffs: list = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    supports_one_way: bool = False
    extended_location_code: str | None = None
    extended_dropoff_code: str | None = None
    country_code: str | None = None
    iata: str | None = None
    provider_code: str | None = None


class Location(BaseModel):
    """Canonical location returned by the gateway."""

    id: str = Field(description="Gateway location ID (loc_...)")
    unified_location_id: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    city: str
    country: str
    country_code: str = ""
    latitude: float
    longitude: float
    location_type: LocationType = LocationType.OTHER
    iata: str | None = None
    providers: list[ProviderLocationEntry] = Field(default_factory=list)
    provider_count: int = 0
    our_location_id: str | None = Field(
        default=None, description="Internal vehicle location ID if applicable"
    )


class LocationSearchResult(BaseModel):
    """A location result with relevance score."""

    location: Location
    score: float = Field(ge=0, le=100, description="Relevance score 0-100")
    match_type: str = Field(description="What matched: iata, name, city, alias, etc.")


class LocationSearchResponse(BaseModel):
    """Response from location search endpoint."""

    query: str
    results: list[LocationSearchResult]
    total: int
