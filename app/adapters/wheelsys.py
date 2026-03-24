"""Wheelsys adapter — REST JSON over HTTPS GET with query-param auth."""

import logging
import uuid

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
    PaymentOption,
    TransmissionType,
    category_from_sipp,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    Extra,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

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


def _parse_transmission_from_sipp(sipp: str) -> TransmissionType | None:
    """3rd char of SIPP: A/B/D = automatic, M/N/C = manual. Returns None when not deterministic."""
    if len(sipp) >= 3:
        code = sipp[2].upper()
        if code in ("A", "B", "D"):
            return TransmissionType.AUTOMATIC
        if code in ("M", "N", "C"):
            return TransmissionType.MANUAL
    return None


def _parse_fuel_from_sipp(sipp: str) -> FuelType | None:
    """4th char of SIPP encodes fuel/AC info."""
    if len(sipp) < 4:
        return None
    ch = sipp[3].upper()
    if ch in ("D", "Q"):
        return FuelType.DIESEL
    if ch in ("H", "I"):
        return FuelType.HYBRID
    if ch in ("E", "C"):
        return FuelType.ELECTRIC
    if ch in ("R", "N"):
        return FuelType.PETROL
    return None


def _has_ac_from_sipp(sipp: str) -> bool | None:
    """4th char: R/D/H/E/L/A/M/V/U denote air conditioning. Returns None when < 4 chars."""
    if len(sipp) < 4:
        return None
    return sipp[3].upper() in ("R", "D", "H", "E", "L", "A", "M", "V", "U")


_FUEL_POLICY_MAP: dict[str, str] = {
    "SL": "Same Level",
    "FF": "Full to Full",
    "FE": "Full to Empty",
    "FP": "Free Tank",
}

# Map Wheelsys option codes to ExtraType
_OPTION_TYPE_MAP: dict[str, ExtraType] = {
    "EI": ExtraType.INSURANCE,
    "CDW": ExtraType.INSURANCE,
    "PCDW": ExtraType.INSURANCE,
    "SLI": ExtraType.INSURANCE,
    "PPI": ExtraType.INSURANCE,
    "LF": ExtraType.INSURANCE,
    "TRV": ExtraType.INSURANCE,
    "TRVA": ExtraType.INSURANCE,
    "YD": ExtraType.FEE,
    "SC": ExtraType.FEE,
    "UM": ExtraType.FEE,
    "APD": ExtraType.FEE,
    "OS": ExtraType.FEE,
}

# Human-readable option names by code
_OPTION_NAMES: dict[str, str] = {
    "EI": "Excess Insurance",
    "CDW": "Collision Damage Waiver",
    "PCDW": "Premium Collision Damage Waiver",
    "BBS": "Baby Booster Seat",
    "SLI": "Supplemental Liability Insurance",
    "GPS": "GPS Navigation",
    "ADD": "Additional Driver",
    "TOL": "Toll Pass",
    "YD": "Young Driver Surcharge",
    "APD": "Airport Parking",
    "OS": "Off-Site Airport",
    "UM": "Underage Driver",
    "RS": "Roadside Service",
    "PPI": "Personal Protection Insurance",
    "LF": "Loss Damage Waiver",
    "UPG": "Vehicle Upgrade",
    "SD": "Sports Equipment",
    "TRV": "Travel Insurance",
    "TRVA": "Travel Insurance Plus",
    "WT": "Winter Tires",
    "DR": "Driver Report",
    "PGAS": "Pre-Paid Gas",
    "SC": "Service Charge",
}


# ──────────────────────────────────────────────
# Adapter
# ──────────────────────────────────────────────

