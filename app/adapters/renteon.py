"""Renteon adapter — REST JSON with Basic Auth."""

import logging
import uuid
from base64 import b64encode

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER_CODES = ["LetsDrive", "CapitalCarRental", "LuxGoo", "Alquicoche"]

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


def _parse_transmission_from_sipp(sipp: str) -> TransmissionType | None:
    """3rd char of SIPP: A/B/D = automatic, M/N/C = manual when deterministic."""
    if len(sipp) >= 3:
        code = sipp[2].upper()
        if code in ("A", "B", "D"):
            return TransmissionType.AUTOMATIC
        if code in ("M", "N", "C"):
            return TransmissionType.MANUAL
    return None


def _parse_fuel_from_sipp(sipp: str) -> FuelType | None:
    """4th char of SIPP: D/Q=diesel, H/I=hybrid, E/C=electric, L=lpg when deterministic."""
    if len(sipp) >= 4:
        c = sipp[3].upper()
        fuel_map = {
            "D": FuelType.DIESEL, "Q": FuelType.DIESEL,
            "H": FuelType.HYBRID, "I": FuelType.HYBRID,
            "E": FuelType.ELECTRIC, "C": FuelType.ELECTRIC,
            "L": FuelType.LPG,
        }
        return fuel_map.get(c)
    return None


def _map_service_type(service: dict) -> ExtraType:
    group = (service.get("ServiceGroupName") or "").lower()
    if "insurance" in group:
        return ExtraType.INSURANCE
    if "fee" in group:
        return ExtraType.FEE
    return ExtraType.EQUIPMENT


