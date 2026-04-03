"""Schemas for the Provider API (external-facing)."""

from datetime import date, time

from pydantic import BaseModel, EmailStr, Field


class ProviderSearchRequest(BaseModel):
    pickup_location_id: int
    dropoff_location_id: int
    pickup_date: date
    pickup_time: time = time(10, 0)
    dropoff_date: date
    dropoff_time: time = time(10, 0)
    driver_age: int = Field(default=30, ge=18, le=99)
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class ProviderDriverInfo(BaseModel):
    first_name: str = Field(max_length=100)
    last_name: str = Field(max_length=100)
    email: EmailStr
    phone: str = Field(max_length=50)
    age: int = Field(ge=18, le=99)
    driving_license_number: str = Field(max_length=50)
    driving_license_country: str = Field(min_length=2, max_length=2)


class ProviderBookingExtra(BaseModel):
    extra_id: int
    quantity: int = Field(default=1, ge=1)


class ProviderCreateBookingRequest(BaseModel):
    vehicle_id: int
    pickup_date: date
    pickup_time: time = time(10, 0)
    dropoff_date: date
    dropoff_time: time = time(10, 0)
    driver: ProviderDriverInfo
    extras: list[ProviderBookingExtra] = []
    insurance_id: int | None = None
    flight_number: str | None = Field(default=None, max_length=20)
    special_requests: str | None = Field(default=None, max_length=500)


class ProviderCancelBookingRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


# Response schemas for Swagger docs


class ProviderVehiclePricing(BaseModel):
    daily_rate: float
    total_price: float
    currency: str
    total_days: int


class ProviderVehicleVendor(BaseModel):
    name: str
    rating: float | None = None


class ProviderVehicle(BaseModel):
    id: int
    name: str
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    category: str | None = None
    transmission: str | None = None
    fuel_type: str | None = None
    seats: int | None = None
    doors: int | None = None
    bags: int | None = None
    air_conditioning: bool | None = None
    image: str | None = None
    images: list[str] = []
    pricing: ProviderVehiclePricing
    pickup_location: str | None = None
    dropoff_location: str | None = None
    vendor: ProviderVehicleVendor | None = None
    features: list[str] = []
    mileage_policy: str | None = None


class ProviderSearchResponse(BaseModel):
    data: list[ProviderVehicle]
    meta: dict = {}


class ProviderExtraItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    daily_rate: float
    total_price: float | None = None
    currency: str = "EUR"
    type: str | None = None
    max_quantity: int = 1


class ProviderInsuranceOption(BaseModel):
    id: int
    name: str
    description: str | None = None
    coverage_type: str | None = None
    daily_rate: float
    currency: str = "EUR"
    features: list[str] = []


class ProviderExtrasResponse(BaseModel):
    data: dict  # { extras: [...], insurance_options: [...] }


class ProviderDriverResponse(BaseModel):
    first_name: str
    last_name: str
    email: str


class ProviderBookingResponse(BaseModel):
    booking_id: str
    status: str
    vehicle_name: str
    pickup_date: str | None = None
    pickup_time: str | None = None
    dropoff_date: str | None = None
    dropoff_time: str | None = None
    pickup_location: str | None = None
    dropoff_location: str | None = None
    total_days: int | None = None
    total_price: float
    currency: str = "EUR"
    driver: ProviderDriverResponse
    extras: list[dict] = []
    created_at: str | None = None


class ProviderCancelResponse(BaseModel):
    booking_id: str
    status: str
    cancellation_fee: float = 0
    refund_amount: float = 0
    currency: str = "EUR"


class ProviderLocationItem(BaseModel):
    id: int
    name: str
    city: str | None = None
    state: str | None = None
    country: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_type: str = "other"


class ProviderLocationsResponse(BaseModel):
    data: list[ProviderLocationItem]


class ProviderErrorDetail(BaseModel):
    code: str
    message: str
    status: int
    details: dict | None = None


class ProviderErrorResponse(BaseModel):
    error: ProviderErrorDetail
