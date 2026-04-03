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


class ProviderMileagePolicy(BaseModel):
    type: str = "unlimited"  # unlimited or limited
    km_per_day: float | None = None
    price_per_extra_km: float | None = None


class ProviderCancellationPolicy(BaseModel):
    free_cancellation: bool = False
    cancel_before_days: int = 0
    cancellation_fee: float = 0


class ProviderOperatingHour(BaseModel):
    day: int  # 0=Sunday, 1=Monday, etc.
    is_open: bool = True
    open_time: str | None = None
    close_time: str | None = None


class ProviderInsurancePlan(BaseModel):
    id: int
    name: str
    daily_rate: float
    total_price: float | None = None
    description: str | None = None
    features: list[str] = []


class ProviderExtraOption(BaseModel):
    id: int
    name: str
    type: str | None = None
    daily_rate: float
    total_price: float | None = None
    description: str | None = None
    max_quantity: int = 1


class ProviderVehicle(BaseModel):
    id: int
    name: str
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    category: str | None = None
    color: str | None = None
    transmission: str | None = None
    fuel_type: str | None = None
    fuel_policy: str | None = None
    seats: int | None = None
    doors: int | None = None
    bags: int | None = None
    air_conditioning: bool | None = None
    image: str | None = None
    images: list[str] = []
    # Pricing
    daily_rate: float | None = None
    total_price: float | None = None
    currency: str | None = None
    total_days: int | None = None
    security_deposit: float | None = None
    # Location
    pickup_location: str | None = None
    dropoff_location: str | None = None
    location_type: str | None = None
    location_phone: str | None = None
    pickup_instructions: str | None = None
    dropoff_instructions: str | None = None
    # Vendor
    vendor_name: str | None = None
    # Policies
    features: list[str] = []
    mileage_policy: ProviderMileagePolicy | None = None
    cancellation_policy: ProviderCancellationPolicy | None = None
    minimum_driver_age: int | None = None
    operating_hours: list[ProviderOperatingHour] = []
    payment_methods: list[str] = []
    # Plans & extras
    insurance_plans: list[ProviderInsurancePlan] = []
    extras: list[ProviderExtraOption] = []
    # Terms
    guidelines: str | None = None
    terms_policy: str | None = None


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
    booking_number: str
    status: str
    vehicle_name: str | None = None
    vehicle_id: int | None = None
    vehicle_image: str | None = None
    pickup_date: str | None = None
    pickup_time: str | None = None
    dropoff_date: str | None = None
    dropoff_time: str | None = None
    pickup_location: str | None = None
    return_location: str | None = None
    total_days: int | None = None
    daily_rate: float | None = None
    base_price: float | None = None
    extras_total: float | None = None
    total_amount: float | None = None
    currency: str = "EUR"
    driver: ProviderDriverResponse | None = None
    extras: list[dict] = []
    created_at: str | None = None


class ProviderCancelResponse(BaseModel):
    booking_number: str | None = None
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