@register_adapter
class WheelsysAdapter(BaseAdapter):
    supplier_id = "wheelsys"
    supplier_name = "Wheelsys"
    supports_one_way = False  # single-station provider
    default_timeout = 30.0

    # ─── Auth params appended to every request ───

    def _auth_params(self) -> dict[str, str]:
        settings = get_settings()
        return {
            "agent": settings.wheelsys_agent_code,
            "format": "json",
        }

    def _base_url(self) -> str:
        settings = get_settings()
        return settings.wheelsys_api_url.rstrip("/")

    def _link_code(self) -> str:
        return get_settings().wheelsys_link_code

    # ─── Search ───

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        base_url = self._base_url()
        link_code = self._link_code()

        pickup_code = pickup_entry.pickup_id
        dropoff_code = dropoff_entry.pickup_id if dropoff_entry else pickup_code

        # Wheelsys requires DD/MM/YYYY date format
        pickup_date_str = request.pickup_date.strftime("%d/%m/%Y")
        dropoff_date_str = request.dropoff_date.strftime("%d/%m/%Y")
        pickup_time_str = request.pickup_time.strftime("%H:%M")
        dropoff_time_str = request.dropoff_time.strftime("%H:%M")

        params = {
            **self._auth_params(),
            "DATE_FROM": pickup_date_str,
            "TIME_FROM": pickup_time_str,
            "DATE_TO": dropoff_date_str,
            "TIME_TO": dropoff_time_str,
            "PICKUP_STATION": pickup_code,
            "RETURN_STATION": dropoff_code,
        }

        url = f"{base_url}/price-quote_{link_code}.html"
        response = await self._request("GET", url, params=params)
        data = response.json()

        if not isinstance(data, dict):
            logger.warning("[wheelsys] Unexpected response type: %s", type(data).__name__)
            return []

        # Check for stop-sale or other errors
        errors = data.get("Errors") or []
        if errors:
            codes = [e.get("Code", "") for e in errors]
            messages = [e.get("Value", "") for e in errors]
            logger.info("[wheelsys] API errors: %s — %s", codes, messages)
            # Stop sale is a normal business condition, not a failure
            return []

        rates = data.get("Rates") or []
        if not rates:
            return []

        # Quote metadata used for booking later
        quote_id = data.get("Id", "")
        currency = data.get("CurrencyCode", "USD")
        fuel_policy = data.get("FuelPolicy", "")
        tax_inclusive = data.get("TaxInclusive", True)
        rental_days = _safe_int(data.get("Duration"), 1) or 1

        vehicles: list[Vehicle] = []
        for rate in rates:
            v = self._parse_vehicle(
                rate=rate,
                quote_id=quote_id,
                currency=currency,
                fuel_policy=fuel_policy,
                tax_inclusive=tax_inclusive,
                rental_days=rental_days,
                pickup_entry=pickup_entry,
                request=request,
            )
            if v is not None:
                vehicles.append(v)

        return vehicles

    def _parse_vehicle(
        self,
        rate: dict,
        quote_id: str,
        currency: str,
        fuel_policy: str,
        tax_inclusive: bool,
        rental_days: int,
        pickup_entry: ProviderLocationEntry,
        request: SearchRequest,
    ) -> Vehicle | None:
        group_code = rate.get("GroupCode", "")
        if not group_code:
            return None

        # Model / name
        sample_model = rate.get("SampleModel") or rate.get("Name") or "Vehicle"
        name = sample_model if "or similar" in sample_model.lower() else f"{sample_model} or Similar"

        # Extract make/model
        name_parts = sample_model.replace(" or Similar", "").replace(" or similar", "").split(" ", 1)
        make = name_parts[0].title() if name_parts else ""
        model = name_parts[1].title() if len(name_parts) > 1 else ""

        # SIPP code — prefer Acriss field, fallback to GroupCode
        sipp = rate.get("Acriss") or rate.get("SippCode") or group_code
        # Validate SIPP is 4 uppercase letters
        if not (len(sipp) == 4 and sipp.isalpha() and sipp.isupper()):
            sipp = group_code if (len(group_code) == 4 and group_code.isalpha() and group_code.isupper()) else ""

        # Prices are in CENTS — divide by 100
        total_rate_cents = _safe_float(rate.get("TotalRate"))
        total_price = round(total_rate_cents / 100, 2)
        daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else total_price

        # Image
        image_url = rate.get("ImageUrl") or ""

        # Passengers / doors — None when API doesn't provide them
        pax_raw = rate.get("Pax")
        seats = _safe_int(pax_raw) if pax_raw is not None else None
        doors_raw = rate.get("Doors")
        doors = _safe_int(doors_raw) if doors_raw is not None else None
        bags_large = _safe_int(rate.get("Suitcases"))
        bags_small = _safe_int(rate.get("Bags"))

        # Mileage
        is_unlimited = rate.get("Unlimited", False)
        included_km = _safe_int(rate.get("IncKlm")) if not is_unlimited else None
        mileage_policy = MileagePolicy.UNLIMITED if is_unlimited else MileagePolicy.LIMITED

        # Availability flag
        availability_str = (rate.get("Availability") or "").upper()
        is_available = availability_str == "AVAILABLE"

        # Pickup location
        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_entry.original_name,
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
            location_type="airport",
        )

        # Extras / options
        extras = self._parse_options(rate.get("Options") or [], rental_days, currency)

        # Age limits
        max_age = _safe_int(rate.get("AgeMaxLimit"))
        max_driver_age = max_age if max_age and max_age < 99 else None

        vehicle_kwargs: dict = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": group_code,
            "name": name,
            "category": category_from_sipp(sipp) if sipp else category_from_sipp(group_code),
            "make": make,
            "model": model,
            "image_url": image_url,
            "seats": seats,
            "doors": doors,
            "bags_large": bags_large,
            "bags_small": bags_small,
            "mileage_policy": mileage_policy,
            "mileage_limit_km": included_km,
            "sipp_code": sipp or None,
            "is_available": is_available,
            "pickup_location": pickup_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=total_price,
                daily_rate=daily_rate,
                price_includes_tax=tax_inclusive,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
            ),
            "extras": extras,
            "cancellation_policy": None,
            "supplier_data": {
                "quote_id": quote_id,
                "group_code": group_code,
                "sipp_code": sipp,
                "fuel_policy": fuel_policy,
                "fuel_policy_label": _FUEL_POLICY_MAP.get(fuel_policy, fuel_policy),
                "tax_inclusive": tax_inclusive,
                "total_rate_cents": int(total_rate_cents),
                "rental_days": rental_days,
                "pickup_station": pickup_entry.pickup_id,
                "return_station": pickup_entry.pickup_id,
                "pickup_date": request.pickup_date.strftime("%d/%m/%Y"),
                "pickup_time": request.pickup_time.strftime("%H:%M"),
                "dropoff_date": request.dropoff_date.strftime("%d/%m/%Y"),
                "dropoff_time": request.dropoff_time.strftime("%H:%M"),
                "raw_rate": rate,
            },
            "min_driver_age": None,
            "max_driver_age": max_driver_age,
        }

        if sipp:
            transmission = _parse_transmission_from_sipp(sipp)
            fuel_type = _parse_fuel_from_sipp(sipp)
            ac = _has_ac_from_sipp(sipp)
            if transmission is not None:
                vehicle_kwargs["transmission"] = transmission
            if fuel_type is not None:
                vehicle_kwargs["fuel_type"] = fuel_type
            if ac is not None:
                vehicle_kwargs["air_conditioning"] = ac

        return Vehicle(**vehicle_kwargs)

    def _parse_options(
        self, options: list[dict], rental_days: int, currency: str
    ) -> list[Extra]:
        """Convert Wheelsys option objects to canonical Extra list."""
        extras: list[Extra] = []
        for opt in options:
            code = opt.get("Code", "")
            if not code:
                continue

            # Prices also in cents
            rate_cents = _safe_float(opt.get("Rate"))
            rate_amount = round(rate_cents / 100, 2)

            charge_type = (opt.get("ChargeType") or "").lower()
            is_mandatory = opt.get("Mandatory", False)

            # Determine daily vs total based on charge type
            if charge_type in ("per_rental", "once", "total"):
                daily_rate = round(rate_amount / rental_days, 2) if rental_days > 0 else rate_amount
                total_price = rate_amount
            else:
                # per-day or unknown — treat as daily
                daily_rate = rate_amount
                total_price = round(rate_amount * rental_days, 2)

            name = _OPTION_NAMES.get(code, code)
            extra_type = _OPTION_TYPE_MAP.get(code, ExtraType.EQUIPMENT)

            extras.append(Extra(
                id=f"ext_{self.supplier_id}_{code}",
                name=name,
                daily_rate=daily_rate,
                total_price=total_price,
                currency=currency,
                max_quantity=_safe_int(opt.get("MaxQuantity"), 1),
                type=extra_type,
                mandatory=is_mandatory,
                description=None,
            ))

        return extras

    # ─── Create Booking ───

    async def create_booking(
        self, request: CreateBookingRequest, vehicle: Vehicle
    ) -> BookingResponse:
        base_url = self._base_url()
        link_code = self._link_code()
        sd = vehicle.supplier_data

        url = f"{base_url}/new-res_{link_code}.html"

        params = {
            **self._auth_params(),
            "quoteref": sd.get("quote_id", ""),
            "group": sd.get("group_code", ""),
            "DATE_FROM": sd.get("pickup_date", ""),
            "TIME_FROM": sd.get("pickup_time", ""),
            "DATE_TO": sd.get("dropoff_date", ""),
            "TIME_TO": sd.get("dropoff_time", ""),
            "PICKUP_STATION": sd.get("pickup_station", ""),
            "RETURN_STATION": sd.get("return_station", ""),
            "first_name": request.driver.first_name,
            "last_name": request.driver.last_name,
            "email": request.driver.email,
            "phone": request.driver.phone,
        }

        # Include flight number if provided
        if request.flight_number:
            params["flight_number"] = request.flight_number

        # Include selected extras
        if request.extras:
            extras_codes = []
            for be in request.extras:
                # extra_id format is "ext_wheelsys_CODE" — extract the CODE part
                parts = be.extra_id.split("_", 2)
                code = parts[2] if len(parts) > 2 else be.extra_id
                for _ in range(be.quantity):
                    extras_codes.append(code)
            if extras_codes:
                params["options"] = ",".join(extras_codes)

        response = await self._request("POST", url, params=params)
        data = response.json()

        if not isinstance(data, dict):
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"error": "Invalid response from Wheelsys"},
            )

        # Wheelsys returns 'irn' as the booking/reservation reference
        booking_ref = str(data.get("irn", ""))
        has_errors = bool(data.get("Errors"))

        if has_errors or not booking_ref:
            error_msgs = [e.get("Value", "") for e in (data.get("Errors") or [])]
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id=booking_ref,
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"errors": error_msgs, "raw": data},
            )

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_ref,
            status=BookingStatus.CONFIRMED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=data,
        )

    # ─── Cancel Booking ───

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        base_url = self._base_url()
        link_code = self._link_code()

        url = f"{base_url}/cancel-res_{link_code}.html"
        params = {
            **self._auth_params(),
            "irn": supplier_booking_id,
        }

        response = await self._request("GET", url, params=params)
        data = response.json()

        # Check for cancellation errors
        errors = (data.get("Errors") or []) if isinstance(data, dict) else []
        if errors:
            error_msgs = [e.get("Value", "") for e in errors]
            logger.warning(
                "[wheelsys] Cancellation errors for %s: %s",
                supplier_booking_id,
                error_msgs,
            )

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
        )

    # ─── Locations ───

    async def get_locations(self) -> list[dict]:
        base_url = self._base_url()
        link_code = self._link_code()

        url = f"{base_url}/stations_{link_code}.html"
        response = await self._request("GET", url, params=self._auth_params())
        data = response.json()

        if not isinstance(data, dict):
            return []

        stations = data.get("Stations") or []
        locations: list[dict] = []

        for station in stations:
            info = station.get("StationInformation") or {}
            is_active = info.get("Active", True)
            if not is_active:
                continue

            code = station.get("Code", "")
            name = station.get("Name", "")
            country = station.get("Country", "")
            lat = _safe_float(station.get("Lat"))
            lng = _safe_float(station.get("Long"))

            # Determine location type from station type
            station_type = (info.get("StationType") or "").lower()
            if "airport" in name.lower() or "airport" in station_type:
                location_type = "airport"
            elif "port" in station_type:
                location_type = "port"
            elif "train" in station_type or "rail" in station_type:
                location_type = "train_station"
            else:
                location_type = "other"

            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": code,
                "name": name,
                "country_code": country,
                "location_type": location_type,
                "latitude": lat,
                "longitude": lng,
                "city": info.get("City", ""),
                "address": info.get("Address", ""),
                "postal_code": info.get("ZipCode", ""),
                "phone": info.get("Phone", ""),
            })

        return locations
