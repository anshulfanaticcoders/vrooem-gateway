"""Booking schemas for create/modify/cancel operations."""

from datetime import date, datetime, time

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import BookingStatus


class DriverInfo(BaseModel):
    """Driver details for a booking."""

    first_name: str
    last_name: str
    email: EmailStr
    phone: str = ""
    age: int = 30
    date_of_birth: str | None = None
    driving_license_number: str | None = None
    driving_license_country: str | None = None
    address: str = ""
    city: str = ""
    country: str = ""
    postal_code: str = ""


class BookingExtra(BaseModel):
    """An extra selected for a booking."""

    extra_id: str
    quantity: int = 1


class CreateBookingRequest(BaseModel):
    """Request to create a booking through the gateway."""

    vehicle_id: str = Field(description="Gateway vehicle ID from search results")
    search_id: str = Field(description="Search ID from which this vehicle was selected")
    driver: DriverInfo
    extras: list[BookingExtra] = Field(default_factory=list)
    insurance_id: str | None = None
    flight_number: str | None = None
    special_requests: str = ""
    pickup_date: date | None = Field(default=None, description="Pickup date (YYYY-MM-DD)")
    pickup_time: str | None = Field(default=None, description="Pickup time (HH:MM)")
    dropoff_date: date | None = Field(default=None, description="Dropoff date (YYYY-MM-DD)")
    dropoff_time: str | None = Field(default=None, description="Dropoff time (HH:MM)")
    laravel_booking_id: int | None = Field(
        default=None, description="Laravel's booking ID for cross-reference"
    )
    laravel_booking_number: str | None = Field(
        default=None, description="Laravel's booking number (e.g. BK2026035725) for voucher reference"
    )


class BookingResponse(BaseModel):
    """Response after creating/modifying a booking."""

    id: str = Field(description="Gateway booking ID (bk_...)")
    supplier_id: str
    supplier_booking_id: str = Field(description="Provider's confirmation/reference number")
    status: BookingStatus
    vehicle_name: str = ""
    pickup_datetime: datetime | None = None
    dropoff_datetime: datetime | None = None
    pickup_location: str = ""
    dropoff_location: str = ""
    total_price: float = 0
    currency: str = "EUR"
    cancellation_policy: str = ""
    supplier_data: dict = Field(
        default_factory=dict, description="Raw supplier response for Laravel storage"
    )
    created_at: datetime | None = None


class CancelBookingRequest(BaseModel):
    """Request to cancel a booking."""

    reason: str = ""


class CancelBookingResponse(BaseModel):
    """Response after cancelling a booking."""

    id: str
    status: BookingStatus
    cancellation_fee: float = 0
    cancellation_currency: str = "EUR"
    refund_amount: float = 0
    supplier_cancellation_id: str = ""
