"""Favrica adapter — REST JSON GET with query params auth.

Quirks:
- HTTP (not HTTPS), SSL verification disabled
- Comma-decimal prices ("254,78" → 254.78)
- Non-standard currency codes: "EURO" (not EUR), "TL" (not TRY)
- All response fields are strings
- String booleans ("True"/"False")
"""

import logging
import uuid

import httpx

logger = logging.getLogger(__name__)

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
    ExtraType,
    FuelType,
    MileagePolicy,
    TransmissionType,
    category_from_sipp,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import Extra, Vehicle, VehicleLocation

# Currency code mapping: standard → Favrica
CURRENCY_TO_FAVRICA = {"EUR": "EURO", "TRY": "TL"}
# Reverse: Favrica → standard
CURRENCY_FROM_FAVRICA = {"EURO": "EUR", "TL": "TRY", "EUR": "EUR"}

# Fuel type mapping: Turkish → enum
FUEL_MAP = {
    "benzin": FuelType.PETROL,
    "dizel": FuelType.DIESEL,
    "diesel": FuelType.DIESEL,
    "hybrid": FuelType.HYBRID,
    "elektrik": FuelType.ELECTRIC,
    "lpg": FuelType.LPG,
}

# Category mapping: Turkish group_str → English
CATEGORY_MAP = {
    "ekonomik": "economy",
    "orta": "compact",
    "suv": "suv",
    "lux": "luxury",
    "premium": "premium",
    "ticari": "van",
    "ust": "fullsize",
}

IMAGE_BASE_URL = "https://www.favricarental.com/Files/img/Car-Images"


def _parse_comma_decimal(value: str, default: float = 0.0) -> float:
    """Parse comma-decimal string: '254,78' → 254.78."""
    if not value:
        return default
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return default