@register_adapter
class RenteonAdapter(BaseAdapter):
    supplier_id = "renteon"
    supplier_name = "Renteon"
    supports_one_way = True
    default_timeout = 45.0  # Renteon can be slow, but must stay under global 55s timeout

    def _auth_headers(self) -> dict[str, str]:
        settings = get_settings()
        creds = b64encode(f"{settings.renteon_username}:{settings.renteon_password}".encode()).decode()
        return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        base_url = settings.renteon_api_url.rstrip("/")

        pickup_code = pickup_entry.pickup_id
        dropoff_code = dropoff_entry.pickup_id if dropoff_entry else pickup_code

        # Format datetimes without timezone (Renteon requirement)
        pickup_dt = f"{request.pickup_date.isoformat()}T{request.pickup_time.strftime('%H:%M:%S')}"
        dropoff_dt = f"{request.dropoff_date.isoformat()}T{request.dropoff_time.strftime('%H:%M:%S')}"

        # Search all configured provider codes (PHP searches LetsDrive, CapitalCarRental, etc.)
        provider_codes = [
            p.strip() for p in settings.renteon_allowed_providers.split(",") if p.strip()
        ]
        if not provider_codes:
            provider_codes = list(_DEFAULT_PROVIDER_CODES)

        all_vehicles: list[Vehicle] = []
        rental_days = (request.dropoff_date - request.pickup_date).days or 1

        # Build PricelistCodes from config (if any)
        pricelist_codes_str = settings.renteon_pricelist_codes
        pricelist_codes: list[str] = []
        if pricelist_codes_str:
            pricelist_codes = [c.strip() for c in pricelist_codes_str.split(",") if c.strip()]

        for provider_code in provider_codes:
            provider_entry: dict = {"Code": provider_code}
            if pricelist_codes:
                provider_entry["PricelistCodes"] = pricelist_codes

            payload = {
                "PickupLocation": pickup_code,
                "DropOffLocation": dropoff_code,
                "PickupDate": pickup_dt,
                "DropOffDate": dropoff_dt,
                "Currency": "EUR",  # Renteon only supports EUR queries
                "Prepaid": False,
                "IncludeOnRequest": True,
                "Providers": [provider_entry],
                "CarCategories": [],
                "Drivers": [{"DriverAge": request.driver_age}],
                "HasDelivery": False,
                "HasCollection": False,
            }

            try:
                response = await self._request(
                    "POST",
                    f"{base_url}/api/bookings/availability",
                    json=payload,
                    headers=self._auth_headers(),
                )

                data = response.json()

                if data is None:
                    logger.info("[renteon] Provider %s returned null", provider_code)
                    continue

                # Handle both formats: flat list [...] or dict {"Vehicles": [...]}
                vehicles_raw: list = []
                if isinstance(data, list):
                    vehicles_raw = data
                elif isinstance(data, dict):
                    if "Vehicles" in data and isinstance(data["Vehicles"], list):
                        vehicles_raw = data["Vehicles"]
                    else:
                        logger.warning("[renteon] Provider %s returned dict without Vehicles key: %s", provider_code, str(data)[:300])
                        continue
                else:
                    logger.warning("[renteon] Provider %s unexpected response type: %s", provider_code, type(data).__name__)
                    continue

                logger.info("[renteon] Provider %s returned %d raw vehicles", provider_code, len(vehicles_raw))

                for raw in vehicles_raw:
                    v = self._parse_vehicle(raw, request, rental_days, pickup_entry, provider_code)
                    if v is not None:
                        all_vehicles.append(v)

            except Exception as exc:
                logger.warning("[renteon] Provider %s search failed: %s", provider_code, str(exc))
                continue

        return all_vehicles

    def _parse_vehicle(
        self,
        raw: dict,
        request: SearchRequest,
        rental_days: int,
        pickup_entry: ProviderLocationEntry,
        provider_code: str = "",
    ) -> Vehicle | None:
        model_name = raw.get("ModelName", "")
        if not model_name:
            return None

        sipp = raw.get("CarCategory", "")
        total_price = _safe_float(raw.get("Amount"))
        currency = raw.get("Currency", request.currency)
        daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else total_price

        # Parse make/model from ModelName
        name_parts = model_name.split(" ", 1)
        make = name_parts[0].title() if name_parts else ""
        model = name_parts[1].title() if len(name_parts) > 1 else ""

        # Parse pickup office info
        office = raw.get("PickupOffice") or {}
        dropoff_office_raw = raw.get("DropOffOffice") or {}
        pickup_loc = VehicleLocation(
            supplier_location_id=str(raw.get("PickupOfficeId", "")),
            name=office.get("Name") or pickup_entry.original_name,
            city=office.get("Town", ""),
            latitude=_safe_float(office.get("Latitude")),
            longitude=_safe_float(office.get("Longitude")),
            location_type=office.get("LocationType", "other").lower(),
            airport_code=office.get("OfficeCode"),
        )

        # Populate dropoff_location whenever the provider payload includes a
        # DropOffOffice distinct from the pickup. The adapter owns this contract
        # because downstream UIs (map pins, dropoff instructions) need coords.
        dropoff_loc = None
        dropoff_office_id = str(raw.get("DropOffOfficeId", "") or "")
        pickup_office_id = str(raw.get("PickupOfficeId", "") or "")
        if dropoff_office_raw and (
            dropoff_office_id and dropoff_office_id != pickup_office_id
            or (dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id)
        ):
            dropoff_loc = VehicleLocation(
                supplier_location_id=dropoff_office_id or (dropoff_entry.pickup_id if dropoff_entry else ""),
                name=dropoff_office_raw.get("Name") or (dropoff_entry.original_name if dropoff_entry else None),
                city=dropoff_office_raw.get("Town", ""),
                latitude=_safe_float(dropoff_office_raw.get("Latitude")),
                longitude=_safe_float(dropoff_office_raw.get("Longitude")),
                location_type=dropoff_office_raw.get("LocationType", "other").lower(),
                airport_code=dropoff_office_raw.get("OfficeCode"),
            )

        # Payment option
        is_prepaid = raw.get("Prepaid", False) or raw.get("IsPrepaid", False)
        payment = PaymentOption.PAY_NOW if is_prepaid else PaymentOption.PAY_AT_PICKUP

        # Parse extras/services
        extras = self._parse_services(raw.get("AvailableServices") or [], rental_days)

        # Insurance from excess amounts
        insurance_options = []
        excess = _safe_float(raw.get("ExcessAmount"))
        if excess > 0:
            insurance_options.append(InsuranceOption(
                id=f"ins_{self.supplier_id}_basic",
                coverage_type=CoverageType.BASIC,
                name="Basic Cover",
                daily_rate=0,
                total_price=0,
                excess_amount=excess,
                included=True,
            ))

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": str(raw.get("ConnectorId", "")),
            "provider_product_id": str(raw.get("ConnectorId", "")) or None,
            "provider_rate_id": str(raw.get("PricelistCode") or raw.get("PricelistId") or "") or None,
            "availability_status": "on_request" if raw.get("IsOnRequest", False) else "available",
            "name": model_name,
            "category": category_from_sipp(sipp),
            "make": make,
            "model": model,
            "image_url": raw.get("CarModelImageURL", ""),
            "pickup_location": pickup_loc,
            "dropoff_location": dropoff_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=total_price,
                daily_rate=daily_rate,
                deposit_amount=_safe_float(raw.get("DepositAmount")) or None,
                deposit_currency=raw.get("DepositCurrency", currency),
                payment_options=[payment],
            ),
            "insurance_options": insurance_options,
            "extras": extras,
            "cancellation_policy": None,  # API does not return cancellation terms
            "supplier_data": {
                "connector_id": raw.get("ConnectorId"),
                "pricelist_id": raw.get("PricelistId"),
                "pricelist_code": raw.get("PricelistCode"),
                "price_date": raw.get("PriceDate"),
                "pickup_office_id": raw.get("PickupOfficeId"),
                "dropoff_office_id": raw.get("DropOffOfficeId"),
                "provider": raw.get("Provider"),
                "provider_code": provider_code,
                "pickup_office": self._normalize_office(office),
                "dropoff_office": self._normalize_office(dropoff_office_raw),
                "office_phone": (office or {}).get("Tel") or (office or {}).get("Phone") or "",
                "pickup_email": (office or {}).get("Email") or "",
                "pickup_address": (office or {}).get("Address") or "",
                "pickup_station_name": (office or {}).get("Name") or "",
                "pickup_instructions": (office or {}).get("OfficePickupInstructions") or (office or {}).get("PickupInstructions") or "",
                "dropoff_instructions": (dropoff_office_raw or {}).get("OfficeDropOffInstructions") or (dropoff_office_raw or {}).get("DropOffInstructions") or "",
                "is_on_request": raw.get("IsOnRequest", False),
                "net_amount": _safe_float(raw.get("NetAmount")),
                "vat_amount": _safe_float(raw.get("VatAmount")),
                "excess_theft_amount": _safe_float(raw.get("ExcessTheftAmount")),
                "prepaid": raw.get("Prepaid", False) or raw.get("IsPrepaid", False),
            },
            "min_driver_age": _safe_int(raw.get("MinimumDriverAge")) or None,
            "max_driver_age": _safe_int(raw.get("MaximumDriverAge")) or None,
        }

        transmission = _parse_transmission_from_sipp(sipp)
        fuel_type = _parse_fuel_from_sipp(sipp)
        if transmission is not None:
            vehicle_kwargs["transmission"] = transmission
        if fuel_type is not None:
            vehicle_kwargs["fuel_type"] = fuel_type
        if raw.get("PassengerCapacity") is not None:
            vehicle_kwargs["seats"] = _safe_int(raw.get("PassengerCapacity"))
        if raw.get("NumberOfDoors") is not None:
            vehicle_kwargs["doors"] = _safe_int(raw.get("NumberOfDoors"))
        if raw.get("BigBagsCapacity") is not None:
            vehicle_kwargs["bags_large"] = _safe_int(raw.get("BigBagsCapacity"))
        if raw.get("SmallBagsCapacity") is not None:
            vehicle_kwargs["bags_small"] = _safe_int(raw.get("SmallBagsCapacity"))
        if sipp:
            vehicle_kwargs["sipp_code"] = sipp

        return Vehicle(**vehicle_kwargs)

    def _parse_services(self, services: list[dict], rental_days: int) -> list[Extra]:
        extras = []
        for svc in services:
            svc_id = svc.get("ServiceId") or svc.get("AdditionalCode")
            name = svc.get("AdditionalName") or svc.get("Name", "")
            amount = _safe_float(svc.get("Amount"))
            is_one_time = svc.get("IsOneTimePayment", False)

            if not svc_id or not name:
                continue

            if is_one_time:
                daily_rate = round(amount / rental_days, 2) if rental_days > 0 else amount
                total_price = amount
            else:
                daily_rate = amount
                total_price = round(amount * rental_days, 2)

            extras.append(Extra(
                id=f"ext_{self.supplier_id}_{svc_id}",
                name=name,
                daily_rate=daily_rate,
                total_price=total_price,
                max_quantity=_safe_int(svc.get("MaximumQuantity"), 1),
                type=_map_service_type(svc),
                mandatory=False,
                description=svc.get("DescriptionWeb") or svc.get("Description", ""),
                supplier_data={
                    "code": svc_id,
                    "service_group": svc.get("ServiceGroupName") or "",
                    "service_type": svc.get("ServiceTypeName") or "",
                    "free_of_charge": svc.get("FreeOfCharge", False),
                    "included_in_price": svc.get("IncludedInPriceUnlimited", False),
                    "included_in_price_limited": svc.get("IncludedInPriceLimited", False),
                    "is_one_time": is_one_time,
                    "quantity_included": _safe_int(svc.get("QuantityIncluded"), 0),
                },
            ))
        return extras

    @staticmethod
    def _normalize_office(office: dict) -> dict | None:
        """Normalize a Renteon PickupOffice/DropOffOffice to a flat dict for the frontend."""
        if not office:
            return None
        name = office.get("Name") or ""
        address = office.get("Address") or ""
        # Skip if no meaningful data at all
        if not name and not address and not office.get("Tel") and not office.get("Email"):
            return None
        return {
            "office_id": office.get("OfficeId"),
            "office_code": office.get("OfficeCode"),
            "name": name,
            "address": address,
            "town": office.get("Town", ""),
            "postal_code": office.get("PostalCode", ""),
            "phone": office.get("Tel") or office.get("Phone") or "",
            "email": office.get("Email") or "",
            "pickup_instructions": office.get("OfficePickupInstructions") or office.get("PickupInstructions") or "",
            "dropoff_instructions": office.get("OfficeDropOffInstructions") or office.get("DropOffInstructions") or "",
            "location_type": (office.get("LocationType") or "other").lower(),
            "is_shuttle": office.get("IsShuttle", False),
        }

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        settings = get_settings()
        base_url = settings.renteon_api_url.rstrip("/")
        sd = vehicle.supplier_data

        # Build pickup/dropoff datetimes from request or fall back to supplier_data
        pickup_dt = ""
        dropoff_dt = ""
        if request.pickup_date and request.dropoff_date:
            pickup_time = request.pickup_time or "09:00"
            dropoff_time = request.dropoff_time or "09:00"
            pickup_dt = f"{request.pickup_date.isoformat()}T{pickup_time}:00"
            dropoff_dt = f"{request.dropoff_date.isoformat()}T{dropoff_time}:00"

        # Step 1: Create
        create_payload = {
            "ConnectorId": sd.get("connector_id"),
            "CarCategory": vehicle.sipp_code,
            "PickupOfficeId": sd.get("pickup_office_id"),
            "DropOffOfficeId": sd.get("dropoff_office_id"),
            "PickupDate": pickup_dt,
            "DropOffDate": dropoff_dt,
            "PricelistId": sd.get("pricelist_id"),
            "Currency": vehicle.pricing.currency,
            "Prepaid": sd.get("prepaid", True),
            "PriceDate": sd.get("price_date"),
            "ClientName": f"{request.driver.first_name} {request.driver.last_name}",
            "ClientEmail": request.driver.email,
            "ClientPhone": request.driver.phone,
            "FlightNumber": request.flight_number or "",
            "Drivers": [{"DriverAge": request.driver.age, "Name": request.driver.first_name, "Surname": request.driver.last_name}],
            "Services": [
                {
                    "ServiceId": int(e.extra_id.replace(f"ext_{self.supplier_id}_", "")),
                    "IsSelected": True,
                    "Quantity": e.quantity,
                }
                for e in request.extras
            ],
        }

        create_resp = await self._request(
            "POST",
            f"{base_url}/api/bookings/create",
            json=create_payload,
            headers=self._auth_headers(),
        )
        create_data = create_resp.json()

        # Step 2: Save — use create response as base, force client fields from create_payload
        save_payload = dict(create_data) if isinstance(create_data, dict) else {}
        # Force-set client fields (create response may return them as null/empty)
        for field in ("ClientName", "ClientEmail", "ClientPhone", "FlightNumber"):
            val = create_payload.get(field, "")
            existing = save_payload.get(field)
            if not existing or (isinstance(existing, str) and not existing.strip()):
                save_payload[field] = val
        if not save_payload.get("Services"):
            save_payload["Services"] = create_payload.get("Services", [])
        if not save_payload.get("Drivers"):
            save_payload["Drivers"] = create_payload.get("Drivers", [])
        save_resp = await self._request(
            "POST",
            f"{base_url}/api/bookings/save",
            json=save_payload,
            headers=self._auth_headers(),
        )
        save_data = save_resp.json()
        result = save_data if save_data else create_data

        # Renteon returns booking reference in 'Number' field (e.g. "26-05-1438")
        booking_ref = ""
        if isinstance(result, dict):
            for key in ("Number", "ReservationNo", "BookingNumber", "BookingRef", "Id"):
                val = result.get(key)
                if val is not None and str(val).strip():
                    booking_ref = str(val).strip()
                    break

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_ref,
            status=BookingStatus.CONFIRMED if booking_ref else BookingStatus.FAILED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=result if isinstance(result, dict) else {},
        )

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()
        base_url = settings.renteon_api_url.rstrip("/")

        await self._request(
            "DELETE",
            f"{base_url}/api/bookings/{supplier_booking_id}",
            headers=self._auth_headers(),
        )

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
        )

    async def get_booking_pdf(
        self, connector_id: int, booking_id: int, culture: str = "en"
    ) -> bytes | None:
        """Download booking voucher PDF from Renteon."""
        settings = get_settings()
        base_url = settings.renteon_api_url.rstrip("/")

        response = await self._request(
            "GET",
            f"{base_url}/api/bookings/getPdf",
            params={
                "connectorId": connector_id,
                "id": booking_id,
                "culture": culture,
                "printHeader": True,
                "printFooter": True,
            },
            headers=self._auth_headers(),
        )

        if response.status_code == 200 and response.content:
            return response.content
        return None

    async def get_locations(self) -> list[dict]:
        settings = get_settings()
        base_url = settings.renteon_api_url.rstrip("/")

        response = await self._request(
            "GET",
            f"{base_url}/api/setup/locations",
            headers=self._auth_headers(),
        )

        data = response.json()
        if not isinstance(data, list):
            return []

        locations = []
        for loc in data:
            # Only use locations with Category "PickupDropoff"
            if loc.get("Category") != "PickupDropoff":
                continue
            # Extract city from Path (e.g. "Athens > Athens airport" → "Athens")
            path = loc.get("Path") or ""
            city = path.split(">")[0].strip() if ">" in path else ""
            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": loc.get("Code", ""),
                "name": loc.get("Name", ""),
                "city": city,
                "country_code": loc.get("CountryCode", ""),
                "location_type": (loc.get("Type") or "other").lower(),
            })

        return locations
