"""Click2Rent adapter — REST JSON API for Mauritius car rentals.

API base: https://morisoleil.com/api
Auth: Bearer token via POST /api/session/login
Docs: https://morisoleil.com/api/documentation (Basic Auth)

Key endpoints:
- POST /api/cars/find — search vehicles
- POST /api/cars/extra/find — search extras
- POST /api/customers/create — create customer (required before booking)
- POST /api/bookings/create — create booking
- DELETE /api/bookings/{id}/delete — cancel booking
- GET /api/hire-points — list locations
"""

import logging
import uuid

from app.adapters.base import BaseAdapter
from app.adapters.registry import register_adapter
from app.core.config import get_settings
from app.schemas.booking import (
    BookingResponse,
    BookingStatus,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    Extra,
    ExtraType,
    MileagePolicy,
    Pricing,
    TransmissionType,
    Vehicle,
    VehicleCategory,
    VehicleLocation,
)

logger = logging.getLogger(__name__)

API_BASE = "https://morisoleil.com/api"

# Vehicle class → category mapping
CLASS_MAP = {
    "mini": VehicleCategory.MINI,
    "economy": VehicleCategory.ECONOMY,
    "compact": VehicleCategory.COMPACT,
    "standard": VehicleCategory.STANDARD,
    "fullsize": VehicleCategory.FULLSIZE,
    "premium": VehicleCategory.PREMIUM,
    "luxury": VehicleCategory.LUXURY,
    "suv": VehicleCategory.SUV,
    "van": VehicleCategory.VAN,
}


async def _get_bearer_token() -> str:
    """Authenticate and return a Bearer token."""
    settings = get_settings()
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{API_BASE}/session/login",
            json={
                "email": settings.click2rent_email,
                "password": settings.click2rent_password,
            },
        )
        data = resp.json()
        token = data.get("token", "")
        if not token:
            logger.error("[click2rent] Login failed: %s", data)
        return token