def _safe_int_str(value: str, default: int = 0) -> int:
    """Parse string integer."""
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@register_adapter
class FavricaAdapter(BaseAdapter):
    supplier_id = "favrica"
    supplier_name = "Favrica"
    supports_one_way = False
    default_timeout = 30.0

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        # SSL verification disabled + browser User-Agent (Turev platform blocks python-httpx)
        self.http_client = http_client or httpx.AsyncClient(
            timeout=self.default_timeout,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )

    def _base_params(self) -> dict:
        """Params for unauthenticated endpoints (locations, groups)."""
        settings = get_settings()
        return {
            "Key_Hack": settings.favrica_token,
        }

    def _auth_params(self) -> dict:
        """Params for authenticated endpoints (search, booking, cancel)."""
        settings = get_settings()
        return {
            "Key_Hack": settings.favrica_token,
            "User_Name": settings.favrica_username,
            "User_Pass": settings.favrica_password,
        }

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        base_url = settings.favrica_api_url.rstrip("/")

        # Map currency to Favrica format
        favrica_currency = CURRENCY_TO_FAVRICA.get(request.currency, "EURO")

        params = {
            **self._auth_params(),
            "Pickup_ID": pickup_entry.pickup_id,
            "Drop_Off_ID": dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id,
            "Pickup_Day": str(request.pickup_date.day).zfill(2),
            "Pickup_Month": str(request.pickup_date.month).zfill(2),
            "Pickup_Year": str(request.pickup_date.year),
            "Pickup_Hour": request.pickup_time.strftime("%H"),
            "Pickup_Min": request.pickup_time.strftime("%M"),
            "Drop_Off_Day": str(request.dropoff_date.day).zfill(2),
            "Drop_Off_Month": str(request.dropoff_date.month).zfill(2),
            "Drop_Off_Year": str(request.dropoff_date.year),
            "Drop_Off_Hour": request.dropoff_time.strftime("%H"),
            "Drop_Off_Min": request.dropoff_time.strftime("%M"),
            "Currency": favrica_currency,
        }

        response = await self._request("GET", f"{base_url}/JsonRez.aspx", params=params)
        data = response.json()

        # Check for error response (dict with success=False)
        if isinstance(data, dict):
            success = str(data.get("success", "")).lower()
            if success == "false":
                error_msg = data.get("error", "Unknown error")
                logger.warning("[favrica] API error: %s", error_msg)
                return []

        # USD fallback: retry with USD if initial currency returns empty (same as XDrive/PHP)
        if (not isinstance(data, list) or len(data) == 0) and favrica_currency != "USD":
            logger.info("[favrica] Empty response for %s, retrying with USD", favrica_currency)
            params["Currency"] = "USD"
            response = await self._request("GET", f"{base_url}/JsonRez.aspx", params=params)
            data = response.json()

        if not isinstance(data, list):
            logger.warning("[favrica] Response is not a list: %s", str(data)[:300])
            return []

        logger.info("[favrica] Got %d raw vehicles from API", len(data))
        rental_days = (request.dropoff_date - request.pickup_date).days or 1
        return [
            v for raw in data
            if (v := self._parse_vehicle(raw, request, rental_days, pickup_entry)) is not None
        ]

    def _parse_vehicle(
        self,
        raw: dict,
        request: SearchRequest,
        rental_days: int,
        pickup_entry: ProviderLocationEntry,
    ) -> Vehicle | None:
        rez_id = raw.get("rez_id", "")
        if not rez_id:
            return None

        brand = (raw.get("brand") or "").title()
        model = (raw.get("type") or "").title()
        name = f"{brand} {model}".strip() or raw.get("car_name", "")

        # Parse comma-decimal prices
        total_price = _parse_comma_decimal(raw.get("total_rental", ""))
        daily_rate = _parse_comma_decimal(raw.get("daily_rental", ""))

        # Map currency from Favrica format
        raw_currency = raw.get("currency_symbol") or raw.get("currency", "EURO")
        currency = CURRENCY_FROM_FAVRICA.get(raw_currency, raw_currency)

        sipp = raw.get("sipp", "")

        # Transmission: Turkish
        transmission_str = (raw.get("transmission") or "").lower()
        transmission = TransmissionType.AUTOMATIC if "otomatik" in transmission_str else TransmissionType.MANUAL

        # Fuel type: Turkish
        fuel_str = (raw.get("fuel") or "").lower()
        fuel_type = FUEL_MAP.get(fuel_str, FuelType.UNKNOWN)

        # Image URL
        image_path = raw.get("image_path", "")
        image_url = f"{IMAGE_BASE_URL}/{image_path}" if image_path else ""

        # Mileage
        km_limit = _safe_int_str(raw.get("km_limit", ""))
        mileage_policy = MileagePolicy.LIMITED if km_limit > 0 else MileagePolicy.UNLIMITED

        # Deposit
        deposit = _parse_comma_decimal(raw.get("provision", ""))

        # Parse extras/services
        extras = self._parse_services(raw.get("Services") or [], rental_days)

        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_entry.original_name,
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
        )

        return Vehicle(
            id=f"gw_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_vehicle_id=rez_id,
            name=name,
            category=category_from_sipp(sipp),
            make=brand,
            model=model,
            image_url=image_url,
            transmission=transmission,
            fuel_type=fuel_type,
            seats=_safe_int_str(raw.get("chairs", ""), 5),
            doors=4,
            bags_large=_safe_int_str(raw.get("big_bags", "")),
            bags_small=_safe_int_str(raw.get("small_bags", "")),
            air_conditioning=True,
            mileage_policy=mileage_policy,
            mileage_limit_km=km_limit if km_limit > 0 else None,
            sipp_code=sipp or None,
            pickup_location=pickup_loc,
            pricing=Pricing(
                currency=currency,
                total_price=total_price,
                daily_rate=daily_rate,
                deposit_amount=deposit if deposit > 0 else None,
                deposit_currency=currency if deposit > 0 else None,
            ),
            extras=extras,
            cancellation_policy=None,  # API does not return cancellation terms
            supplier_data={
                "rez_id": rez_id,
                "cars_park_id": raw.get("cars_park_id"),
                "group_id": raw.get("group_id"),
                "reservation_source": raw.get("reservation_source"),
                "reservation_source_id": raw.get("reservation_source_id"),
                "drop_fee": raw.get("drop", "0"),
                "drop_fee_raw": raw.get("drop", "0,00"),
                "provision": raw.get("provision", "0"),
                "car_exemption": raw.get("car_exemption", "0"),
                "pickup_station_id": pickup_entry.pickup_id,
                "dropoff_station_id": pickup_entry.pickup_id,
                "pickup_date": request.pickup_date.isoformat(),
                "pickup_time": request.pickup_time.strftime("%H:%M"),
                "dropoff_date": request.dropoff_date.isoformat(),
                "dropoff_time": request.dropoff_time.strftime("%H:%M"),
            },
            min_driver_age=_safe_int_str(raw.get("driver_age", "")) or None,
        )

    def _parse_services(self, services: list[dict], rental_days: int) -> list[Extra]:
        extras = []
        for svc in services:
            svc_name = svc.get("service_name", "")
            title = svc.get("service_title", "")
            total_str = svc.get("service_total_price", "")

            if not svc_name:
                continue

            total_price = _parse_comma_decimal(total_str)
            daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else total_price

            # Map service type
            extra_type = ExtraType.EQUIPMENT
            if svc_name in ("LCF", "SCDW", "CDW"):
                extra_type = ExtraType.INSURANCE
            elif svc_name == "Addition_Drive":
                extra_type = ExtraType.FEE

            extras.append(Extra(
                id=f"ext_{self.supplier_id}_{svc_name}",
                name=title or svc_name,
                daily_rate=daily_rate,
                total_price=total_price,
                max_quantity=1,
                type=extra_type,
            ))
        return extras

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        settings = get_settings()
        base_url = settings.favrica_api_url.rstrip("/")
        sd = vehicle.supplier_data

        favrica_currency = CURRENCY_TO_FAVRICA.get(vehicle.pricing.currency, "EURO")

        # Build extras flags
        extras_flags = {
            "Baby_Seat": "OFF", "Navigation": "OFF", "Additional_Driver": "OFF",
            "CDW": "OFF", "SCDW": "OFF", "LCF": "OFF", "PAI": "OFF",
        }
        for extra in request.extras:
            raw_name = extra.extra_id.replace(f"ext_{self.supplier_id}_", "")
            if raw_name in extras_flags:
                extras_flags[raw_name] = "ON"

        # Format amounts with comma-decimal (Turev format)
        rent_price = str(vehicle.pricing.total_price).replace(".", ",")
        extra_price = "0,00"

        params = {
            **self._auth_params(),
            "Pickup_ID": sd.get("pickup_station_id", ""),
            "Drop_Off_ID": sd.get("dropoff_station_id", sd.get("pickup_station_id", "")),
            "Pickup_Day": sd.get("pickup_date", "")[8:10],
            "Pickup_Month": sd.get("pickup_date", "")[5:7],
            "Pickup_Year": sd.get("pickup_date", "")[:4],
            "Pickup_Hour": sd.get("pickup_time", "")[:2],
            "Pickup_Min": sd.get("pickup_time", "")[3:5],
            "Drop_Off_Day": sd.get("dropoff_date", "")[8:10],
            "Drop_Off_Month": sd.get("dropoff_date", "")[5:7],
            "Drop_Off_Year": sd.get("dropoff_date", "")[:4],
            "Drop_Off_Hour": sd.get("dropoff_time", "")[:2],
            "Drop_Off_Min": sd.get("dropoff_time", "")[3:5],
            "Currency": favrica_currency,
            "Rez_ID": sd.get("rez_id", ""),
            "Cars_Park_ID": sd.get("cars_park_id", ""),
            "Group_ID": sd.get("group_id", ""),
            "Name": request.driver.first_name,
            "SurName": request.driver.last_name,
            "MobilePhone": request.driver.phone,
            "Mail_Adress": request.driver.email,
            "Flight_Number": request.flight_number or "",
            **{k: v for k, v in extras_flags.items()},
            "Your_Rez_ID": request.laravel_booking_id or "",
            "Your_Rent_Price": rent_price,
            "Your_Extra_Price": extra_price,
            "Your_Drop_Price": sd.get("drop_fee_raw", "0,00"),
            "Payment_Type": "1",
        }

        response = await self._request("GET", f"{base_url}/JsonRez_Save.aspx", params=params)
        data = response.json()

        booking_ref = ""
        if isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            booking_ref = str(first.get("rez_id", first.get("Rez_ID", "")))
        elif isinstance(data, dict):
            booking_ref = str(data.get("booking_ref", data.get("rez_id", "")))

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_ref or sd.get("rez_id", ""),
            status=BookingStatus.CONFIRMED if booking_ref else BookingStatus.PENDING,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=data if isinstance(data, dict) else {"raw": str(data)},
        )

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()
        base_url = settings.favrica_api_url.rstrip("/")

        params = {
            **self._auth_params(),
            "Rez_ID": supplier_booking_id,
        }

        await self._request("GET", f"{base_url}/JsonCancel.aspx", params=params)

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
        )

    async def get_locations(self) -> list[dict]:
        settings = get_settings()
        base_url = settings.favrica_api_url.rstrip("/")

        response = await self._request(
            "GET", f"{base_url}/JsonLocations.aspx", params=self._base_params()
        )
        data = response.json()

        if not isinstance(data, list):
            return []

        locations = []
        for loc in data:
            # Parse maps_point: "36.909530, 30.798137"
            maps_point = loc.get("maps_point", "")
            lat, lng = None, None
            if maps_point and "," in maps_point:
                parts = maps_point.split(",", 1)
                try:
                    lat = float(parts[0].strip())
                    lng = float(parts[1].strip())
                except ValueError:
                    pass

            # Detect location type
            is_airport = loc.get("isairport", "").lower() == "true"
            iata = loc.get("iata", "")

            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": loc.get("location_id", ""),
                "name": loc.get("location_name", ""),
                "country_code": loc.get("country", ""),
                "latitude": lat,
                "longitude": lng,
                "location_type": "airport" if is_airport else "other",
                "iata": iata if iata else None,
            })

        return locations
