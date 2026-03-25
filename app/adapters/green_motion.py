"""GreenMotion adapter — Custom XML API."""

import logging
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger(__name__)

from app.adapters.base import BaseAdapter
from app.adapters.registry import register_adapter
from app.core.config import get_settings
from app.schemas.common import (
    CoverageType,
    ExtraType,
    FuelType,
    MileagePolicy,
    TransmissionType,
    VehicleCategory,
    category_from_sipp,
)
from app.schemas.booking import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
)
from app.schemas.common import BookingStatus
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import Extra, InsuranceOption, Vehicle, VehicleLocation


def _xml_text(element: ET.Element | None, tag: str, default: str = "") -> str:
    """Safely extract text from an XML child element."""
    if element is None:
        return default
    child = element.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _xml_float(element: ET.Element | None, tag: str, default: float = 0.0) -> float:
    """Safely extract a float from an XML child element."""
    text = _xml_text(element, tag)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _xml_int(element: ET.Element | None, tag: str, default: int = 0) -> int:
    """Safely extract an int from an XML child element."""
    text = _xml_text(element, tag)
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _parse_fuel(fuel_str: str) -> FuelType:
    fuel_lower = fuel_str.lower()
    if "diesel" in fuel_lower or "dizel" in fuel_lower:
        return FuelType.DIESEL
    if "electric" in fuel_lower:
        return FuelType.ELECTRIC
    if "hybrid" in fuel_lower:
        return FuelType.HYBRID
    if "petrol" in fuel_lower or "gasoline" in fuel_lower or "benzin" in fuel_lower:
        return FuelType.PETROL
    if "lpg" in fuel_lower:
        return FuelType.LPG
    return FuelType.UNKNOWN


