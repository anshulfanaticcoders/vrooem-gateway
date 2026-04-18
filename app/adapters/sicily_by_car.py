"""Sicily By Car (SBC / Usave) adapter — REST JSON with X-API-Key auth.

SBC uses a two-phase booking pattern: createReservation + commitReservation.
All endpoints are POST to: {base_url}/v2/{account_code}/{endpoint}
"""

import logging
import uuid
from urllib.parse import quote

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
    category_from_sipp,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    Extra,
    InsuranceOption,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


_FUEL_MAP: dict[str, FuelType] = {
    "petrol": FuelType.PETROL,
    "diesel": FuelType.DIESEL,
    "electric": FuelType.ELECTRIC,
    "hybrid": FuelType.HYBRID,
    "lpg": FuelType.LPG,
}

_TRANSMISSION_MAP: dict[str, TransmissionType] = {
    "manual": TransmissionType.MANUAL,
    "automatic": TransmissionType.AUTOMATIC,
}


def _map_fuel_type(raw: str | None) -> FuelType | None:
    if not raw:
        return None
    return _FUEL_MAP.get(raw.lower())


def _map_transmission(raw: str | None) -> TransmissionType | None:
    if not raw:
        return None
    return _TRANSMISSION_MAP.get(raw.lower())


def _map_payment(rate_id: str) -> PaymentOption:
    """BASIC-POA / PLUS-POA -> pay_at_pickup, BASIC-PRE / PLUS-PRE -> pay_now."""
    if rate_id.upper().endswith("-PRE"):
        return PaymentOption.PAY_NOW
    return PaymentOption.PAY_AT_PICKUP


def _map_coverage_type(rate_id: str) -> CoverageType:
    """PLUS rates have enhanced coverage, BASIC rates have basic."""
    if rate_id.upper().startswith("PLUS"):
        return CoverageType.STANDARD
    return CoverageType.BASIC


def _map_location_type(raw: str | None) -> str:
    """Map SBC location type to our canonical type string."""
    if not raw:
        return "other"
    lower = raw.lower()
    if lower == "airport":
        return "airport"
    if lower == "office":
        return "downtown"
    return "other"


