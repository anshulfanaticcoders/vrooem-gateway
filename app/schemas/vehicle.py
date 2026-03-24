"""Vehicle schemas — the canonical format all providers normalize to."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import (
    CoverageType,
    ExtraType,
    FuelType,
    MileagePolicy,
    TransmissionType,
    VehicleCategory,
)
from app.schemas.pricing import Pricing
from app.schemas.sipp import apply_sipp_specs


class InsuranceOption(BaseModel):
    """An available insurance/coverage option."""

    id: str
    coverage_type: CoverageType = CoverageType.BASIC
    name: str
    daily_rate: float = Field(ge=0)
    total_price: float = Field(ge=0)
    currency: str = "EUR"
    excess_amount: float | None = Field(
        default=None, description="Remaining excess after this coverage"
    )
    included: bool = Field(default=False, description="Already included in base price")
    description: str = ""


class Extra(BaseModel):
    """An available rental extra (child seat, GPS, etc.)."""

    id: str
    name: str
    daily_rate: float = Field(ge=0)
    total_price: float = Field(ge=0)
    currency: str = "EUR"
    max_quantity: int = 1
    type: ExtraType = ExtraType.EQUIPMENT
    mandatory: bool = False
    description: str | None = ""
    supplier_data: dict = Field(
        default_factory=dict,
        description="Raw supplier-specific extra data (passed through to frontend)",
    )


class CancellationPolicy(BaseModel):
    """Cancellation terms for this vehicle offer."""

    free_cancellation: bool = True
    free_cancellation_until: datetime | None = None
    cancellation_fee: float | None = None
    cancellation_fee_currency: str = "EUR"
    description: str = ""


class VehicleLocation(BaseModel):
    """Simplified location info attached to a vehicle result."""

    id: str = ""
    supplier_location_id: str = ""
    name: str = ""
    city: str = ""
    country_code: str = ""
    latitude: float | None = None
    longitude: float | None = None
    location_type: str = "other"
    airport_code: str | None = None


class Vehicle(BaseModel):
    """Canonical vehicle — every provider's response normalizes to this."""

    id: str = Field(description="Gateway vehicle ID (gw_<uuid>)")
    supplier_id: str = Field(description="Provider identifier (e.g. green_motion)")
    supplier_vehicle_id: str = Field(description="Provider's own vehicle/offer ID")
    name: str = Field(description="Display name (e.g. Toyota Aygo or similar)")
    category: VehicleCategory = VehicleCategory.OTHER
    make: str = ""
    model: str = ""
    image_url: str = ""
    transmission: TransmissionType | None = None
    fuel_type: FuelType | None = None
    seats: int | None = None
    doors: int | None = None
    bags_large: int | None = None
    bags_small: int | None = None
    air_conditioning: bool | None = None
    mileage_policy: MileagePolicy | None = None
    mileage_limit_km: int | None = None
    sipp_code: str | None = None
    is_available: bool = True

    pickup_location: VehicleLocation = Field(default_factory=VehicleLocation)
    dropoff_location: VehicleLocation | None = None

    pricing: Pricing
    insurance_options: list[InsuranceOption] = Field(default_factory=list)
    extras: list[Extra] = Field(default_factory=list)
    cancellation_policy: CancellationPolicy | None = None

    # Metadata for booking
    supplier_data: dict = Field(
        default_factory=dict,
        description="Raw supplier-specific data needed for booking (opaque to Laravel)",
    )
    min_driver_age: int | None = None
    max_driver_age: int | None = None

    @model_validator(mode="after")
    def _validate_specs_against_sipp(self) -> "Vehicle":
        """Use SIPP code as source of truth for specs.

        SIPP is an industry standard — more reliable than individual provider APIs.
        - Fills missing transmission/fuel/AC from SIPP when adapter didn't set them.
        - Overrides transmission/fuel/AC with SIPP values (SIPP is authoritative).
        - Validates door count against SIPP body type, drops if contradictory.
        """
        if not self.sipp_code:
            return self
        validated = apply_sipp_specs(
            self.sipp_code,
            transmission=self.transmission,
            fuel_type=self.fuel_type,
            air_conditioning=self.air_conditioning,
            doors=self.doors,
            seats=self.seats,
        )
        self.transmission = validated["transmission"]
        self.fuel_type = validated["fuel_type"]
        self.air_conditioning = validated["air_conditioning"]
        self.doors = validated["doors"]
        self.seats = validated["seats"]
        return self