@register_adapter
class GreenMotionAdapter(BaseAdapter):
    supplier_id = "green_motion"
    supplier_name = "GreenMotion"
    supports_one_way = True
    default_timeout = 20.0

    def _api_url(self) -> str:
        return get_settings().greenmotion_api_url

    def _build_xml(self, request_type: str, body: str) -> str:
        settings = get_settings()
        return f"""<?xml version="1.0" encoding="utf-8"?>
<gm_webservice>
    <header>
        <username>{settings.greenmotion_username}</username>
        <password>{settings.greenmotion_password}</password>
        <version>1.5</version>
    </header>
    <request type="{request_type}">
        {body}
    </request>
</gm_webservice>"""

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()

        dropoff_tag = ""
        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            dropoff_tag = f"<dropoff_location_id>{dropoff_entry.pickup_id}</dropoff_location_id>"

        body = f"""
        <location_id>{pickup_entry.pickup_id}</location_id>
        {dropoff_tag}
        <start_date>{request.pickup_date.isoformat()}</start_date>
        <start_time>{request.pickup_time.strftime('%H:%M')}</start_time>
        <end_date>{request.dropoff_date.isoformat()}</end_date>
        <end_time>{request.dropoff_time.strftime('%H:%M')}</end_time>
        <age>{request.driver_age}</age>
        <currency>{request.currency}</currency>
        <language>en</language>
        <rentalCode>1</rentalCode>
        """

        xml_payload = self._build_xml("GetVehicles", body)
        response = await self._request(
            "POST",
            self._api_url(),
            content=xml_payload,
            headers={"Content-Type": "application/xml"},
        )

        return self._parse_vehicles(response.text, request, pickup_entry, dropoff_entry)

    def _parse_vehicles(
        self,
        xml_text: str,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> list[Vehicle]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning("[green_motion] XML parse error, response: %s", xml_text[:300])
            return []

        resp = root.find("response")
        if resp is None:
            logger.warning("[green_motion] No <response> element in XML: %s", xml_text[:300])
            return []

        quote_id = _xml_text(resp, "quoteid")
        rental_days = (request.dropoff_date - request.pickup_date).days or 1

        # Parse extras (shared across all vehicles in this response)
        # Element is <optionalextras>, not <extras>
        extras_elem = resp.find("optionalextras")
        if extras_elem is None:
            extras_elem = resp.find("extras")  # fallback for older API
        shared_extras = self._parse_extras(extras_elem, rental_days)

        # Vehicles are inside a <vehicles> wrapper element
        vehicles_elem = resp.find("vehicles")
        if vehicles_elem is None:
            # Fallback: try direct <vehicle> children (older API versions)
            vehicle_elems = resp.findall("vehicle")
        else:
            vehicle_elems = vehicles_elem.findall("vehicle")

        if not vehicle_elems:
            logger.warning("[green_motion] No <vehicle> elements found in response")
            return []

        logger.info("[green_motion] Found %d raw vehicles", len(vehicle_elems))

        vehicles = []
        for veh in vehicle_elems:
            try:
                vehicle = self._parse_single_vehicle(
                    veh, quote_id, rental_days, shared_extras, request, pickup_entry, dropoff_entry
                )
                if vehicle:
                    vehicles.append(vehicle)
            except Exception:
                logger.debug("[green_motion] Failed to parse vehicle", exc_info=True)
                continue

        return vehicles

    def _parse_single_vehicle(
        self,
        veh: ET.Element,
        quote_id: str,
        rental_days: int,
        shared_extras: list[Extra],
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> Vehicle | None:
        # name, id, image are XML ATTRIBUTES on <vehicle>, not child elements
        name = veh.get("name", "")
        if not name:
            return None

        vehicle_id = veh.get("id", "")
        sipp_code = _xml_text(veh, "acriss")
        transmission_str = _xml_text(veh, "transmission").lower()

        # Price is inside <product type="BAS"> elements, NOT a direct <total> child
        products = veh.findall("product")
        total_price = 0.0
        currency = request.currency
        deposit_amount = 0.0
        excess_amount = 0.0
        fuel_policy = ""
        minage = 0
        all_products: list[dict] = []

        if products:
            # Use the BAS (base rate) product, fallback to first product
            bas_product = products[0]
            for p in products:
                if p.get("type") == "BAS":
                    bas_product = p
                    break

            total_elem = bas_product.find("total")
            if total_elem is not None and total_elem.text:
                try:
                    total_price = float(total_elem.text)
                except ValueError:
                    pass
                currency = total_elem.get("currency", currency)

            deposit_amount = _xml_float(bas_product, "deposit")
            excess_amount = _xml_float(bas_product, "excess")
            fuel_policy = _xml_text(bas_product, "fuelpolicy")
            minage = _xml_int(bas_product, "minage")

            # Extract ALL products (BAS, PLU, PRE, PMP) for frontend package selector
            for p in products:
                p_total_elem = p.find("total")
                p_total = 0.0
                p_currency = currency
                if p_total_elem is not None and p_total_elem.text:
                    try:
                        p_total = float(p_total_elem.text)
                    except ValueError:
                        pass
                    p_currency = p_total_elem.get("currency", currency)
                all_products.append({
                    "type": p.get("type", "BAS"),
                    "total": p_total,
                    "currency": p_currency,
                    "deposit": _xml_float(p, "deposit"),
                    "excess": _xml_float(p, "excess"),
                    "fuelpolicy": _xml_text(p, "fuelpolicy"),
                    "mileage": _xml_int(p, "mileage"),
                    "costperextradistance": _xml_float(p, "costperextradistance"),
                    "minage": _xml_int(p, "minage"),
                    "debitcard": _xml_text(p, "debitcard"),
                })
        else:
            # Fallback: try direct <total> child (older API versions)
            total_elem = veh.find("total")
            if total_elem is not None and total_elem.text:
                try:
                    total_price = float(total_elem.text)
                except ValueError:
                    pass
                currency = total_elem.get("currency", currency)

        daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else total_price

        # Image URL is a percent-encoded XML ATTRIBUTE
        image_raw = veh.get("image", "")
        image_url = unquote(image_raw) if image_raw else ""

        # Mileage (inside <product> or direct child)
        mileage_limit = 0
        if products:
            mileage_limit = _xml_int(bas_product, "mileage")
        if mileage_limit == 0:
            mileage_limit = _xml_int(veh, "mileage")
        mileage_policy = MileagePolicy.LIMITED if mileage_limit > 0 else MileagePolicy.UNLIMITED

        # Per-vehicle extras override shared extras if present
        vehicle_extras_elem = veh.find("options")
        if vehicle_extras_elem is None:
            vehicle_extras_elem = veh.find("optionalextras")
        if vehicle_extras_elem is not None:
            vehicle_specific_extras = self._parse_extras(vehicle_extras_elem, rental_days)
            if vehicle_specific_extras:
                shared_extras = vehicle_specific_extras  # override with vehicle-specific

        # Per-vehicle insurance options
        vehicle_insurance: list[InsuranceOption] = []
        insurance_elem = veh.find("insurance_options")
        if insurance_elem is not None:
            for ins in insurance_elem.findall("insurance"):
                ins_id = _xml_text(ins, "optionID") or _xml_text(ins, "id")
                ins_name = _xml_text(ins, "Name") or _xml_text(ins, "name")
                ins_daily = _xml_float(ins, "Daily_rate") or _xml_float(ins, "price")
                ins_total = _xml_float(ins, "Total_for_this_booking") or _xml_float(ins, "total")
                ins_excess = _xml_float(ins, "excess")
                if ins_id and ins_name:
                    vehicle_insurance.append(InsuranceOption(
                        id=f"ins_{self.supplier_id}_{ins_id}",
                        coverage_type=CoverageType.FULL if "full" in ins_name.lower() else CoverageType.BASIC,
                        name=ins_name,
                        daily_rate=ins_daily,
                        total_price=ins_total,
                        excess_amount=ins_excess if ins_excess > 0 else None,
                        included=False,
                    ))

        # Derive make/model from name (e.g., "Volkswagen Up" → make="Volkswagen", model="Up")
        name_parts = name.split(" ", 1)
        make = name_parts[0].title() if name_parts else ""
        model = name_parts[1].title() if len(name_parts) > 1 else ""
        # Strip "or similar" variants
        for suffix in [" or similar model", " or similar"]:
            if model.lower().endswith(suffix):
                model = model[: -len(suffix)].strip()
                break

        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_entry.original_name,
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
        )

        # Use per-vehicle insurance if available, else fall back to excess-based default
        if vehicle_insurance:
            final_insurance = vehicle_insurance
        elif excess_amount > 0:
            final_insurance = [
                InsuranceOption(
                    id=f"ins_{self.supplier_id}_excess",
                    name="Standard Cover",
                    daily_rate=0,
                    total_price=0,
                    excess_amount=excess_amount,
                    included=True,
                    description=f"Excess: {excess_amount} {currency}",
                )
            ]
        else:
            final_insurance = []

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": vehicle_id,
            "name": name,
            "category": category_from_sipp(sipp_code),
            "make": make,
            "model": model,
            "image_url": image_url,
            "pickup_location": pickup_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=total_price,
                daily_rate=daily_rate,
                deposit_amount=deposit_amount if deposit_amount > 0 else None,
                deposit_currency=currency,
            ),
            "insurance_options": final_insurance,
            "extras": shared_extras,
            "cancellation_policy": None,  # API does not return cancellation terms
            "supplier_data": {
                "quote_id": quote_id,
                "vehicle_id": vehicle_id,
                "location_id": pickup_entry.pickup_id,
                "dropoff_location_id": dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id,
                "fuel_policy": fuel_policy or _xml_text(veh, "fuelpolicy"),
                "cost_per_extra_km": _xml_float(bas_product, "costperextradistance") if products else _xml_float(veh, "costperextradistance"),
                "start_date": request.pickup_date.isoformat(),
                "start_time": request.pickup_time.strftime("%H:%M"),
                "end_date": request.dropoff_date.isoformat(),
                "end_time": request.dropoff_time.strftime("%H:%M"),
                "products": all_products,
            },
            "min_driver_age": minage or None,
        }

        if transmission_str:
            if "auto" in transmission_str:
                vehicle_kwargs["transmission"] = TransmissionType.AUTOMATIC
            elif "manual" in transmission_str:
                vehicle_kwargs["transmission"] = TransmissionType.MANUAL

        fuel_text = _xml_text(veh, "fuel")
        if fuel_text:
            vehicle_kwargs["fuel_type"] = _parse_fuel(fuel_text)

        adults_text = _xml_text(veh, "adults")
        if adults_text:
            vehicle_kwargs["seats"] = _xml_int(veh, "adults")

        doors_text = _xml_text(veh, "doors")
        if doors_text:
            vehicle_kwargs["doors"] = _xml_int(veh, "doors")

        luggage_large_text = _xml_text(veh, "luggageLarge")
        if luggage_large_text:
            vehicle_kwargs["bags_large"] = _xml_int(veh, "luggageLarge")

        luggage_small_text = _xml_text(veh, "luggageSmall")
        if luggage_small_text:
            vehicle_kwargs["bags_small"] = _xml_int(veh, "luggageSmall")

        air_conditioning_text = _xml_text(veh, "airConditioning").lower()
        if air_conditioning_text in ("yes", "1", "true"):
            vehicle_kwargs["air_conditioning"] = True
        elif air_conditioning_text in ("no", "0", "false"):
            vehicle_kwargs["air_conditioning"] = False

        mileage_present = False
        if products and (bas_product.find("mileage") is not None) and _xml_text(bas_product, "mileage") != "":
            mileage_present = True
        if veh.find("mileage") is not None and _xml_text(veh, "mileage") != "":
            mileage_present = True
        if mileage_present:
            vehicle_kwargs["mileage_policy"] = mileage_policy
            if mileage_limit > 0:
                vehicle_kwargs["mileage_limit_km"] = mileage_limit

        if sipp_code:
            vehicle_kwargs["sipp_code"] = sipp_code

        return Vehicle(**vehicle_kwargs)

    def _parse_extras(self, extras_elem: ET.Element | None, rental_days: int) -> list[Extra]:
        if extras_elem is None:
            return []

        extras = []
        for ext in extras_elem.findall("extra"):
            # Real field names: <optionID>, <Name>, <Daily_rate>, <Total_for_this_booking>
            ext_id = _xml_text(ext, "optionID") or _xml_text(ext, "id")
            name = _xml_text(ext, "Name") or _xml_text(ext, "name")
            daily_rate = _xml_float(ext, "Daily_rate") or _xml_float(ext, "price")
            total_price = _xml_float(ext, "Total_for_this_booking") or _xml_float(ext, "total")

            if not ext_id or not name:
                continue

            extras.append(Extra(
                id=f"ext_{self.supplier_id}_{ext_id}",
                name=name,
                daily_rate=daily_rate,
                total_price=total_price,
                max_quantity=1,
                type=ExtraType.EQUIPMENT,
            ))

        return extras

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        settings = get_settings()
        sd = vehicle.supplier_data

        extras_xml = ""
        for extra in request.extras:
            raw_id = extra.extra_id
            prefix = f"ext_{self.supplier_id}_"
            if raw_id.startswith(prefix):
                raw_id = raw_id[len(prefix):]
            extras_xml += f'<option id="{raw_id}" option_qty="{extra.quantity}" option_total="0" />'

        dropoff_loc_xml = ""
        if sd.get("dropoff_location_id") and sd["dropoff_location_id"] != sd.get("location_id"):
            dropoff_loc_xml = f"<dropoff_location_id>{sd['dropoff_location_id']}</dropoff_location_id>"

        body = f"""
        <location_id>{sd['location_id']}</location_id>
        {dropoff_loc_xml}
        <start_date>{sd.get('start_date', '')}</start_date>
        <start_time>{sd.get('start_time', '')}</start_time>
        <end_date>{sd.get('end_date', '')}</end_date>
        <end_time>{sd.get('end_time', '')}</end_time>
        <vehicle_id>{sd['vehicle_id']}</vehicle_id>
        <vehicle_total>{vehicle.pricing.total_price}</vehicle_total>
        <currency>{vehicle.pricing.currency}</currency>
        <grand_total>{vehicle.pricing.total_price}</grand_total>
        <quoteid>{sd['quote_id']}</quoteid>
        <payment_type>POA</payment_type>
        <options>{extras_xml}</options>
        <cust_info>
            <firstname>{request.driver.first_name}</firstname>
            <lastname>{request.driver.last_name}</lastname>
            <age>{request.driver.age}</age>
            <email>{request.driver.email}</email>
            <telephone>{request.driver.phone}</telephone>
            <flight_no>{request.flight_number or ''}</flight_no>
            <address1>{request.driver.address}</address1>
            <city>{request.driver.city}</city>
            <postcode>{request.driver.postal_code}</postcode>
            <country>{request.driver.country}</country>
            <licno>{request.driver.driving_license_number or ''}</licno>
        </cust_info>
        """

        xml_payload = self._build_xml("MakeReservation", body)
        response = await self._request(
            "POST",
            self._api_url(),
            content=xml_payload,
            headers={"Content-Type": "application/xml"},
        )

        root = ET.fromstring(response.text)
        resp = root.find("response")
        booking_ref = _xml_text(resp, "booking_ref") if resp else ""

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_ref,
            status=BookingStatus.CONFIRMED if booking_ref else BookingStatus.FAILED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data={"booking_ref": booking_ref, "quote_id": sd.get("quote_id")},
        )

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()

        reason = _xml_escape(request.reason or "Cancelled by customer")

        # Try CancelReservation first, then CancelBooking as fallback
        for req_type in ("CancelReservation", "CancelBooking"):
            body = f"""
            <booking_ref>{supplier_booking_id}</booking_ref>
            <cancellationreason>{reason}</cancellationreason>
            """

            xml_payload = self._build_xml(req_type, body)
            try:
                response = await self._request(
                    "POST",
                    self._api_url(),
                    content=xml_payload,
                    headers={"Content-Type": "application/xml"},
                )
                # Check if response indicates success
                try:
                    root = ET.fromstring(response.text)
                    resp = root.find("response")
                    if resp is not None:
                        status = _xml_text(resp, "status")
                        if status.lower() in ("success", "cancelled", "ok", ""):
                            break  # success
                except ET.ParseError:
                    pass
            except Exception as exc:
                logger.warning("[green_motion] %s failed: %s, trying next", req_type, exc)
                continue

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
        )

    async def get_locations(self) -> list[dict]:
        settings = get_settings()

        # Step 1: Get countries
        xml_payload = self._build_xml("GetCountryList", "")
        response = await self._request(
            "POST",
            self._api_url(),
            content=xml_payload,
            headers={"Content-Type": "application/xml"},
        )

        root = ET.fromstring(response.text)
        resp = root.find("response")
        if resp is None:
            return []

        locations = []
        for country in resp.findall("country"):
            country_id = _xml_text(country, "countryID")
            country_name = _xml_text(country, "countryName")
            country_code = _xml_text(country, "iso_alpha2")

            # Step 2: Get locations for each country
            area_body = f"<country_id>{country_id}</country_id><language>en</language>"
            area_xml = self._build_xml("GetServiceAreas", area_body)
            area_response = await self._request(
                "POST",
                self._api_url(),
                content=area_xml,
                headers={"Content-Type": "application/xml"},
            )

            area_root = ET.fromstring(area_response.text)
            area_resp = area_root.find("response")
            if area_resp is None:
                continue

            for area in area_resp.findall("service_area"):
                locations.append({
                    "provider": self.supplier_id,
                    "provider_location_id": _xml_text(area, "location_id"),
                    "name": _xml_text(area, "location_name"),
                    "country": country_name,
                    "country_code": country_code,
                })

        return locations
