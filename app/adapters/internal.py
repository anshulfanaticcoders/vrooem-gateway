"""Internal adapter — platform's own fleet via Laravel REST API."""

import hashlib
import logging
import uuid

import httpx

from app.adapters.base import BaseAdapter
from app.adapters.registry import register_adapter
from app.core.config import get_settings
from app.schemas.booking import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.schemas.common import (
    BookingStatus,
    FuelType,
    MileagePolicy,
    PaymentOption,
    TransmissionType,
    VehicleCategory,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    CancellationPolicy,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _build_internal_location_id(raw: dict) -> str:
    payload = ''.join([
        str(raw.get('city') or ''),
        str(raw.get('state') or ''),
        str(raw.get('country') or ''),
        str(raw.get('location') or ''),
    ])
    return 'internal_' + hashlib.md5(payload.encode('utf-8')).hexdigest()


# Map Laravel fuel strings to canonical FuelType
_FUEL_MAP: dict[str, FuelType] = {
    "diesel": FuelType.DIESEL,
    "petrol": FuelType.PETROL,
    "electric": FuelType.ELECTRIC,
    "hybrid": FuelType.HYBRID,
    "lpg": FuelType.LPG,
}

# Map Laravel category_id to VehicleCategory (best-effort)
_CATEGORY_MAP: dict[int, VehicleCategory] = {
    1: VehicleCategory.MINI,
    2: VehicleCategory.ECONOMY,
    3: VehicleCategory.COMPACT,
    4: VehicleCategory.INTERMEDIATE,
    5: VehicleCategory.VAN,
    6: VehicleCategory.SUV,
    7: VehicleCategory.PREMIUM,
    8: VehicleCategory.LUXURY,
}


def _parse_transmission(value: str | None) -> TransmissionType | None:
    if not value:
        return None
    if value.lower() == "automatic":
        return TransmissionType.AUTOMATIC
    if value.lower() == "manual":
        return TransmissionType.MANUAL
    return None


def _parse_fuel(value: str | None) -> FuelType | None:
    if not value:
        return None
    return _FUEL_MAP.get(value.lower()) or None


def _parse_category(category_id: int | None) -> VehicleCategory:
    if category_id is None:
        return VehicleCategory.OTHER
    return _CATEGORY_MAP.get(category_id, VehicleCategory.OTHER)


def _extract_image_url(raw: dict) -> str:
    """Extract the primary image URL from the vehicle's images relation."""
    images = raw.get("images")
    if not images or not isinstance(images, list):
        return ""
    # Prefer primary image, fall back to first
    for img in images:
        if isinstance(img, dict) and (img.get("type") == "primary" or img.get("image_type") == "primary"):
            return img.get("url") or img.get("image_url") or img.get("path", "")
    # No primary found — use first image
    first = images[0]
    if isinstance(first, dict):
        return first.get("url") or first.get("image_url") or first.get("path", "")
    if isinstance(first, str):
        return first
    return ""



@register_adapter
class InternalAdapter(BaseAdapter):
    supplier_id = "internal"
    supplier_name = "Internal"
    supports_one_way = False
    default_timeout = 15.0

    def _auth_headers(self) -> dict[str, str]:
        settings = get_settings()
        return {
            "Authorization": f"Bearer {settings.laravel_api_token}",
            "X-Gateway-Token": settings.laravel_api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        base_url = settings.laravel_base_url.rstrip("/")

        params = {
            "location_id": pickup_entry.pickup_id,
            "pickup_date": request.pickup_date.isoformat(),
            "dropoff_date": request.dropoff_date.isoformat(),
            "pickup_time": request.pickup_time.strftime("%H:%M"),
            "dropoff_time": request.dropoff_time.strftime("%H:%M"),
        }
        if request.driver_age:
            params["driver_age"] = str(request.driver_age)

        try:
            response = await self._request(
                "GET",
                f"{base_url}/api/internal/vehicles",
                params=params,
                headers=self._auth_headers(),
            )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.warning("[internal] Laravel API unreachable: %s", exc)
            return []

        if response.status_code != 200:
            logger.warning(
                "[internal] Unexpected status %d from Laravel vehicle search",
                response.status_code,
            )
            return []

        data = response.json()

        # Laravel may wrap results in a "data" key (Resource/Paginator)
        vehicles_list = data.get("data") if isinstance(data, dict) else data
        if not isinstance(vehicles_list, list):
            return []

        rental_days = max((request.dropoff_date - request.pickup_date).days, 1)
        results: list[Vehicle] = []
        for raw in vehicles_list:
            vehicle = self._parse_vehicle(raw, request, rental_days, pickup_entry)
            if vehicle is not None:
                results.append(vehicle)
        return results

    def _parse_vehicle(
        self,
        raw: dict,
        request: SearchRequest,
        rental_days: int,
        pickup_entry: ProviderLocationEntry,
    ) -> Vehicle | None:
        """Map a Laravel vehicle dict to canonical Vehicle."""
        vehicle_id = raw.get("id")
        if vehicle_id is None:
            return None

        # Brand / model
        brand = raw.get("brand", "") or ""
        model = raw.get("model", "") or ""
        name = f"{brand} {model}".strip() or f"Vehicle {vehicle_id}"

        # Pricing: prefer total_price from API, else calculate from per_day
        price_per_day = _safe_float(raw.get("price_per_day"))
        price_per_week = _safe_float(raw.get("price_per_week"))
        price_per_month = _safe_float(raw.get("price_per_month"))

        # Use the API-calculated total if present, otherwise derive from daily
        total_price = _safe_float(raw.get("total_price"))
        if total_price <= 0:
            # Smart calculation: use weekly/monthly rates when beneficial
            if rental_days >= 30 and price_per_month > 0:
                months = rental_days // 30
                remaining_days = rental_days % 30
                total_price = (months * price_per_month) + (remaining_days * price_per_day)
            elif rental_days >= 7 and price_per_week > 0:
                weeks = rental_days // 7
                remaining_days = rental_days % 7
                total_price = (weeks * price_per_week) + (remaining_days * price_per_day)
            else:
                total_price = price_per_day * rental_days

        daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else price_per_day

        # Location from vehicle data
        location_str = raw.get("location", "")
        vendor = raw.get("vendor") or {}
        profile = vendor.get("profile") or {}

        pickup_loc = VehicleLocation(
            supplier_location_id=str(vehicle_id),
            name=location_str or pickup_entry.original_name,
            city=profile.get("city", ""),
            country_code=profile.get("country_code", "BE"),
            latitude=_safe_float(raw.get("latitude")) or None,
            longitude=_safe_float(raw.get("longitude")) or None,
            location_type="other",
        )

        # Security deposit
        deposit = _safe_float(raw.get("security_deposit"))

        # Benefits (mileage, cancellation, driver age)
        benefits = raw.get("benefits") or {}
        km_per_day = _safe_int(benefits.get("km_per_day"))
        km_per_month = _safe_int(benefits.get("km_per_month"))
        min_driver_age = _safe_int(benefits.get("min_driver_age")) or None
        cancellation_text = benefits.get("cancellation", "")

        # Mileage policy
        if (km_per_day or 0) > 0 or (km_per_month or 0) > 0:
            mileage_policy = MileagePolicy.LIMITED
            mileage_limit_km = km_per_day * rental_days if (km_per_day or 0) > 0 else km_per_month
        else:
            mileage_policy = MileagePolicy.UNLIMITED
            mileage_limit_km = None

        # Cancellation
        has_free_cancel = bool(cancellation_text)
        cancellation_policy = CancellationPolicy(
            free_cancellation=has_free_cancel,
            description=cancellation_text,
        )

        # Features as a list
        features = raw.get("features") or []
        if isinstance(features, str):
            # Handle JSON-encoded string
            import json
            try:
                features = json.loads(features)
            except (json.JSONDecodeError, TypeError):
                features = []

        transmission = _parse_transmission(raw.get("transmission"))
        fuel_type = _parse_fuel(raw.get("fuel"))

        vehicle_kwargs: dict = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": str(vehicle_id),
            "provider_product_id": str(vehicle_id),
            "availability_status": "available",
            "name": name,
            "category": _parse_category(raw.get("category_id")),
            "make": brand.title(),
            "model": model.title(),
            "image_url": _extract_image_url(raw),
            "seats": _safe_int(raw.get("seating_capacity")),
            "doors": _safe_int(raw.get("doors")),
            "air_conditioning": (True if ("Air Conditioning" in features or "AC" in features) else None) if features else None,
            "mileage_policy": mileage_policy,
            "mileage_limit_km": mileage_limit_km,
            "pickup_location": pickup_loc,
            "pricing": Pricing(
                currency=request.currency,
                total_price=round(total_price, 2),
                daily_rate=daily_rate,
                deposit_amount=deposit if deposit > 0 else None,
                deposit_currency=request.currency,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
            ),
            "cancellation_policy": cancellation_policy,
            "supplier_data": {
                "laravel_vehicle_id": vehicle_id,
                "vendor_id": raw.get("vendor_id"),
                "category_id": raw.get("category_id"),
                "price_per_day": price_per_day,
                "price_per_week": price_per_week,
                "price_per_month": price_per_month,
                "security_deposit": deposit,
                "features": features,
                "horsepower": raw.get("horsepower"),
                "co2": raw.get("co2"),
                "km_per_day": km_per_day,
                "price_per_extra_km": _safe_float(benefits.get("price_per_extra_km")),
                "location": location_str,
                "vendor": raw.get("vendor") or {},
                "vendorProfileData": raw.get("vendorProfileData") or raw.get("vendor_profile_data") or {},
                "vendor_profile_data": raw.get("vendor_profile_data") or raw.get("vendorProfileData") or {},
                "images": raw.get("images") or [],
            },
            "min_driver_age": min_driver_age,
        }

        if transmission is not None:
            vehicle_kwargs["transmission"] = transmission
        if fuel_type is not None:
            vehicle_kwargs["fuel_type"] = fuel_type

        return Vehicle(**vehicle_kwargs)

    async def create_booking(
        self, request: CreateBookingRequest, vehicle: Vehicle
    ) -> BookingResponse:
        settings = get_settings()
        base_url = settings.laravel_base_url.rstrip("/")
        sd = vehicle.supplier_data

        payload = {
            "vehicle_id": sd.get("laravel_vehicle_id"),
            "first_name": request.driver.first_name,
            "last_name": request.driver.last_name,
            "email": request.driver.email,
            "phone": request.driver.phone,
            "date_of_birth": request.driver.date_of_birth,
            "driving_license_number": request.driver.driving_license_number,
            "address": request.driver.address,
            "city": request.driver.city,
            "country": request.driver.country,
            "postal_code": request.driver.postal_code,
            "flight_number": request.flight_number or "",
            "special_requests": request.special_requests,
            "extras": [
                {"extra_id": e.extra_id, "quantity": e.quantity}
                for e in request.extras
            ],
            "insurance_id": request.insurance_id,
            "total_price": vehicle.pricing.total_price,
            "currency": vehicle.pricing.currency,
            "laravel_booking_id": request.laravel_booking_id,
        }

        try:
            response = await self._request(
                "POST",
                f"{base_url}/api/internal/bookings",
                json=payload,
                headers=self._auth_headers(),
            )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.error("[internal] Laravel API unreachable for booking: %s", exc)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"error": str(exc)},
            )

        if response.status_code not in (200, 201):
            logger.error(
                "[internal] Booking creation failed with status %d: %s",
                response.status_code,
                response.text[:500],
            )
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"http_status": response.status_code, "body": response.text[:500]},
            )

        data = response.json()
        result = data.get("data", data) if isinstance(data, dict) else data
        booking_id = str(result.get("id", "")) if isinstance(result, dict) else ""

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_id,
            status=BookingStatus.CONFIRMED if booking_id else BookingStatus.FAILED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=result if isinstance(result, dict) else {},
        )

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()
        base_url = settings.laravel_base_url.rstrip("/")

        try:
            response = await self._request(
                "DELETE",
                f"{base_url}/api/internal/bookings/{supplier_booking_id}",
                headers=self._auth_headers(),
                json={"reason": request.reason} if request.reason else None,
            )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.error("[internal] Laravel API unreachable for cancellation: %s", exc)
            return CancelBookingResponse(
                id=supplier_booking_id,
                status=BookingStatus.FAILED,
            )

        if response.status_code not in (200, 204):
            logger.error(
                "[internal] Cancellation failed with status %d",
                response.status_code,
            )
            return CancelBookingResponse(
                id=supplier_booking_id,
                status=BookingStatus.FAILED,
            )

        # Parse refund info if available
        refund_amount = 0.0
        if response.status_code == 200:
            try:
                data = response.json()
                result = data.get("data", data) if isinstance(data, dict) else {}
                refund_amount = _safe_float(result.get("refund_amount")) if isinstance(result, dict) else 0.0
            except Exception:
                pass

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
            refund_amount=refund_amount,
        )

    async def get_locations(self) -> list[dict]:
        settings = get_settings()
        base_url = settings.laravel_base_url.rstrip("/")

        try:
            response = await self._request(
                "GET",
                f"{base_url}/api/internal/locations",
                headers=self._auth_headers(),
            )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.warning("[internal] Laravel API unreachable for locations: %s", exc)
            return []

        if response.status_code != 200:
            logger.warning(
                "[internal] Locations fetch failed with status %d",
                response.status_code,
            )
            return []

        data = response.json()
        locations_list = data.get("data") if isinstance(data, dict) else data
        if not isinstance(locations_list, list):
            return []

        locations = []
        for loc in locations_list:
            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": str(loc.get("id", "")),
                "name": loc.get("name", loc.get("location", "")),
                "city": loc.get("city", ""),
                "country": loc.get("country", ""),
                "country_code": loc.get("country_code", "BE"),
                "latitude": _safe_float(loc.get("latitude")) or None,
                "longitude": _safe_float(loc.get("longitude")) or None,
                "location_type": (loc.get("type") or "other").lower(),
                "our_location_id": _build_internal_location_id(loc),
            })

        return locations
