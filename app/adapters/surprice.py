"""Surprice Mobility adapter — REST JSON with Bearer token auth."""

import asyncio
import logging
import uuid
from datetime import datetime

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
from app.schemas.pricing import Fee, Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    Extra,
    InsuranceOption,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)


class SurpriceOneWayNotAllowedError(RuntimeError):
    """Raised when Surprice explicitly rejects a one-way route."""


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


def _parse_fuel_from_sipp(sipp_code: str | None) -> FuelType:
    """Derive fuel type from the 4th character of the SIPP/ACRISS code."""
    if not sipp_code or len(sipp_code) < 4:
        return FuelType.PETROL
    fuel_char = sipp_code[3].upper()
    return {
        "D": FuelType.DIESEL,
        "E": FuelType.ELECTRIC,
        "C": FuelType.ELECTRIC,
        "H": FuelType.HYBRID,
        "Q": FuelType.HYBRID,
    }.get(fuel_char, FuelType.PETROL)


def _parse_transmission(transmission_str: str) -> TransmissionType:
    """Normalize Surprice transmission string to enum."""
    if "auto" in transmission_str.lower():
        return TransmissionType.AUTOMATIC
    return TransmissionType.MANUAL


def _split_vehicle_name(description: str) -> tuple[str, str]:
    """Split vehicle description into make and model, stripping 'or similar'."""
    cleaned = description
    # Remove "or similar" suffix
    for suffix in (" or similar", " Or Similar", " OR SIMILAR"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break

    parts = cleaned.split(" ", 1)
    make = parts[0].title() if parts else "Surprice"
    model = parts[1].title() if len(parts) > 1 else cleaned.title()
    return make, model


def _extract_coordinates(address: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from Surprice address, handling both coordinate formats.

    Locations endpoint uses {lat, lon} while search endpoint uses {latitude, longitude}.
    """
    coords = address.get("coordinates") or {}
    lat = coords.get("latitude") or coords.get("lat")
    lon = coords.get("longitude") or coords.get("lon")
    return (_safe_float(lat) if lat is not None else None, _safe_float(lon) if lon is not None else None)


@register_adapter
class SurpriceAdapter(BaseAdapter):
    supplier_id = "surprice"
    supplier_name = "Surprice Mobility"
    supports_one_way = True
    default_timeout = 30.0

    def _auth_headers(self) -> dict[str, str]:
        settings = get_settings()
        return {
            "Authorization": f"Bearer {settings.surprice_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _base_url(self) -> str:
        settings = get_settings()
        return settings.surprice_api_url.rstrip("/")

    # ─── Search ───────────────────────────────────────────────────────────

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        base_url = self._base_url()

        pickup_code, pickup_ext_code = self._resolve_location_codes(
            pickup_entry.pickup_id,
            pickup_entry.extended_location_code,
        )

        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            dropoff_code, dropoff_ext_code = self._resolve_location_codes(
                dropoff_entry.pickup_id,
                dropoff_entry.extended_dropoff_code or dropoff_entry.extended_location_code,
            )
        else:
            dropoff_code, dropoff_ext_code = pickup_code, pickup_ext_code

        pickup_dt = f"{request.pickup_date.isoformat()}T{request.pickup_time.strftime('%H:%M:%S')}"
        dropoff_dt = f"{request.dropoff_date.isoformat()}T{request.dropoff_time.strftime('%H:%M:%S')}"

        base_payload = {
            "pickUpDateTime": pickup_dt,
            "returnDateTime": dropoff_dt,
            "pickUpLocationCode": pickup_code,
            "pickUpExtendedLocationCode": pickup_ext_code,
            "returnLocationCode": dropoff_code,
            "returnExtendedLocationCode": dropoff_ext_code,
            "returnExtras": True,
        }
        if request.driver_age:
            base_payload["driverAge"] = request.driver_age

        cdw_rate_code = settings.surprice_rate_code or "Vrooem"
        fdw_rate_code = settings.surprice_fdw_rate_code or ""

        # Fetch CDW and FDW results in parallel
        cdw_task = self._fetch_availability(base_url, {**base_payload, "rateCode": cdw_rate_code})
        if fdw_rate_code:
            fdw_task = self._fetch_availability(base_url, {**base_payload, "rateCode": fdw_rate_code})
            cdw_data, fdw_data = await asyncio.gather(cdw_task, fdw_task)
        else:
            cdw_data = await cdw_task
            fdw_data = None

        if not cdw_data or not cdw_data.get("productOfferings"):
            return []

        rental_days = (request.dropoff_date - request.pickup_date).days or 1
        pickup_station = cdw_data.get("pickupStationInfo") or {}
        return_station = cdw_data.get("returnStationInfo") or {}

        # Build FDW lookup by SIPP code for merging
        fdw_by_sipp: dict[str, dict] = {}
        if fdw_data and fdw_data.get("productOfferings"):
            for offering in fdw_data["productOfferings"]:
                sipp = (offering.get("vehicle") or {}).get("code")
                if sipp:
                    fdw_by_sipp[sipp] = offering

        parse_kwargs = dict(
            rental_days=rental_days,
            request=request,
            pickup_entry=pickup_entry,
            dropoff_entry=dropoff_entry,
            pickup_station=pickup_station,
            return_station=return_station,
            pickup_code=pickup_code,
            pickup_ext_code=pickup_ext_code,
            dropoff_code=dropoff_code,
            dropoff_ext_code=dropoff_ext_code,
        )

        vehicles = []
        for offering in cdw_data["productOfferings"]:
            try:
                sipp = (offering.get("vehicle") or {}).get("code")
                fdw_offering = fdw_by_sipp.get(sipp) if sipp else None
                vehicle = self._parse_vehicle(
                    offering=offering,
                    fdw_offering=fdw_offering,
                    **parse_kwargs,
                )
                if vehicle:
                    vehicles.append(vehicle)
            except Exception:
                logger.warning("[surprice] Failed to parse vehicle offering", exc_info=True)
                continue

        logger.info(
            "[surprice] Parsed %d vehicles (CDW: %d, FDW matched: %d)",
            len(vehicles),
            len(cdw_data["productOfferings"]),
            len(fdw_by_sipp),
        )
        return vehicles

    async def _fetch_availability(self, base_url: str, payload: dict) -> dict | None:
        """Fetch availability for a single rate code. Returns parsed JSON or None."""
        rate_code = payload.get("rateCode", "")
        try:
            response = await self._request(
                "POST",
                f"{base_url}/v1/availability",
                json=payload,
                headers=self._auth_headers(),
            )
            if response.status_code != 200:
                body = response.text[:300]
                try:
                    data = response.json()
                except Exception:
                    data = None

                if response.status_code == 422 and isinstance(data, dict):
                    message = str(data.get("message") or "").strip()
                    code = data.get("code")
                    if code == 225 or "one way rentals not allowed" in message.lower():
                        raise SurpriceOneWayNotAllowedError(
                            message or "One-way rentals not allowed to this location"
                        )

                logger.warning("[surprice] %s search returned HTTP %d: %s", rate_code, response.status_code, body)
                return None
            data = response.json()
            return data if isinstance(data, dict) else None
        except SurpriceOneWayNotAllowedError:
            raise
        except Exception:
            logger.warning("[surprice] %s search request failed", rate_code, exc_info=True)
            return None

    def _parse_vehicle(
        self,
        offering: dict,
        rental_days: int,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
        pickup_station: dict,
        return_station: dict,
        pickup_code: str,
        pickup_ext_code: str,
        dropoff_code: str,
        dropoff_ext_code: str,
        fdw_offering: dict | None = None,
    ) -> Vehicle | None:
        vehicle_data = offering.get("vehicle") or {}
        rental_details = offering.get("rentalDetails") or []

        if not vehicle_data or not rental_details:
            return None

        # Use first rental detail (our Vrooem CDW rate)
        detail = rental_details[0]
        rate = detail.get("rentalRate") or {}
        total_charge = detail.get("totalCharge") or {}
        qualifier = rate.get("rateQualifier") or {}

        sipp_code = vehicle_data.get("code")
        description = vehicle_data.get("description") or "Surprice Vehicle"
        make, model = _split_vehicle_name(description)

        total_amount = _safe_float(total_charge.get("estimatedTotalAmount"))
        currency = total_charge.get("currencyCode") or "EUR"

        if total_amount <= 0:
            return None

        daily_rate = round(total_amount / rental_days, 2) if rental_days > 0 else total_amount

        vendor_rate_id = qualifier.get("vendorRateID") or ""
        rate_code = qualifier.get("rateCode") or ""

        # Mileage policy
        mileage_data = rate.get("mileagePolicy") or {}
        mileage_unlimited = mileage_data.get("unlimited", False)
        mileage_policy = MileagePolicy.UNLIMITED if mileage_unlimited else MileagePolicy.LIMITED

        mileage_limit_km = None
        if not mileage_unlimited:
            quantity = _safe_int(mileage_data.get("quantity"))
            if quantity > 0:
                mileage_limit_km = quantity

        # Transmission
        transmission = _parse_transmission(vehicle_data.get("transmissionType") or "Manual")

        # Fuel type from SIPP code
        fuel_type = _parse_fuel_from_sipp(sipp_code)

        # Image
        image_url = vehicle_data.get("pictureURL") or ""

        # Pickup location coordinates — handle both {lat,lon} and {latitude,longitude}
        pickup_address = pickup_station.get("address") or {}
        station_lat, station_lon = _extract_coordinates(pickup_address)

        # Fall back to entry coordinates if station doesn't have them
        if station_lat is None or station_lat == 0:
            station_lat = pickup_entry.latitude
        if station_lon is None or station_lon == 0:
            station_lon = pickup_entry.longitude

        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_station.get("name") or pickup_entry.original_name,
            city=(pickup_address.get("city") or "").title(),
            country_code=(pickup_address.get("country") or {}).get("code", ""),
            latitude=station_lat,
            longitude=station_lon,
            location_type="airport" if (pickup_station.get("stationType") or "").lower() == "airport" else "other",
            airport_code=pickup_code if len(pickup_code) == 3 else None,
        )

        # Dropoff location
        dropoff_loc = None
        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            return_address = return_station.get("address") or {}
            ret_lat, ret_lon = _extract_coordinates(return_address)
            dropoff_loc = VehicleLocation(
                supplier_location_id=dropoff_entry.pickup_id,
                name=return_station.get("name") or dropoff_entry.original_name,
                city=(return_address.get("city") or "").title(),
                country_code=(return_address.get("country") or {}).get("code", ""),
                latitude=ret_lat,
                longitude=ret_lon,
                location_type="airport" if (return_station.get("stationType") or "").lower() == "airport" else "other",
            )

        # Deposit and excess from CDW vehicle data
        deposit_amount = _safe_float(vehicle_data.get("insuranceDeposit")) or None
        excess_amount = _safe_float(vehicle_data.get("insuranceExcess"))

        # Fees from vehicleCharges (surcharges not included in rate)
        fees = self._parse_fees(rate.get("vehicleCharges") or [], currency)

        # VAT info
        vat_amount = _safe_float(total_charge.get("VAT"))
        vat_percentage = _safe_float(total_charge.get("VATPercentage"))

        # Insurance options — CDW (included in base price)
        insurance_data = rate.get("insurance") or {}
        insurance_options = []
        if insurance_data:
            insurance_options.append(
                InsuranceOption(
                    id=f"ins_{self.supplier_id}_cdw",
                    coverage_type=CoverageType.BASIC,
                    name=insurance_data.get("description") or "CDW",
                    daily_rate=0,
                    total_price=0,
                    currency=currency,
                    excess_amount=excess_amount if excess_amount > 0 else None,
                    included=True,
                    description=insurance_data.get("detailedDescription") or "",
                )
            )

        # FDW option — zero excess upgrade (from parallel FDW API call)
        fdw_supplier_data = {}
        if fdw_offering:
            fdw_vehicle = fdw_offering.get("vehicle") or {}
            fdw_details = fdw_offering.get("rentalDetails") or []
            if fdw_details:
                fdw_detail = fdw_details[0]
                fdw_rate = fdw_detail.get("rentalRate") or {}
                fdw_total_charge = fdw_detail.get("totalCharge") or {}
                fdw_qualifier = fdw_rate.get("rateQualifier") or {}
                fdw_insurance = fdw_rate.get("insurance") or {}
                fdw_total = _safe_float(fdw_total_charge.get("estimatedTotalAmount"))
                fdw_excess = _safe_float(fdw_vehicle.get("insuranceExcess"))
                fdw_deposit = _safe_float(fdw_vehicle.get("insuranceDeposit"))
                fdw_upgrade_cost = round(fdw_total - total_amount, 2) if fdw_total > 0 else 0
                fdw_daily_upgrade = round(fdw_upgrade_cost / rental_days, 2) if rental_days > 0 else fdw_upgrade_cost

                insurance_options.append(
                    InsuranceOption(
                        id=f"ins_{self.supplier_id}_fdw",
                        coverage_type=CoverageType.FULL,
                        name=fdw_insurance.get("description") or "Full Damage Waiver (0 Excess)",
                        daily_rate=fdw_daily_upgrade,
                        total_price=fdw_upgrade_cost,
                        currency=currency,
                        excess_amount=fdw_excess if fdw_excess > 0 else 0,
                        included=False,
                        description=fdw_insurance.get("detailedDescription") or "Zero excess — no deductible in case of damage or theft.",
                    )
                )

                fdw_supplier_data = {
                    "fdw_vendor_rate_id": fdw_qualifier.get("vendorRateID") or "",
                    "fdw_rate_code": fdw_qualifier.get("rateCode") or "",
                    "fdw_total_amount": round(fdw_total, 2),
                    "fdw_deposit_amount": fdw_deposit,
                    "fdw_excess_amount": fdw_excess,
                }

        # Extras
        extras = self._parse_extras(rate.get("extras") or [], currency)

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": sipp_code or "",
            "provider_rate_id": vendor_rate_id or None,
            "name": description,
            "category": category_from_sipp(sipp_code),
            "make": make,
            "model": model,
            "image_url": image_url,
            "pickup_location": pickup_loc,
            "dropoff_location": dropoff_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=round(total_amount, 2),
                daily_rate=daily_rate,
                price_includes_tax=True,
                fees=fees,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
                deposit_amount=deposit_amount,
                deposit_currency=currency if deposit_amount else None,
            ),
            "insurance_options": insurance_options,
            "extras": extras,
            "cancellation_policy": None,  # API does not return cancellation terms
            "supplier_data": {
                "vendor_rate_id": vendor_rate_id,
                "rate_code": rate_code,
                "pickup_code": pickup_code,
                "pickup_ext_code": pickup_ext_code,
                "dropoff_code": dropoff_code,
                "dropoff_ext_code": dropoff_ext_code,
                "sipp_code": sipp_code,
                "total_amount": round(total_amount, 2),
                "currency": currency,
                "net_amount": round(total_amount - vat_amount, 2) if vat_amount > 0 else None,
                "vat_amount": vat_amount,
                "vat_percentage": vat_percentage,
                "deposit_amount": _safe_float(vehicle_data.get("insuranceDeposit")),
                "excess_amount": excess_amount,
                "theft_excess": _safe_float(vehicle_data.get("theftExcess")),
                "pickup_station_name": pickup_station.get("name"),
                "pickup_additional_info": (pickup_station.get("additionalInfo") or {}).get("text"),
                "return_station_name": return_station.get("name"),
                "pickup_office": self._normalize_station(pickup_station),
                "dropoff_office": self._normalize_station(return_station),
                **fdw_supplier_data,
            },
            "min_driver_age": _safe_int(vehicle_data.get("minDriverAge")) or None,
            "max_driver_age": _safe_int(vehicle_data.get("maxDriverAge")) or None,
        }

        if vehicle_data.get("transmissionType"):
            vehicle_kwargs["transmission"] = _parse_transmission(vehicle_data.get("transmissionType") or "")

        if sipp_code:
            vehicle_kwargs["fuel_type"] = _parse_fuel_from_sipp(sipp_code)
            vehicle_kwargs["sipp_code"] = sipp_code

        if vehicle_data.get("passengerQuantity") not in (None, ""):
            vehicle_kwargs["seats"] = _safe_int(vehicle_data.get("passengerQuantity"))

        if vehicle_data.get("doorsNum") not in (None, ""):
            vehicle_kwargs["doors"] = _safe_int(vehicle_data.get("doorsNum"))

        if vehicle_data.get("suitcasesNum") not in (None, ""):
            vehicle_kwargs["bags_large"] = _safe_int(vehicle_data.get("suitcasesNum"))

        if vehicle_data.get("airConditionInd") is not None:
            vehicle_kwargs["air_conditioning"] = bool(vehicle_data.get("airConditionInd"))

        if mileage_data:
            vehicle_kwargs["mileage_policy"] = mileage_policy
            if mileage_limit_km is not None:
                vehicle_kwargs["mileage_limit_km"] = mileage_limit_km

        return Vehicle(**vehicle_kwargs)

    def _parse_extras(self, raw_extras: list[dict], currency: str) -> list[Extra]:
        """Parse Surprice extras into canonical Extra objects."""
        extras = []
        for ext in raw_extras:
            ext_description = ext.get("description") or ""
            detailed = ext.get("detailedDescription") or ext_description
            if not ext_description:
                continue

            calc_info = ext.get("calculationInfo") or {}
            amount = _safe_float(ext.get("amount"))
            unit_charge = _safe_float(calc_info.get("unitCharge"))
            is_per_day = (calc_info.get("unitName") or "") == "Day"

            extras.append(
                Extra(
                    id=f"ext_{self.supplier_id}_{ext_description}",
                    name=detailed,
                    daily_rate=unit_charge if is_per_day else amount,
                    total_price=amount,
                    currency=ext.get("currencyCode") or currency,
                    max_quantity=_safe_int(ext.get("allowQuantity"), 1) if ext.get("allowQuantity") else 1,
                    type=ExtraType.EQUIPMENT,
                    mandatory=False,
                    description=detailed if detailed != ext_description else None,
                    supplier_data={
                        "code": ext_description,
                        "per_day": is_per_day,
                        "unit_charge": unit_charge,
                        "allow_quantity": _safe_int(ext.get("allowQuantity"), 1) if ext.get("allowQuantity") else 1,
                        "purpose": ext.get("purpose") or None,
                    },
                )
            )
        return extras

    def _parse_fees(self, vehicle_charges: list[dict], currency: str) -> list[Fee]:
        """Parse vehicleCharges (surcharges) into Fee objects."""
        fees = []
        for charge in vehicle_charges:
            # Skip charges that are the base rate itself
            if charge.get("includedInRate", False):
                continue

            description = charge.get("description") or ""
            detailed = charge.get("detailedDescription") or description
            amount = _safe_float(charge.get("amount"))
            included_in_total = bool(charge.get("includedInEstTotalInd", False))

            if not description or amount == 0:
                continue

            fees.append(
                Fee(
                    name=detailed,
                    amount=amount,
                    currency=charge.get("currencyCode") or currency,
                    included_in_total=included_in_total,
                    description=description,
                )
            )
        return fees

    @staticmethod
    def _normalize_station(station: dict) -> dict | None:
        """Normalize a Surprice station object to the frontend office format."""
        if not station:
            return None
        address_obj = station.get("address") or {}
        address_lines = address_obj.get("addressLine") or []
        address = address_lines[0] if address_lines else ""
        name = station.get("name") or ""
        phone = station.get("telephone") or ""
        additional = station.get("additionalInfo") or {}
        instructions = additional.get("text") or ""
        if not name and not address and not phone:
            return None
        return {
            "name": name,
            "address": address,
            "town": (address_obj.get("city") or "").title(),
            "postal_code": address_obj.get("postalCode") or "",
            "phone": phone,
            "email": "",
            "pickup_instructions": instructions,
            "dropoff_instructions": "",
            "location_type": (station.get("stationType") or "other").lower(),
        }

    # ─── Booking ──────────────────────────────────────────────────────────

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        base_url = self._base_url()
        sd = vehicle.supplier_data

        # Build extras list for reservation payload
        extras_payload = []
        for extra in request.extras:
            extras_payload.append({
                "description": extra.extra_id.replace(f"ext_{self.supplier_id}_", ""),
                "quantity": extra.quantity,
            })

        payload = {
            "pickUpLocationCode": sd.get("pickup_code"),
            "pickUpExtendedLocationCode": sd.get("pickup_ext_code"),
            "returnLocationCode": sd.get("dropoff_code"),
            "returnExtendedLocationCode": sd.get("dropoff_ext_code"),
            "vendorRateID": sd.get("vendor_rate_id"),
            "rateCode": sd.get("rate_code"),
            "vehicleCode": sd.get("sipp_code"),
            "customer": {
                "firstName": request.driver.first_name,
                "lastName": request.driver.last_name,
                "email": request.driver.email,
                "phone": request.driver.phone,
            },
            "extras": extras_payload,
        }

        if request.driver.date_of_birth:
            payload["customer"]["dateOfBirth"] = request.driver.date_of_birth
        if request.driver.driving_license_number:
            payload["customer"]["drivingLicenseNumber"] = request.driver.driving_license_number
        if request.flight_number:
            payload["flightNumber"] = request.flight_number
        if request.special_requests:
            payload["specialRequests"] = request.special_requests

        response = await self._request(
            "POST",
            f"{base_url}/v1/reservation",
            json=payload,
            headers=self._auth_headers(),
        )

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
                supplier_data={"error": "Invalid response from Surprice"},
            )

        order_id = str(data.get("orderId") or data.get("reservationId") or data.get("id") or "")
        is_success = bool(order_id) and response.status_code in (200, 201)

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=order_id,
            status=BookingStatus.CONFIRMED if is_success else BookingStatus.FAILED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=data,
        )

    # ─── Cancel ───────────────────────────────────────────────────────────

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        base_url = self._base_url()

        payload: dict = {}
        if request.reason:
            payload["cancellation_reason"] = request.reason

        response = await self._request(
            "PUT",
            f"{base_url}/v1/reservation/{supplier_booking_id}/cancel",
            json=payload,
            headers=self._auth_headers(),
        )

        data = response.json() if response.status_code in (200, 201) else {}
        is_cancelled = response.status_code in (200, 201)

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED if is_cancelled else BookingStatus.FAILED,
            cancellation_fee=_safe_float((data or {}).get("cancellationFee")),
            cancellation_currency=(data or {}).get("currency", "EUR"),
            refund_amount=_safe_float((data or {}).get("refundAmount")),
            supplier_cancellation_id=str((data or {}).get("cancellationId", supplier_booking_id)),
        )

    # ─── Locations ────────────────────────────────────────────────────────

    async def get_locations(self) -> list[dict]:
        base_url = self._base_url()

        response = await self._request(
            "GET",
            f"{base_url}/v1/location/search",
            params={"limit": 500},
            headers=self._auth_headers(),
        )

        data = response.json()

        # API may return a flat array or a dict with a "results" key.
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results") or []
        else:
            return []

        if not isinstance(results, list):
            return []

        locations = []
        for loc in results:
            location_code = loc.get("locationCode") or ""
            ext_code = loc.get("extendedLocationCode") or location_code
            station_type = (loc.get("stationType") or "").lower()
            address = loc.get("address") or {}
            country = address.get("country") or {}

            lat, lon = _extract_coordinates(address)

            locations.append({
                "provider": self.supplier_id,
                # Store both codes joined by colon for later splitting
                "provider_location_id": f"{location_code}:{ext_code}",
                "name": loc.get("name") or "",
                "city": (address.get("city") or "").title(),
                "country": (country.get("name") or "").title(),
                "country_code": country.get("code") or "",
                "location_type": "airport" if station_type == "airport" else "office",
                "latitude": lat,
                "longitude": lon,
                "telephone": loc.get("telephone"),
                "is_meet_and_greet": loc.get("isMeetAndGreet", False),
                "additional_info": (loc.get("additionalInfo") or {}).get("text"),
            })

        logger.info("[surprice] Fetched %d locations", len(locations))
        return locations

    # ─── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _split_location_id(pickup_id: str) -> tuple[str, str]:
        """Split a combined 'locationCode:extendedLocationCode' into its parts.

        If no colon separator is found, uses the single value for both codes.
        """
        if ":" in pickup_id:
            parts = pickup_id.split(":", 1)
            return parts[0], parts[1]
        return pickup_id, pickup_id

    @classmethod
    def _resolve_location_codes(
        cls,
        pickup_id: str,
        extended_location_code: str | None = None,
    ) -> tuple[str, str]:
        """Resolve Surprice location + extended codes from either supported format."""
        if ":" in pickup_id:
            return cls._split_location_id(pickup_id)

        resolved_extended_code = (extended_location_code or "").strip() or pickup_id
        return pickup_id, resolved_extended_code
