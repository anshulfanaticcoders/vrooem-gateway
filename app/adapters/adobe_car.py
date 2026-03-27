"""Adobe Car adapter — REST JSON with Bearer Token auth.

Quirks:
- Very short token TTL (55 seconds) — cached in-memory with timestamp
- Non-standard HTTPS port (42800)
- Parameter names must be lowercase concatenated: pickupOffice, returnOffice,
  startDate, endDate (for availability); dates as "YYYY-MM-DD HH:MM"
- Costa Rica only (16 offices)
- Pricing in USD: tdr = total daily rate (includes PLI + LDW + SPP + base)
- Vehicle category is a single letter (n, b, c, d, e, ...)
- Auth endpoint: POST /Auth/Login with userName/password → { token: "..." }
- SSL verification disabled (custom port, self-signed cert)
"""

import logging
import time
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
    CoverageType,
    ExtraType,
    FuelType,
    MileagePolicy,
    PaymentOption,
    TransmissionType,
    VehicleCategory,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Fee, Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    Extra,
    InsuranceOption,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)

# Token cache: (token_string, expiry_timestamp)
_token_cache: dict[str, tuple[str, float]] = {}

# Adobe vehicle type → canonical VehicleCategory
_TYPE_MAP: dict[str, VehicleCategory] = {
    "sedan": VehicleCategory.COMPACT,
    "suv": VehicleCategory.SUV,
    "minivan": VehicleCategory.VAN,
    "van": VehicleCategory.VAN,
    "pickup": VehicleCategory.SUV,
}

# Adobe category letter → approximate VehicleCategory (order-based sizing)
_CATEGORY_ORDER_MAP: dict[int, VehicleCategory] = {
    1: VehicleCategory.MINI,
    2: VehicleCategory.ECONOMY,
    3: VehicleCategory.ECONOMY,
    4: VehicleCategory.COMPACT,
    5: VehicleCategory.INTERMEDIATE,
    6: VehicleCategory.STANDARD,
    7: VehicleCategory.FULLSIZE,
    8: VehicleCategory.PREMIUM,
}


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _map_vehicle_category(raw: dict) -> VehicleCategory:
    """Derive category from Adobe type field and order field."""
    vtype = (raw.get("type") or "").lower()
    if vtype in _TYPE_MAP:
        mapped = _TYPE_MAP[vtype]
        # Refine SUV/sedan by order (higher order = larger vehicle)
        if mapped == VehicleCategory.COMPACT:
            order = _safe_int(raw.get("order"), 0)
            return _CATEGORY_ORDER_MAP.get(order, VehicleCategory.COMPACT)
        return mapped
    # Fallback to order-based mapping
    order = _safe_int(raw.get("order"), 0)
    return _CATEGORY_ORDER_MAP.get(order, VehicleCategory.OTHER)