@register_adapter
class Click2RentAdapter(BaseAdapter):
    supplier_id = "click2rent"
    supplier_name = "Click2Rent"
    supports_one_way = True
    default_timeout = 30.0

    _token: str = ""

    async def _ensure_token(self) -> str:
        if not self._token:
            self._token = await _get_bearer_token()
        return self._token

    def _auth_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _api_request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated API request, refreshing token on 401."""
        token = await self._ensure_token()
        url = f"{API_BASE}{path}"
        kwargs.setdefault("headers", {}).update(self._auth_headers(token))

        response = await self._request(method, url, **kwargs)

        # Refresh token on 401
        if response.status_code == 401:
            logger.info("[click2rent] Token expired, re-authenticating...")
            self._token = await _get_bearer_token()
            kwargs["headers"].update(self._auth_headers(self._token))
            response = await self._request(method, url, **kwargs)

        return response.json()

    # Cached hire-point data {hp_id: {country_code, city_code, location_code, ...}}
    _hire_points: dict = {}
    # Cached car details {car_id: {acriss_sipp, power_type, drive_type, ...}}
    _car_details: dict = {}

    async def _ensure_hire_points(self) -> dict:
        """Fetch and cache hire-point metadata (country/city/location codes)."""
        if not self._hire_points:
            data = await self._api_request("GET", "/hire-points?paginate=false")
            for hp in data.get("data", []):
                hp_id = str(hp.get("id", ""))
                country = hp.get("country", {}) or {}
                city = hp.get("city", {}) or {}
                location = hp.get("location", {}) or {}
                self._hire_points[hp_id] = {
                    "country_code": country.get("code", 125),
                    "city_code": city.get("code"),
                    "location_code": location.get("code"),
                    "instruction_1": hp.get("instruction_1", ""),
                    "instruction_2": hp.get("instruction_2", ""),
                    "address": hp.get("address", ""),
                    "phone_number": hp.get("phone_number", ""),
                    "email": hp.get("email", ""),
                }
        return self._hire_points

    async def _ensure_car_details(self) -> dict:
        """Fetch and cache vehicle details (SIPP, fuel, drive type)."""
        if not self._car_details:
            data = await self._api_request("GET", "/cars?paginate=false")
            for car in data.get("data", []):
                cid = car.get("id")
                if cid is None:
                    continue
                power = car.get("power_type", {}) or {}
                drive = car.get("drive_type", {}) or {}
                cls = car.get("class", {}) or {}
                self._car_details[int(cid)] = {
                    "acriss_sipp": car.get("acriss_sipp", ""),
                    "fuel": (power.get("name_en", "") or "").lower(),
                    "drive_type": (drive.get("name_en", "") or "").lower(),
                    "class_name": (cls.get("name_en", "") or "").lower(),
                    "combined_name": car.get("combined_name", ""),
                    "is_exact_car": car.get("is_exact_car", "0"),
                    "years": car.get("years", ""),
                }
        return self._car_details

    # ─── Search ─────────────────────────────────────────────────────────

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        if not settings.click2rent_email:
            return []

        # Fetch hire-point metadata and car details in parallel-ish
        hp_map = await self._ensure_hire_points()
        car_details = await self._ensure_car_details()

        hire_point_id = pickup_entry.pickup_id
        hp_data = hp_map.get(hire_point_id, {})

        country_code = hp_data.get("country_code", 125)
        city_code = hp_data.get("city_code")
        location_code = hp_data.get("location_code")

        if not city_code or not location_code:
            logger.warning("[click2rent] Missing city/location codes for hire point %s", hire_point_id)
            return []

        pickup_dt = f"{request.pickup_date.isoformat()} {request.pickup_time.strftime('%H:%M')}:00"
        dropoff_dt = f"{request.dropoff_date.isoformat()} {request.dropoff_time.strftime('%H:%M')}:00"

        # Dropoff location
        drop_hp_id = dropoff_entry.pickup_id if dropoff_entry else hire_point_id
        drop_hp_data = hp_map.get(drop_hp_id, hp_data)
        drop_country = drop_hp_data.get("country_code", country_code)
        drop_city = drop_hp_data.get("city_code", city_code)
        drop_location = drop_hp_data.get("location_code", location_code)

        payload = {
            "pickup_country": int(country_code),
            "pickup_city": int(city_code),
            "pickup_location": int(location_code),
            "drop_off_country": int(drop_country),
            "drop_off_city": int(drop_city),
            "drop_off_location": int(drop_location),
            "pickup_date": pickup_dt,
            "drop_off_date": dropoff_dt,
        }

        data = await self._api_request("POST", "/cars/find", json=payload)
        raw_vehicles = data.get("data", [])
        logger.info("[click2rent] Got %d raw vehicle entries from API", len(raw_vehicles))

        # Fetch extras
        extras_data = await self._api_request("POST", "/cars/extra/find", json=payload)
        raw_extras = extras_data.get("data", [])

        rental_days = (request.dropoff_date - request.pickup_date).days or 1

        # Group raw results by car_id — each car can have multiple packages
        from collections import defaultdict
        car_groups: dict[int, list[dict]] = defaultdict(list)
        for raw in raw_vehicles:
            cid = raw.get("id")
            if cid is not None:
                car_groups[cid].append(raw)

        vehicles = []
        for car_id, entries in car_groups.items():
            v = self._build_vehicle(
                car_id, entries, raw_extras, rental_days, car_details,
                pickup_entry, hire_point_id, drop_hp_id, hp_data, payload,
            )
            if v:
                vehicles.append(v)

        return vehicles

    def _build_vehicle(
        self,
        car_id: int,
        entries: list[dict],
        raw_extras: list[dict],
        rental_days: int,
        car_details: dict,
        pickup_entry: ProviderLocationEntry,
        pickup_hp_id: str,
        dropoff_hp_id: str,
        hp_data: dict,
        search_payload: dict,
    ) -> Vehicle | None:
        # Use first entry as base vehicle info
        base = entries[0]
        car_name = base.get("car_name", "")
        currency = "EUR"

        # Build product from POA (Pay on Arrival) package only — we don't offer PREPAY
        primary = None
        for entry in entries:
            pkg_name = entry.get("package_name", "Standard")
            payment = entry.get("payment_type", "rental_rates")
            total = float(entry.get("total_price", 0))
            ppd = float(entry.get("price_per_day", 0))
            if total <= 0:
                continue
            # Skip prepay packages — we only accept POA
            if "prepay" in pkg_name.lower() or "prepaid" in payment.lower():
                continue
            if primary is None or total < primary["total"]:
                primary = {
                    "type": "BAS",
                    "name": pkg_name,
                    "total": total,
                    "price_per_day": ppd,
                    "currency": currency,
                    "package_id": entry.get("package_id", 0),
                    "payment_type": payment,
                    "oneway_rental_id": entry.get("oneway_rental_id", 0),
                    "benefits": [
                        "Collision Damage Waiver (CDW)",
                        "Theft Protection",
                        "Third Party Liability",
                        "Unlimited Mileage",
                    ],
                }

        if not primary:
            return None

        products = [primary]

        # Enrich with car details (SIPP, fuel, drive type)
        details = car_details.get(car_id, {})
        sipp = details.get("acriss_sipp", "")
        fuel_raw = details.get("fuel", "")
        fuel_map = {"petrol": "petrol", "diesel": "diesel", "hybrid": "hybrid", "electric": "electric"}
        from app.schemas.vehicle import FuelType
        fuel_type = None
        if fuel_raw in fuel_map:
            fuel_type = FuelType(fuel_map[fuel_raw])

        # Car class from search response
        car_class = base.get("car_class", {})
        class_name = (car_class.get("name_en", "") or "").lower() if isinstance(car_class, dict) else ""
        category = CLASS_MAP.get(class_name, VehicleCategory.OTHER)

        # Transmission
        transmission = TransmissionType.AUTOMATIC if str(base.get("automatic", "0")) == "1" else TransmissionType.MANUAL

        # Split name
        parts = car_name.split(" ", 1)
        brand = parts[0] if parts else ""
        model = parts[1] if len(parts) > 1 else ""

        # Parse extras
        extras = self._parse_extras(raw_extras, rental_days)

        # Fees
        raw_fees = base.get("fees", [])
        for fee in raw_fees:
            if isinstance(fee, dict) and float(fee.get("amount", 0)) > 0:
                fee_name = fee.get("name", "Fee")
                fee_amount = float(fee.get("amount", 0))
                extras.append(Extra(
                    id=f"ext_click2rent_fee_{fee.get('id', fee_name)}",
                    name=fee_name,
                    daily_rate=round(fee_amount / rental_days, 2) if rental_days > 0 else fee_amount,
                    total_price=fee_amount, currency=currency,
                    max_quantity=1, type=ExtraType.FEE, mandatory=True,
                ))

        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_hp_id,
            name=pickup_entry.original_name,
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
        )

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": str(car_id),
            "provider_product_id": str(primary.get("package_id", "")),
            "provider_rate_id": str(car_id),
            "availability_status": "available",
            "name": car_name,
            "category": category,
            "make": brand,
            "model": model,
            "image_url": base.get("image", ""),
            "transmission": transmission,
            "seats": int(base.get("seats", 0)) or None,
            "doors": int(base.get("doors", 0)) or None,
            "bags_large": int(base.get("bags", 0)) or None,
            "air_conditioning": str(base.get("air_conditioning", "0")) == "1",
            "mileage_policy": MileagePolicy.UNLIMITED,
            "pickup_location": pickup_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=primary["total"],
                daily_rate=primary["price_per_day"],
                deposit_amount=625.0,
                deposit_currency=currency,
            ),
            "extras": extras,
            "cancellation_policy": None,
            "supplier_data": {
                "car_id": car_id,
                "package_id": primary.get("package_id", 0),
                "package_name": primary.get("name", ""),
                "payment_type": primary.get("payment_type", "rental_rates"),
                "oneway_rental_id": primary.get("oneway_rental_id", 0),
                "fee_total": float(base.get("fee_total", 0)),
                "pickup_charge": float(base.get("pickup_charge", 0)),
                "drop_off_charge": float(base.get("drop_off_charge", 0)),
                "pickup_hire_point_id": int(pickup_hp_id),
                "dropoff_hire_point_id": int(dropoff_hp_id),
                "excess_amount": 650.0,
                "deposit_amount": 625.0,
                "pickup_instructions": hp_data.get("instruction_1", ""),
                "dropoff_instructions": hp_data.get("instruction_2", ""),
                "pickup_station_name": base.get("pickup_location_name", ""),
                "pickup_address": hp_data.get("address", "") or (base.get("pickup_hire_point", {}).get("name", "") if isinstance(base.get("pickup_hire_point"), dict) else ""),
                "office_address": hp_data.get("address", ""),
                "office_phone": hp_data.get("phone_number", ""),
                "pickup_email": hp_data.get("email", ""),
                "dropoff_station_name": base.get("drop_off_location_name", ""),
                "pickup_date": search_payload["pickup_date"],
                "dropoff_date": search_payload["drop_off_date"],
                "country_code": search_payload["pickup_country"],
                "city_code": search_payload["pickup_city"],
                "location_code": search_payload["pickup_location"],
                "drop_off_country_code": search_payload["drop_off_country"],
                "drop_off_city_code": search_payload["drop_off_city"],
                "drop_off_location_code": search_payload["drop_off_location"],
                "products": products,
            },
        }

        if sipp:
            vehicle_kwargs["sipp_code"] = sipp
        if fuel_type:
            vehicle_kwargs["fuel_type"] = fuel_type

        return Vehicle(**vehicle_kwargs)

    def _parse_extras(self, raw_extras: list[dict], rental_days: int) -> list[Extra]:
        extras = []
        for ex in raw_extras:
            extra_id = ex.get("id")
            name = ex.get("name_en", "") or ex.get("name_fr", "")
            code = ex.get("code", "")
            price_per_day = float(ex.get("price", 0))
            max_qty = int(ex.get("quantity", 1))
            total = round(price_per_day * rental_days, 2)

            if not name or price_per_day <= 0:
                continue

            extras.append(
                Extra(
                    id=f"ext_click2rent_{extra_id}",
                    name=name,
                    daily_rate=price_per_day,
                    total_price=total,
                    currency=ex.get("base", "EUR").upper(),
                    max_quantity=min(max_qty, 10),
                    type=ExtraType.EQUIPMENT,
                    description=code,
                )
            )
        return extras

    # ─── Booking ────────────────────────────────────────────────────────

    async def create_booking(
        self, request: CreateBookingRequest, vehicle: Vehicle
    ) -> BookingResponse:
        sd = vehicle.supplier_data

        # Step 1: Create customer
        name_parts = (request.driver.first_name, request.driver.last_name)
        customer_payload = {
            "firstname": name_parts[0] or "Guest",
            "lastname": name_parts[1] or "Guest",
            "email": request.driver.email or "",
            "phone": request.driver.phone or "",
            "residence": (request.driver.country or "MU")[:2].upper(),
        }

        customer_data = await self._api_request("POST", "/customers/create", json=customer_payload)
        customer = customer_data.get("data", {})
        client_id = customer.get("id")

        if not client_id:
            logger.error("[click2rent] Failed to create customer: %s", customer_data)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                error_message=f"Failed to create customer: {customer_data}",
            )

        # Step 2: Create booking
        # Build extras array
        extras_payload = []
        for extra in request.extras:
            raw_id = extra.extra_id.replace("ext_click2rent_", "")
            if raw_id.startswith("fee_"):
                continue  # Skip fee-type extras
            try:
                extras_payload.append({"id": int(raw_id), "quantity": extra.quantity})
            except (ValueError, TypeError):
                pass

        booking_payload = {
            "client_id": client_id,
            "pickup_country": sd.get("country_code", 125),
            "pickup_city": sd.get("city_code"),
            "pickup_location": sd.get("location_code"),
            "drop_off_country": sd.get("drop_off_country_code", sd.get("country_code", 125)),
            "drop_off_city": sd.get("drop_off_city_code", sd.get("city_code")),
            "drop_off_location": sd.get("drop_off_location_code", sd.get("location_code")),
            "pickup_date": sd.get("pickup_date"),
            "drop_off_date": sd.get("dropoff_date"),
            "pickup_hire_point_id": sd.get("pickup_hire_point_id"),
            "drop_off_hire_point_id": sd.get("dropoff_hire_point_id"),
            "flight_number": request.flight_number or "",
            "payment_type": sd.get("payment_type", "rental_rates"),
            "package_id": sd.get("package_id", 0),
            "oneway_rental_id": sd.get("oneway_rental_id", 0),
            "car_id": sd.get("car_id"),
            "extra_options": extras_payload,
        }

        data = await self._api_request("POST", "/bookings/create", json=booking_payload)
        booking = data.get("data", {})
        booking_id = booking.get("id")
        booking_ref = booking.get("booking_ref", "")

        if not booking_id:
            logger.error("[click2rent] Booking creation failed: %s", data)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                error_message=f"Booking failed: {data}",
            )

        logger.info("[click2rent] Booking created: id=%s ref=%s", booking_id, booking_ref)

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_ref or str(booking_id),
            status=BookingStatus.CONFIRMED,
            vehicle_name=vehicle.name,
        )

    # ─── Cancel ─────────────────────────────────────────────────────────

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        data = await self._api_request("DELETE", f"/bookings/{supplier_booking_id}/delete")

        return CancelBookingResponse(
            supplier_id=self.supplier_id,
            supplier_booking_id=supplier_booking_id,
            cancelled=True,
        )

    # ─── Locations ──────────────────────────────────────────────────────

    async def get_locations(self) -> list[dict]:
        settings = get_settings()
        if not settings.click2rent_email:
            return []

        data = await self._api_request("GET", "/hire-points?paginate=false")
        hire_points = data.get("data", [])

        locations = []
        for hp in hire_points:
            if str(hp.get("is_enable", "0")) != "1":
                continue

            country = hp.get("country", {}) or {}
            city = hp.get("city", {}) or {}
            location = hp.get("location", {}) or {}

            # Note: Click2Rent lat/lng are swapped in API response
            raw_lat = hp.get("latitude", "")
            raw_lng = hp.get("longitude", "")
            try:
                lat = float(raw_lng) if raw_lng else None  # longitude field has latitude
                lng = float(raw_lat) if raw_lat else None  # latitude field has longitude
            except (ValueError, TypeError):
                lat = None
                lng = None

            # Detect airport (only by name, not zone — zone is the same for all locations on the island)
            name = hp.get("name", "")
            is_airport = "airport" in name.lower()

            locations.append({
                "id": str(hp["id"]),
                "provider_location_id": str(hp["id"]),
                "name": name,
                "city": city.get("name", ""),
                "country": country.get("name", ""),
                "country_code": "MU",
                "latitude": lat,
                "longitude": lng,
                "location_type": "airport" if is_airport else "downtown",
                "iata": "MRU" if is_airport else None,
                "address": hp.get("address", ""),
                "phone": hp.get("phone_number", ""),
                "email": hp.get("email", ""),
                "operating_hours": self._format_business_hours(hp.get("business_hour", [])),
                "pickup_instructions": hp.get("instruction_1", ""),
                "dropoff_instructions": hp.get("instruction_2", ""),
                "extra_data": {
                    "country_code": country.get("code"),
                    "city_code": city.get("code"),
                    "location_code": location.get("code"),
                    "hire_point_id": hp["id"],
                    "instruction_1": hp.get("instruction_1", ""),
                    "instruction_2": hp.get("instruction_2", ""),
                    "zone": hp.get("zone", ""),
                },
            })

        logger.info("[click2rent] Fetched %d enabled locations", len(locations))
        return locations

    def _format_business_hours(self, hours: list) -> str:
        if not hours:
            return ""
        # Group by regular hours (non-OOH)
        regular = [h for h in hours if float(h.get("charge_1", 0)) == 0]
        if regular:
            first = regular[0]
            return f"{first.get('time_1', '')} - {first.get('time_2', '')}"
        return ""