def _parse_make_model(description: str) -> tuple[str, str]:
    """Extract make/model from SBC description like 'Fiat Panda 1.2 or similar'."""
    clean = description.replace(" or similar", "").strip()
    parts = clean.split(" ", 1)
    make = parts[0].title() if parts else ""
    model = parts[1].title() if len(parts) > 1 else ""
    return make, model


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register_adapter
class SicilyByCarAdapter(BaseAdapter):
    supplier_id = "sicily_by_car"
    supplier_name = "Sicily By Car"
    supports_one_way = True
    default_timeout = 20.0

    # ── Internal helpers ──

    def _base_url(self) -> str:
        settings = get_settings()
        return settings.sicilybycar_api_url.rstrip("/")

    def _account_code(self) -> str:
        settings = get_settings()
        return settings.sicilybycar_account_code

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        return {
            "X-API-Key": settings.sicilybycar_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, endpoint: str) -> str:
        """Build full URL: {base}/v2/{account_code}/{endpoint}."""
        return f"{self._base_url()}/v2/{quote(self._account_code(), safe='')}/{endpoint.lstrip('/')}"

    async def _post(self, endpoint: str, payload: dict) -> dict:
        """POST to SBC API, return parsed JSON response.

        Raises on HTTP transport errors; returns the full envelope so callers
        can check ``resp["ok"]``.
        """
        response = await self._request(
            "POST",
            self._url(endpoint),
            json=payload,
            headers=self._headers(),
        )
        return response.json()

    # ── search_vehicles ──

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        pickup_code = pickup_entry.pickup_id
        dropoff_code = dropoff_entry.pickup_id if dropoff_entry else pickup_code

        # SBC uses ISO 8601 without timezone: YYYY-MM-DDTHH:MM:SS
        pickup_dt = f"{request.pickup_date.isoformat()}T{request.pickup_time.strftime('%H:%M:%S')}"
        dropoff_dt = f"{request.dropoff_date.isoformat()}T{request.dropoff_time.strftime('%H:%M:%S')}"

        dropoff_different = dropoff_code != pickup_code

        payload = {
            "pickUpLocation": pickup_code,
            "pickUpDateTime": pickup_dt,
            "dropoffAtDifferentLocation": dropoff_different,
            "dropOffLocation": dropoff_code,
            "dropOffDateTime": dropoff_dt,
            "driverAge": request.driver_age,
        }

        # Add posCountry if available
        if request.country_code:
            payload["posCountry"] = request.country_code.upper()

        resp = await self._post("offers/availability", payload)

        # SBC may return data directly or wrapped in {"ok": true, "data": {...}}
        # Direct format: {"requestId": "...", "availabilityId": "...", "offers": [...]}
        # Wrapped format: {"ok": true, "data": {"offers": [...]}, "errors": []}
        if resp.get("ok") is False:
            errors = resp.get("errors") or []
            logger.warning("[sicily_by_car] availability failed: %s", errors)
            return []

        # Unwrap envelope if present, otherwise use response directly
        if "data" in resp and isinstance(resp["data"], dict):
            data = resp["data"]
        else:
            data = resp

        offers = data.get("offers")
        if not isinstance(offers, list) or not offers:
            return []

        availability_id = data.get("availabilityId", "")
        request_id = data.get("requestId", "")
        rental_days = max((request.dropoff_date - request.pickup_date).days, 1)

        vehicles: list[Vehicle] = []
        for offer in offers:
            vehicle = self._parse_offer(
                offer,
                request=request,
                rental_days=rental_days,
                availability_id=availability_id,
                request_id=request_id,
                pickup_entry=pickup_entry,
                dropoff_entry=dropoff_entry,
            )
            if vehicle is not None:
                vehicles.append(vehicle)

        return vehicles

    def _parse_offer(
        self,
        offer: dict,
        *,
        request: SearchRequest,
        rental_days: int,
        availability_id: str,
        request_id: str,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> Vehicle | None:
        veh = offer.get("vehicle") or {}
        rate = offer.get("rate") or {}
        total_prices = offer.get("totalPrices") or {}

        description = veh.get("description", "")
        if not description:
            return None

        # SIPP code — SBC uses 5-char codes (e.g. MDMRS); trim to 4 for category
        sipp_raw = veh.get("sipp", "")
        sipp_4 = sipp_raw[:4] if len(sipp_raw) >= 4 else sipp_raw

        rate_id = rate.get("id", "")
        payment = _map_payment(rate_id)

        total_price = _safe_float(total_prices.get("total"))
        daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else total_price
        currency = offer.get("currency", request.currency)
        deposit = _safe_float(offer.get("deposit"))

        make, model = _parse_make_model(description)

        # Mileage policy
        distance = rate.get("distance") or {}
        mileage_policy = None
        if "unlimited" in distance:
            mileage_policy = (
                MileagePolicy.UNLIMITED
                if distance.get("unlimited")
                else MileagePolicy.LIMITED
            )

        # Transmission — prefer explicit field, then infer from SIPP only when deterministic
        transmission_raw = veh.get("transmissionType")
        transmission = _map_transmission(transmission_raw)
        if transmission is None and len(sipp_4) >= 3:
            sipp_transmission_code = sipp_4[2].upper()
            if sipp_transmission_code in ("A", "B", "D"):
                transmission = TransmissionType.AUTOMATIC
            elif sipp_transmission_code in ("M", "N", "C"):
                transmission = TransmissionType.MANUAL

        # Pickup location from offer
        offer_pickup = offer.get("pickupLocation") or {}
        pickup_loc = VehicleLocation(
            supplier_location_id=offer_pickup.get("id", pickup_entry.pickup_id),
            name=offer_pickup.get("name", pickup_entry.original_name),
            airport_code=offer_pickup.get("airportCode"),
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
        )

        # Dropoff location — populate only when the caller asked for a distinct
        # dropoff. Downstream UIs (map pin, booking views) rely on this object
        # existing to render the drop-off address / coords.
        dropoff_loc = None
        offer_dropoff = offer.get("dropoffLocation") or offer.get("returnLocation") or {}
        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            dropoff_loc = VehicleLocation(
                supplier_location_id=offer_dropoff.get("id", dropoff_entry.pickup_id),
                name=offer_dropoff.get("name", dropoff_entry.original_name),
                airport_code=offer_dropoff.get("airportCode"),
                latitude=dropoff_entry.latitude,
                longitude=dropoff_entry.longitude,
            )

        # Insurance based on rate coverage level
        insurance_options: list[InsuranceOption] = []
        coverage_type = _map_coverage_type(rate_id)
        insurance_options.append(
            InsuranceOption(
                id=f"ins_{self.supplier_id}_{rate_id.lower().replace('-', '_')}",
                coverage_type=coverage_type,
                name=rate.get("description", rate_id),
                daily_rate=0,
                total_price=0,
                excess_amount=deposit if deposit > 0 else None,
                included=True,
            )
        )

        # Unique supplier vehicle id combines vehicle category + rate for dedup
        supplier_vehicle_id = f"{veh.get('id', '')}_{rate_id}"

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": supplier_vehicle_id,
            "provider_rate_id": rate_id or None,
            "name": description,
            "category": category_from_sipp(sipp_4),
            "make": make,
            "model": model,
            "image_url": veh.get("imageUrl", ""),
            "availability_status": offer.get("availability") or None,
            "pickup_location": pickup_loc,
            "dropoff_location": dropoff_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=total_price,
                daily_rate=daily_rate,
                deposit_amount=deposit if deposit > 0 else None,
                deposit_currency=currency,
                payment_options=[payment],
            ),
            "insurance_options": insurance_options,
            "extras": self._parse_services(offer.get("services") or [], rental_days),
            "cancellation_policy": None,  # API does not return cancellation terms
            "supplier_data": {
                "availability_id": availability_id,
                "request_id": request_id,
                "vehicle_category_id": veh.get("id"),
                "rate_id": rate_id,
                "rate_payment": rate.get("payment"),
                "rate_description": rate.get("description"),
                "sipp_raw": sipp_raw,
                "deposit": deposit,
                "total_prices": total_prices,
                "services_raw": offer.get("services") or [],
                "pickup_location_id": pickup_entry.pickup_id,
                "dropoff_location_id": (dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id),
            },
        }

        fuel_type = _map_fuel_type(veh.get("fuelType"))
        if transmission is not None:
            vehicle_kwargs["transmission"] = transmission
        if fuel_type is not None:
            vehicle_kwargs["fuel_type"] = fuel_type
        if veh.get("numberOfPassengers") is not None:
            vehicle_kwargs["seats"] = _safe_int(veh.get("numberOfPassengers"))
        if veh.get("numberOfDoors") is not None:
            vehicle_kwargs["doors"] = _safe_int(veh.get("numberOfDoors"))
        if veh.get("luggageBig") is not None:
            vehicle_kwargs["bags_large"] = _safe_int(veh.get("luggageBig"))
        if veh.get("luggageSmall") is not None:
            vehicle_kwargs["bags_small"] = _safe_int(veh.get("luggageSmall"))
        air_conditioning = veh.get("airConditioning")
        if isinstance(air_conditioning, bool):
            vehicle_kwargs["air_conditioning"] = air_conditioning
        elif isinstance(air_conditioning, str) and air_conditioning.lower() in ("true", "false"):
            vehicle_kwargs["air_conditioning"] = air_conditioning.lower() == "true"
        if mileage_policy is not None:
            vehicle_kwargs["mileage_policy"] = mileage_policy
        if sipp_raw:
            vehicle_kwargs["sipp_code"] = sipp_raw

        return Vehicle(**vehicle_kwargs)

    @staticmethod
    def _parse_services(services: list, rental_days: int) -> list[Extra]:
        """Convert SBC services into canonical extras.

        The frontend reads vehicle.extras for SicilyByCar and splits them into:
        - isMandatory → included services
        - CDW/TLW/CPP/GLD/PAI/RAP → protection plans
        - everything else → optional extras
        We preserve ALL raw fields so the frontend can use them.
        """
        extras: list[Extra] = []
        for svc in services:
            if not isinstance(svc, dict):
                continue
            svc_id = svc.get("id") or svc.get("code") or ""
            total = _safe_float(svc.get("total"))
            daily = round(total / rental_days, 2) if rental_days > 0 else total
            is_mandatory = bool(svc.get("isMandatory"))
            description = svc.get("description") or svc_id

            ext = Extra(
                id=f"ext_sicily_by_car_{svc_id}",
                name=description,
                daily_rate=daily,
                total_price=total,
                max_quantity=1,
                type=ExtraType.EQUIPMENT,
                mandatory=is_mandatory,
                description=description,
                supplier_data={
                    "id": svc_id,
                    "description": description,
                    "isMandatory": is_mandatory,
                    "total": total,
                    "excess": svc.get("excess"),
                    "excessAmount": svc.get("excessAmount"),
                    "payment": svc.get("payment"),
                },
            )
            extras.append(ext)
        return extras

    # ── create_booking ──

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        """Two-phase booking: createReservation then commitReservation."""
        sd = vehicle.supplier_data

        # Build pickup/dropoff datetimes
        pickup_dt = ""
        dropoff_dt = ""
        if request.pickup_date and request.dropoff_date:
            pickup_time = request.pickup_time or "10:00"
            dropoff_time = request.dropoff_time or "10:00"
            pickup_dt = f"{request.pickup_date.isoformat()}T{pickup_time}:00"
            dropoff_dt = f"{request.dropoff_date.isoformat()}T{dropoff_time}:00"

        # Phase 1: Create reservation (hold inventory)
        create_payload = {
            "availabilityId": sd.get("availability_id", ""),
            "vehicleCategoryId": sd.get("vehicle_category_id", ""),
            "rateId": sd.get("rate_id", ""),
            "pickupDatetime": pickup_dt,
            "dropoffDatetime": dropoff_dt,
            "pickupLocationId": sd.get("pickup_location_id", ""),
            "dropoffLocationId": sd.get("dropoff_location_id", sd.get("pickup_location_id", "")),
            "customer": {
                "firstName": request.driver.first_name,
                "lastName": request.driver.last_name,
                "email": request.driver.email,
                "phone": request.driver.phone or "",
                "address": request.driver.address or "",
                "city": request.driver.city or "",
                "country": request.driver.country or "",
                "postalCode": request.driver.postal_code or "",
            },
            "flightNumber": request.flight_number or "",
            "specialRequests": request.special_requests or "",
        }

        # Attach selected extras (strip gateway prefix)
        if request.extras:
            prefix = f"ext_{self.supplier_id}_"
            create_payload["services"] = [
                {"serviceId": e.extra_id.replace(prefix, ""), "quantity": e.quantity}
                for e in request.extras
            ]

        create_resp = await self._post("reservations/create", create_payload)

        if create_resp.get("ok") is False:
            errors = create_resp.get("errors") or []
            logger.error("[sicily_by_car] createReservation failed: %s", errors)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data=create_resp,
            )

        # Unwrap envelope if present
        create_data = create_resp.get("data") if isinstance(create_resp.get("data"), dict) else create_resp
        reservation_id = str(create_data.get("reservationId", ""))

        if not reservation_id:
            logger.error("[sicily_by_car] createReservation returned no reservationId")
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data=create_data,
            )

        # Phase 2: Commit reservation (confirm)
        commit_resp = await self._post("reservations/commit", {"reservationId": reservation_id})

        if commit_resp.get("ok") is False:
            # Attempt to release the held reservation
            try:
                await self._post("reservations/ignore", {"reservationId": reservation_id})
            except Exception:
                logger.warning("[sicily_by_car] Failed to ignore held reservation %s", reservation_id)

            errors = commit_resp.get("errors") or []
            logger.error("[sicily_by_car] commitReservation failed: %s", errors)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id=reservation_id,
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"create": create_data, "commit_error": commit_resp},
            )

        commit_data = commit_resp.get("data") if isinstance(commit_resp.get("data"), dict) else commit_resp

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=reservation_id,
            status=BookingStatus.CONFIRMED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data={"create": create_data, "commit": commit_data},
        )

    # ── cancel_booking ──

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        resp = await self._post("reservations/cancel", {"reservationId": supplier_booking_id})

        if resp.get("ok") is False:
            errors = resp.get("errors") or []
            logger.error("[sicily_by_car] cancelReservation failed: %s", errors)

        # Consider cancelled unless explicitly failed
        is_success = resp.get("ok") is not False
        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED if is_success else BookingStatus.FAILED,
            supplier_cancellation_id=supplier_booking_id,
        )

    # ── get_locations ──

    async def get_locations(self) -> list[dict]:
        resp = await self._post("locations/list", {})

        if resp.get("ok") is False:
            logger.warning("[sicily_by_car] listLocations failed: %s", resp.get("errors"))
            return []

        # SBC may return locations wrapped or directly
        # Try data.locations, then data (if list), then resp.locations, then resp directly
        data = resp.get("data") if isinstance(resp.get("data"), (dict, list)) else resp
        if isinstance(data, dict):
            raw_locations = data.get("locations", [])
        elif isinstance(data, list):
            raw_locations = data
        else:
            return []

        if not isinstance(raw_locations, list):
            return []

        locations: list[dict] = []
        for loc in raw_locations:
            loc_id = loc.get("id", "")
            if not loc_id:
                continue

            address = loc.get("address") or {}
            coords = loc.get("coordinates") or {}
            lat = _safe_float(coords.get("latitude"))
            lng = _safe_float(coords.get("longitude"))

            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": loc_id,
                "name": loc.get("name", ""),
                "country_code": address.get("country", ""),
                "city": address.get("city", ""),
                "location_type": _map_location_type(loc.get("type")),
                "airport_code": loc.get("airportCode"),
                "latitude": lat if lat != 0 else None,
                "longitude": lng if lng != 0 else None,
                "address": address.get("addressLineOne", ""),
                "phone": loc.get("phone", ""),
                "email": loc.get("email", ""),
            })

        return locations
