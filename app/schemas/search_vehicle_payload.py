"""API-facing search vehicle payload schemas."""

from typing import Any

from pydantic import BaseModel, Field


class SearchVehicleSpecsPayload(BaseModel):
    transmission: str | None = None
    fuel: str | None = None
    seating_capacity: int | None = None
    doors: int | None = None
    luggage_small: int | None = None
    luggage_medium: int | None = None
    luggage_large: int | None = None
    air_conditioning: bool | None = None
    sipp_code: str | None = None


class SearchVehiclePricingPayload(BaseModel):
    currency: str
    price_per_day: float
    total_price: float
    deposit_amount: float | None = None
    deposit_currency: str | None = None
    excess_amount: float | None = None
    excess_theft_amount: float | None = None


class SearchVehiclePoliciesPayload(BaseModel):
    mileage_policy: str | None = None
    mileage_limit_km: int | None = None
    fuel_policy: str | None = None
    cancellation: dict[str, Any] | None = None


class SearchVehicleLocationPointPayload(BaseModel):
    provider_location_id: str | None = None
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class SearchVehicleLocationPayload(BaseModel):
    pickup: SearchVehicleLocationPointPayload
    dropoff: SearchVehicleLocationPointPayload


class SearchVehicleUiPlaceholdersPayload(BaseModel):
    image: bool = False


class SearchVehicleBookingContextPayload(BaseModel):
    version: int = 1
    provider_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Temporary opaque booking compatibility payload; not for search-card rendering.",
    )


class SearchVehiclePayload(BaseModel):
    id: str
    gateway_vehicle_id: str | None = None
    provider_vehicle_id: str | None = None
    provider_product_id: str | None = None
    provider_rate_id: str | None = None
    source: str
    provider_code: str
    display_name: str
    availability_status: str | None = None
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    image: str | None = None
    specs: SearchVehicleSpecsPayload
    pricing: SearchVehiclePricingPayload
    policies: SearchVehiclePoliciesPayload
    products: list[dict[str, Any]] = Field(default_factory=list)
    extras_preview: list[dict[str, Any]] = Field(default_factory=list)
    location: SearchVehicleLocationPayload
    data_quality_flags: list[str] = Field(default_factory=list)
    pricing_transparency_flags: list[str] = Field(default_factory=list)
    ui_placeholders: SearchVehicleUiPlaceholdersPayload
    booking_context: SearchVehicleBookingContextPayload


class SearchVehicleSupplierResultPayload(BaseModel):
    supplier_id: str
    vehicle_count: int = 0
    response_time_ms: int = 0
    error: str | None = None
    from_cache: bool = False


class SearchVehicleProviderStatusPayload(BaseModel):
    provider: str
    stage: str
    failure_type: str
    http_status: int | None = None
    provider_code: str | None = None
    message: str
    retryable: bool = False
    raw_excerpt: str | None = None


class SearchVehicleResponsePayload(BaseModel):
    search_id: str
    vehicles: list[SearchVehiclePayload] = Field(default_factory=list)
    total_vehicles: int = 0
    suppliers_queried: int = 0
    suppliers_responded: int = 0
    supplier_results: list[SearchVehicleSupplierResultPayload] = Field(default_factory=list)
    provider_status: list[SearchVehicleProviderStatusPayload] = Field(default_factory=list)
    from_cache: bool = False
    response_time_ms: int = 0