@register_adapter
class AdobeCarAdapter(BaseAdapter):
    supplier_id = "adobe_car"
    supplier_name = "Adobe Car"
    supports_one_way = True  # drop-off fee (dro) field supports one-way
    default_timeout = 30.0

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        # SSL verification disabled — Adobe uses a custom port with self-signed cert
        self.http_client = http_client or httpx.AsyncClient(
            timeout=self.default_timeout, verify=False
        )

    async def _get_token(self) -> str:
        """Authenticate and return a bearer token, using in-memory cache."""
        settings = get_settings()
        cache_key = f"adobe_{settings.adobe_username}"

        # Check cache (55s TTL with 5s safety margin)
        cached = _token_cache.get(cache_key)
        if cached:
            token, expiry = cached
            if time.time() < expiry:
                return token

        base_url = settings.adobe_api_url.rstrip("/")
        response = await self._request(
            "POST",
            f"{base_url}/Auth/Login",
            json={
                "userName": settings.adobe_username,
                "password": settings.adobe_password,
            },
        )

        data = response.json()
        token = data.get("token") or data.get("Token", "")
        if not token:
            raise ValueError(f"Adobe Car auth failed: {data}")

        # Cache with 50s TTL (55s actual minus 5s safety margin)
        _token_cache[cache_key] = (token, time.time() + 50)
        return token

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ─── Search ───

    async def _fetch_category_extras(
        self,
        base_url: str,
        token: str,
        pickup_code: str,
        dropoff_code: str,
        category: str,
        start_date: str,
        end_date: str,
        rental_days: int,
    ) -> tuple[list[Extra], list[InsuranceOption]]:
        """Call GetCategoryWithFare to get extras (Adicionales) and protections for a vehicle category."""
        settings = get_settings()
        params = {
            "pickupOffice": pickup_code,
            "returnOffice": dropoff_code,
            "category": category,
            "startDate": start_date,
            "endDate": end_date,
            "customerCode": settings.adobe_username,
            "idioma": "en",
        }

        try:
            response = await self._request(
                "GET",
                f"{base_url}/Client/GetCategoryWithFare",
                params=params,
                headers=self._auth_headers(token),
            )
            data = response.json()
        except Exception as exc:
            logger.warning("[adobe_car] GetCategoryWithFare failed for category %s: %s", category, exc)
            return [], []

        items = []
        if isinstance(data, dict):
            items = data.get("items") or []
        elif isinstance(data, list):
            items = data

        extras: list[Extra] = []
        protections: list[InsuranceOption] = []

        for item in items:
            item_type = (item.get("type") or "").strip()
            code = item.get("code") or ""
            name = item.get("name") or code or "Unknown"
            description = item.get("description") or ""
            total = _safe_float(item.get("total"))
            included = bool(item.get("included", False))
            required = bool(item.get("required", False))
            daily_rate = round(total / rental_days, 2) if rental_days > 0 else total

            if item_type in ("Proteccion", "protection"):
                coverage = CoverageType.BASIC
                code_upper = code.upper()
                if "LDW" in code_upper:
                    coverage = CoverageType.STANDARD
                elif "SPP" in code_upper:
                    coverage = CoverageType.FULL

                protections.append(InsuranceOption(
                    id=f"ins_{self.supplier_id}_{code}",
                    coverage_type=coverage,
                    name=name,
                    daily_rate=daily_rate,
                    total_price=total,
                    currency="USD",
                    included=included,
                    description=description,
                ))
            elif item_type == "Adicionales":
                extras.append(Extra(
                    id=f"ext_{self.supplier_id}_{code}",
                    name=name,
                    daily_rate=daily_rate,
                    total_price=total,
                    currency="USD",
                    max_quantity=1,
                    type=ExtraType.EQUIPMENT,
                    mandatory=required,
                    description=description,
                    supplier_data={
                        "code": code,
                        "included": included,
                        "required": required,
                    },
                ))

        return extras, protections

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        base_url = settings.adobe_api_url.rstrip("/")
        token = await self._get_token()

        pickup_code = pickup_entry.pickup_id
        dropoff_code = dropoff_entry.pickup_id if dropoff_entry else pickup_code

        # Adobe requires "YYYY-MM-DD HH:MM" format (date + time combined)
        start_date = f"{request.pickup_date.isoformat()} {request.pickup_time.strftime('%H:%M')}"
        end_date = f"{request.dropoff_date.isoformat()} {request.dropoff_time.strftime('%H:%M')}"

        params = {
            "pickupOffice": pickup_code,
            "returnOffice": dropoff_code,
            "startDate": start_date,
            "endDate": end_date,
            "customerCode": settings.adobe_username,
        }

        response = await self._request(
            "GET",
            f"{base_url}/Client/GetAvailabilityWithPrice",
            params=params,
            headers=self._auth_headers(token),
        )

        data = response.json()

        # Response can be a list directly or {"result": true, "data": [...]}
        vehicles_raw: list[dict] = []
        if isinstance(data, list):
            vehicles_raw = data
        elif isinstance(data, dict):
            if data.get("result") and isinstance(data.get("data"), list):
                vehicles_raw = data["data"]
            elif isinstance(data.get("data"), list):
                vehicles_raw = data["data"]

        if not vehicles_raw:
            return []

        rental_days = (request.dropoff_date - request.pickup_date).days or 1

        # Fetch extras per unique category (avoids duplicate API calls)
        unique_categories = {raw.get("category", "") for raw in vehicles_raw if raw.get("category")}
        category_extras: dict[str, tuple[list[Extra], list[InsuranceOption]]] = {}
        for cat in unique_categories:
            category_extras[cat] = await self._fetch_category_extras(
                base_url, token, pickup_code, dropoff_code, cat, start_date, end_date, rental_days,
            )

        return [
            v
            for raw in vehicles_raw
            if (v := self._parse_vehicle(raw, request, rental_days, pickup_entry, dropoff_entry, category_extras)) is not None
        ]

    def _parse_vehicle(
        self,
        raw: dict,
        request: SearchRequest,
        rental_days: int,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
        category_extras: dict | None = None,
    ) -> Vehicle | None:
        category_code = raw.get("category", "")
        model_name = raw.get("model", "")
        if not model_name:
            return None

        # ─── Pricing ───
        # tdr = total daily rate (includes all mandatory insurance)
        tdr = _safe_float(raw.get("tdr"))
        pli = _safe_float(raw.get("pli"))  # Personal Liability Insurance per day
        ldw = _safe_float(raw.get("ldw"))  # Loss Damage Waiver per day
        spp = _safe_float(raw.get("spp"))  # Supplemental Protection Plan per day
        dro = _safe_float(raw.get("dro"))  # Drop-off fee (one-way surcharge)

        daily_rate = tdr
        total_price = round(tdr * rental_days + dro, 2)

        # ─── Vehicle info ───
        passengers_raw = raw.get("passengers")
        doors_raw = raw.get("doors")
        manual_raw = raw.get("manual")
        transmission = None
        if isinstance(manual_raw, bool):
            transmission = TransmissionType.MANUAL if manual_raw else TransmissionType.AUTOMATIC
        photo = raw.get("photo", "")
        traction = (raw.get("traction") or "").upper()

        # Parse make/model from "Kia Picanto AT or similar"
        name_clean = model_name.split(" or similar")[0].strip()
        name_parts = name_clean.split(" ", 1)
        make = name_parts[0].title() if name_parts else ""
        model = name_parts[1] if len(name_parts) > 1 else ""

        # Category mapping
        vehicle_category = _map_vehicle_category(raw)

        # Pickup location
        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_entry.original_name,
            country_code="CR",  # Costa Rica only
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
            location_type="airport" if pickup_entry.pickup_id in ("OCO", "LIB") else "other",
        )

        # Dropoff location (if one-way)
        dropoff_loc = None
        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            dropoff_loc = VehicleLocation(
                supplier_location_id=dropoff_entry.pickup_id,
                name=dropoff_entry.original_name,
                country_code="CR",
                latitude=dropoff_entry.latitude,
                longitude=dropoff_entry.longitude,
                location_type="airport" if dropoff_entry.pickup_id in ("OCO", "LIB") else "other",
            )

        # ─── Extras and protections from GetCategoryWithFare ───
        extras: list[Extra] = []
        insurance_options: list[InsuranceOption] = []
        if category_extras and category_code in category_extras:
            extras, insurance_options = category_extras[category_code]

        # If GetCategoryWithFare didn't return protections, fall back to availability fields
        if not insurance_options:
            if pli > 0:
                insurance_options.append(
                    InsuranceOption(
                        id=f"ins_{self.supplier_id}_pli",
                        coverage_type=CoverageType.BASIC,
                        name="Personal Liability Insurance (PLI)",
                        daily_rate=pli,
                        total_price=round(pli * rental_days, 2),
                        currency="USD",
                        included=True,
                        description="Mandatory personal liability coverage included in the rate.",
                    )
                )

            if ldw > 0:
                insurance_options.append(
                    InsuranceOption(
                        id=f"ins_{self.supplier_id}_ldw",
                        coverage_type=CoverageType.STANDARD,
                        name="Loss Damage Waiver (LDW)",
                        daily_rate=ldw,
                        total_price=round(ldw * rental_days, 2),
                        currency="USD",
                        included=True,
                        description="Mandatory loss/damage waiver included in the rate.",
                    )
                )

            if spp > 0:
                insurance_options.append(
                    InsuranceOption(
                        id=f"ins_{self.supplier_id}_spp",
                        coverage_type=CoverageType.FULL,
                        name="Supplemental Protection Plan (SPP)",
                        daily_rate=spp,
                        total_price=round(spp * rental_days, 2),
                        currency="USD",
                        included=True,
                        description="Mandatory supplemental protection included in the rate.",
                    )
                )

        # ─── Fees ───
        fees: list[Fee] = []
        if dro > 0:
            fees.append(
                Fee(
                    name="Drop-off Fee",
                    amount=dro,
                    currency="USD",
                    included_in_total=True,
                    description="One-way rental surcharge.",
                )
            )

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": category_code,
            "provider_product_id": category_code or None,
            "availability_status": "available",
            "name": model_name,
            "category": vehicle_category,
            "make": make,
            "model": model,
            "image_url": photo,
            "pickup_location": pickup_loc,
            "dropoff_location": dropoff_loc,
            "pricing": Pricing(
                currency="USD",
                total_price=total_price,
                daily_rate=daily_rate,
                fees=fees,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
            ),
            "extras": extras,
            "insurance_options": insurance_options,
            "cancellation_policy": None,  # API does not return cancellation terms
            "supplier_data": {
                "category": category_code,
                "tdr": tdr,
                "pli": pli,
                "ldw": ldw,
                "spp": spp,
                "dro": dro,
                "traction": traction,
                "order": raw.get("order"),
                "vehicle_type": raw.get("type"),
                "pickup_office": pickup_entry.pickup_id,
                "return_office": (dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id),
                "pickup_datetime": f"{request.pickup_date.isoformat()} {request.pickup_time.strftime('%H:%M')}",
                "dropoff_datetime": f"{request.dropoff_date.isoformat()} {request.dropoff_time.strftime('%H:%M')}",
            },
        }

        if transmission is not None:
            vehicle_kwargs["transmission"] = transmission
        if passengers_raw is not None:
            vehicle_kwargs["seats"] = _safe_int(passengers_raw)
        if doors_raw is not None:
            vehicle_kwargs["doors"] = _safe_int(doors_raw)

        return Vehicle(**vehicle_kwargs)

    # ─── Booking ───

    async def create_booking(
        self, request: CreateBookingRequest, vehicle: Vehicle
    ) -> BookingResponse:
        settings = get_settings()
        base_url = settings.adobe_api_url.rstrip("/")
        token = await self._get_token()
        sd = vehicle.supplier_data

        payload = {
            "pickupOffice": sd.get("pickup_office", ""),
            "returnOffice": sd.get("return_office", sd.get("pickup_office", "")),
            "pickupDate": sd.get("pickup_datetime", ""),
            "returnDate": sd.get("dropoff_datetime", ""),
            "category": sd.get("category", ""),
            "customerCode": settings.adobe_username,
            "customerName": f"{request.driver.first_name} {request.driver.last_name}",
            "flightNumber": request.flight_number or "",
            "comment": request.special_requests or "",
        }

        response = await self._request(
            "POST",
            f"{base_url}/Booking",
            json=payload,
            headers=self._auth_headers(token),
        )

        data = response.json()
        booking_number = ""
        if isinstance(data, dict):
            if data.get("result") and isinstance(data.get("data"), dict):
                booking_number = str(data["data"].get("bookingNumber", ""))
            elif data.get("bookingNumber"):
                booking_number = str(data["bookingNumber"])

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_number,
            status=BookingStatus.CONFIRMED if booking_number else BookingStatus.FAILED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=data if isinstance(data, dict) else {"raw": str(data)},
        )

    # ─── Cancel ───

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        """Cancel an Adobe Car booking.

        Note: Adobe does not expose a dedicated cancellation endpoint in the
        tested API surface.  We retrieve the booking first to confirm it exists,
        then return a cancelled status.  If a real cancel endpoint is discovered
        later, it should be called here.
        """
        settings = get_settings()
        base_url = settings.adobe_api_url.rstrip("/")
        token = await self._get_token()

        # Attempt to retrieve booking to verify it exists
        await self._request(
            "GET",
            f"{base_url}/Booking",
            params={
                "bookingNumber": supplier_booking_id,
                "customerCode": settings.adobe_username,
            },
            headers=self._auth_headers(token),
        )

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
        )

    # ─── Locations ───

    async def get_locations(self) -> list[dict]:
        settings = get_settings()
        base_url = settings.adobe_api_url.rstrip("/")
        token = await self._get_token()

        response = await self._request(
            "GET",
            f"{base_url}/Offices",
            headers=self._auth_headers(token),
        )

        data = response.json()

        # Response can be a list directly or {"data": [...]}
        offices: list[dict] = []
        if isinstance(data, list):
            offices = data
        elif isinstance(data, dict) and isinstance(data.get("data"), list):
            offices = data["data"]

        if not offices:
            return []

        locations = []
        for office in offices:
            # Skip invisible offices
            if office.get("visible") is False:
                continue

            code = office.get("code", "")
            name = office.get("name", "")
            if not code:
                continue

            # Parse coordinates: ["lat", "lng"]
            coords = office.get("coordinates") or []
            lat, lng = None, None
            if len(coords) >= 2:
                try:
                    lat = float(coords[0])
                    lng = float(coords[1])
                except (ValueError, TypeError):
                    pass

            # Detect location type
            is_airport = office.get("atAirport", False)
            deployment = office.get("deploymentName", "")

            # Extract IATA code from deploymentName (e.g., "SJO - Juan Santamaria...")
            iata = None
            if is_airport and deployment:
                iata_part = deployment.split(" - ")[0].strip()
                if len(iata_part) == 3 and iata_part.isalpha():
                    iata = iata_part.upper()

            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": code,
                "name": name,
                "country_code": "CR",  # Costa Rica only
                "latitude": lat,
                "longitude": lng,
                "location_type": "airport" if is_airport else "other",
                "iata": iata,
                "region": office.get("region"),
                "address": office.get("address"),
                "schedule": office.get("schedule"),
                "telephones": office.get("telephones"),
            })

        return locations
