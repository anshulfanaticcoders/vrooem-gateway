"""OK Mobility adapter — SOAP 1.1/1.2 XML API."""

import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape

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
    CancellationPolicy,
    Extra,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)

# SOAP namespaces used by OK Mobility

_OK_MOBILITY_COUNTRY_MAP = {
    "1": ("Spain", "ES"),
    "2": ("France", "FR"),
    "4": ("Italy", "IT"),
    "6": ("United States", "US"),
    "10": ("Germany", "DE"),
    "12": ("Albania", "AL"),
    "62": ("Croatia", "HR"),
    "64": ("Cyprus", "CY"),
    "88": ("Gambia", "GM"),
    "94": ("Greece", "GR"),
    "146": ("Malta", "MT"),
    "157": ("Montenegro", "ME"),
    "159": ("Morocco", "MA"),
    "186": ("Poland", "PL"),
    "187": ("Portugal", "PT"),
    "201": ("Senegal", "SN"),
    "202": ("Serbia", "RS"),
    "232": ("Tunisia", "TN"),
    "233": ("Turkey", "TR"),
    "239": ("United Arab Emirates", "AE"),
}
NS_SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
NS_SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
NS_OK = "http://www.OKGroup.es/RentaCarWebService/getWSDL"
NS_TEMPURI = "http://tempuri.org/"


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
    """4th char of SIPP for fuel/AC. R=petrol+AC, N=petrol no-AC, D=diesel+AC, etc."""
    if len(sipp) < 4:
        return None
    ch = sipp[3].upper()
    if ch in ("R", "N", "S"):
        return FuelType.PETROL
    if ch in ("D", "Q"):
        return FuelType.DIESEL
    if ch in ("E", "C"):
        return FuelType.ELECTRIC
    if ch in ("H", "I"):
        return FuelType.HYBRID
    return None


def _parse_ac_from_sipp(sipp: str) -> bool | None:
    """4th char of SIPP: N/S = no AC; all other known chars = AC. Returns None when < 4 chars."""
    if len(sipp) < 4:
        return None
    return sipp[3].upper() not in ("N", "S")


def _strip_ns(tag: str) -> str:
    """Strip namespace prefix from an XML tag: {ns}local -> local."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _elem_to_dict(elem: ET.Element) -> dict:
    """Recursively convert an XML element to a dict, handling attributes."""
    result: dict = {}
    if elem.attrib:
        result["@attributes"] = dict(elem.attrib)
    for child in elem:
        key = _strip_ns(child.tag)
        child_dict = _elem_to_dict(child)
        # If child has only text and no children/attribs, store as string
        if not child_dict and child.text and child.text.strip():
            value = child.text.strip()
        elif child.text and child.text.strip() and not list(child):
            value = child.text.strip()
            if child_dict.get("@attributes"):
                child_dict["#text"] = value
                value = child_dict
        else:
            value = child_dict if child_dict else (child.text.strip() if child.text and child.text.strip() else "")

        if key in result:
            existing = result[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[key] = [existing, value]
        else:
            result[key] = value
    return result


def _find_elements(root: ET.Element, local_name: str) -> list[ET.Element]:
    """Find all elements matching a local name regardless of namespace."""
    results = []
    for elem in root.iter():
        if _strip_ns(elem.tag) == local_name:
            results.append(elem)
    return results


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _map_extra_type(extra_data: dict) -> ExtraType:
    """Determine extra type from OK Mobility extra data."""
    is_insurance = extra_data.get("insurance", "false")
    if isinstance(is_insurance, str) and is_insurance.lower() in ("true", "1"):
        return ExtraType.INSURANCE
    name = (extra_data.get("extra") or extra_data.get("name") or "").lower()
    if "insurance" in name or "cover" in name or "protection" in name:
        return ExtraType.INSURANCE
    if "fee" in name or "charge" in name or "tax" in name:
        return ExtraType.FEE
    return ExtraType.EQUIPMENT


@register_adapter
class OkMobilityAdapter(BaseAdapter):
    supplier_id = "ok_mobility"
    supplier_name = "OK Mobility"
    supports_one_way = False  # OK Mobility typically same-station returns
    default_timeout = 30.0

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        # OK Mobility's SSL cert sometimes causes issues; disable verification
        self.http_client = http_client or httpx.AsyncClient(
            timeout=self.default_timeout, verify=False
        )

    # ─── SOAP Envelope Builders ───

    def _soap11_envelope(self, body_xml: str) -> str:
        """Wrap body XML in a SOAP 1.1 envelope with OK namespace."""
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:get="http://www.OKGroup.es/RentaCarWebService/getWSDL">'
            "<soapenv:Header/>"
            "<soapenv:Body>"
            f"{body_xml}"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )

    def _soap12_envelope(self, body_xml: str) -> str:
        """Wrap body XML in a SOAP 1.2 envelope."""
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<soap:Body>"
            f"{body_xml}"
            "</soap:Body>"
            "</soap:Envelope>"
        )

    # ─── HTTP Helpers ───

    async def _soap_request(
        self,
        endpoint: str,
        action: str,
        xml_body: str,
        *,
        prefer_soap12: bool = False,
    ) -> str:
        """Send a SOAP request with port failover.

        OK Mobility supports both port 60060 and 30060, and both SOAP 1.1 and 1.2.
        We try SOAP 1.2 first when prefer_soap12 is True, then fallback to 1.1.
        """
        settings = get_settings()
        base_url = settings.okmobility_api_url.rstrip("/")

        # Port failover: try configured port first, then alternate (60060 ↔ 30060)
        urls = [f"{base_url}/{endpoint}"]
        if ":60060" in base_url:
            urls.append(f"{base_url.replace(':60060', ':30060')}/{endpoint}")
        elif ":30060" in base_url:
            urls.append(f"{base_url.replace(':30060', ':60060')}/{endpoint}")

        url = urls[0]

        if prefer_soap12:
            try:
                response = await self._request(
                    "POST",
                    url,
                    content=xml_body,
                    headers={
                        "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
                    },
                )
                text = response.text
                if "<VehicleModel>" in text or "<vehicleModel>" in text or "<getMultiplePrice" in text:
                    return text
            except Exception:
                logger.debug("[ok_mobility] SOAP 1.2 attempt failed for %s", url)

        # Fallback: SOAP 1.1 with port failover
        for try_url in urls:
            try:
                response = await self._request(
                    "POST",
                    try_url,
                    content=xml_body,
                    headers={
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": action,
                    },
                )
                return response.text
            except Exception:
                logger.warning("[ok_mobility] SOAP 1.1 attempt failed for %s", try_url)

        logger.error("[ok_mobility] All SOAP attempts failed for endpoint %s", endpoint)
        return ""

    # ─── search_vehicles ───

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()

        pickup_code = pickup_entry.pickup_id
        dropoff_code = dropoff_entry.pickup_id if dropoff_entry else pickup_code

        pickup_dt = f"{request.pickup_date.isoformat()} {request.pickup_time.strftime('%H:%M')}:00"
        dropoff_dt = f"{request.dropoff_date.isoformat()} {request.dropoff_time.strftime('%H:%M')}:00"

        # Build SOAP 1.2 body for getMultiplePrices
        soap12_body = self._soap12_envelope(
            f'<getMultiplePrices xmlns="http://tempuri.org/">'
            f"<Value>"
            f"<companyCode>{escape(settings.okmobility_company_code)}</companyCode>"
            f"<customerCode>{escape(settings.okmobility_customer_code)}</customerCode>"
            f"<onlyDynamicRate>false</onlyDynamicRate>"
            f"<PickUpDate>{pickup_dt}</PickUpDate>"
            f"<PickUpStation>{escape(pickup_code)}</PickUpStation>"
            f"<DropOffDate>{dropoff_dt}</DropOffDate>"
            f"<DropOffStation>{escape(dropoff_code)}</DropOffStation>"
            f"<extendedModel>true</extendedModel>"
            f"</Value>"
            f"</getMultiplePrices>"
        )

        # Build SOAP 1.1 body as fallback
        soap11_body = self._soap11_envelope(
            f"<get:getMultiplePricesRequest>"
            f"<get:objRequest>"
            f"<get:customerCode>{escape(settings.okmobility_customer_code)}</get:customerCode>"
            f"<get:companyCode>{escape(settings.okmobility_company_code)}</get:companyCode>"
            f"<get:pickUp>"
            f"<get:Date>{pickup_dt}</get:Date>"
            f"<get:rentalStation>{escape(pickup_code)}</get:rentalStation>"
            f"</get:pickUp>"
            f"<get:dropOff>"
            f"<get:Date>{dropoff_dt}</get:Date>"
            f"<get:rentalStation>{escape(dropoff_code)}</get:rentalStation>"
            f"</get:dropOff>"
            f"<get:extendedModel>true</get:extendedModel>"
            f"</get:objRequest>"
            f"</get:getMultiplePricesRequest>"
        )

        # Match PHP flow: try SOAP 1.2 on port 30060 first, then SOAP 1.1
        settings_url = settings.okmobility_api_url.rstrip("/")

        # PHP forces SOAP 1.2 to port 30060
        soap12_url = settings_url
        if ":60060" in soap12_url:
            soap12_url = soap12_url.replace(":60060", ":30060")

        # Step 1: Try SOAP 1.2 (single attempt, no fallback)
        xml_text = ""
        try:
            response = await self._request(
                "POST",
                f"{soap12_url}/getMultiplePrices",
                content=soap12_body,
                headers={
                    "Content-Type": 'application/soap+xml; charset=utf-8; action="http://tempuri.org/getMultiplePrices"',
                },
            )
            text = response.text
            if "<VehicleModel>" in text or "<vehicleModel>" in text:
                xml_text = text
        except Exception:
            logger.debug("[ok_mobility] SOAP 1.2 attempt failed")

        # Step 2: If SOAP 1.2 didn't return vehicles, try SOAP 1.1 with correct body
        if not xml_text:
            xml_text = await self._soap_request(
                "getMultiplePrices",
                "getMultiplePrices",
                soap11_body,
            )

        if not xml_text:
            return []

        return self._parse_vehicles(xml_text, request, pickup_entry, dropoff_entry)

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
            logger.warning("[ok_mobility] Failed to parse XML response")
            return []

        # Find all getMultiplePrice elements regardless of namespace
        vehicle_elements = _find_elements(root, "getMultiplePrice")
        if not vehicle_elements:
            logger.info("[ok_mobility] No vehicle elements found in XML response")
            return []

        rental_days = (request.dropoff_date - request.pickup_date).days or 1
        vehicles: list[Vehicle] = []

        for veh_elem in vehicle_elements:
            try:
                vehicle = self._parse_single_vehicle(
                    veh_elem, rental_days, request, pickup_entry, dropoff_entry
                )
                if vehicle:
                    vehicles.append(vehicle)
            except Exception:
                logger.debug("[ok_mobility] Failed to parse vehicle element", exc_info=True)
                continue

        return vehicles

    def _parse_single_vehicle(
        self,
        elem: ET.Element,
        rental_days: int,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> Vehicle | None:
        # Convert XML element to dict for easier field access
        data = _elem_to_dict(elem)

        # Extract core fields
        group_id = data.get("GroupID") or data.get("groupID") or data.get("GroupCode") or ""
        sipp = data.get("SIPP") or data.get("sipp") or ""
        acriss = data.get("AcrissCode") or data.get("acrissCode") or sipp
        token = data.get("token") or ""
        vehicle_model = data.get("VehicleModel") or data.get("vehicleModel") or ""
        group_name = data.get("Group_Name") or data.get("group_Name") or ""
        image_url = data.get("imageURL") or data.get("imageUrl") or ""

        # Display name: prefer VehicleModel, then Group_Name
        display_name = vehicle_model or group_name or group_id
        if not display_name:
            return None

        # Generate fallback image URL from OK Mobility website
        if not image_url and group_id:
            image_url = f"https://www.okmobility.com/alquilar/images/grupos/{group_id}.png"

        # Pricing
        total_price = _safe_float(data.get("previewValue") or data.get("PreviewValue"))
        if total_price <= 0:
            # Try dayValue * rental_days as fallback
            day_value = _safe_float(data.get("dayValue") or data.get("DayValue"))
            total_price = round(day_value * rental_days, 2) if day_value > 0 else 0.0

        if total_price <= 0:
            return None

        daily_rate = round(total_price / rental_days, 2) if rental_days > 0 else total_price
        currency = "EUR"  # OK Mobility always uses EUR

        # Value without tax for fee breakdown
        value_without_tax = _safe_float(data.get("valueWithoutTax") or data.get("ValueWithoutTax"))
        tax_rate = _safe_float(data.get("taxRate") or data.get("TaxRate"))
        fees: list[Fee] = []
        if value_without_tax > 0 and tax_rate > 0:
            tax_amount = round(total_price - value_without_tax, 2)
            if tax_amount > 0:
                fees.append(Fee(
                    name=f"Tax ({int(tax_rate)}%)",
                    amount=tax_amount,
                    currency=currency,
                    included_in_total=True,
                ))

        # Deposit from PrepayValue
        deposit = _safe_float(data.get("PrepayValue") or data.get("prepayValue"))

        # Mileage
        kms_included = (data.get("kmsIncluded") or data.get("KmsIncluded") or "false")
        if isinstance(kms_included, str):
            unlimited = kms_included.lower() in ("true", "1", "yes")
        else:
            unlimited = bool(kms_included)
        mileage_policy = MileagePolicy.UNLIMITED if unlimited else MileagePolicy.LIMITED

        # Transmission and fuel from SIPP/ACRISS code
        sipp_for_parse = acriss or sipp

        # Derive make/model from display name
        name_parts = display_name.split(" ", 1)
        make = name_parts[0].title() if name_parts else ""
        model_str = name_parts[1] if len(name_parts) > 1 else ""
        # Strip "or similar" suffix
        if model_str.lower().endswith(" or similar"):
            model_str = model_str[: -len(" or similar")].strip()

        # Pickup location
        station_name = data.get("Station") or data.get("station") or ""
        station_name_pick = data.get("StationNamePick") or data.get("stationNamePick") or station_name
        iata_code = data.get("IataCodePick") or data.get("iataCodePick") or None

        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=station_name_pick or pickup_entry.original_name,
            city=data.get("CityPick") or data.get("cityPick") or "",
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
            location_type="airport" if iata_code else "other",
            airport_code=iata_code,
        )

        # Cancellation policy from RateRestriction
        cancellation = self._parse_cancellation(data)

        # Extras
        extras = self._parse_extras(data, rental_days)

        # Rate code for booking
        rate_code = data.get("rateCode") or data.get("RateCode") or ""

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": f"{group_id}_{token[:16]}" if token else group_id,
            "provider_product_id": group_id or None,
            "provider_rate_id": rate_code or token or None,
            "availability_status": "available",
            "name": display_name,
            "category": category_from_sipp(acriss or sipp),
            "make": make,
            "model": model_str,
            "image_url": image_url,
            "pickup_location": pickup_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=total_price,
                daily_rate=daily_rate,
                fees=fees,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
                deposit_amount=deposit if deposit > 0 else None,
                deposit_currency=currency if deposit > 0 else None,
            ),
            "extras": extras,
            "cancellation_policy": cancellation,
            "supplier_data": {
                "token": token,
                "group_id": group_id,
                "sipp": sipp,
                "acriss_code": acriss,
                "rate_code": rate_code,
                "station_id": data.get("stationID") or data.get("StationID") or "",
                "station_name": station_name,
                "pickup_station_id": pickup_entry.pickup_id,
                "dropoff_station_id": (dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id),
                "preview_value": total_price,
                "value_without_tax": value_without_tax,
                "tax_rate": tax_rate,
                "dynamic_rate": data.get("dynamicRate") or data.get("DynamicRate") or "false",
                "pickup_datetime": f"{request.pickup_date.isoformat()} {request.pickup_time.strftime('%H:%M')}:00",
                "dropoff_datetime": f"{request.dropoff_date.isoformat()} {request.dropoff_time.strftime('%H:%M')}:00",
                # Frontend display fields
                "pickup_station_name": station_name_pick,
                "dropoff_station_name": data.get("StationNameDrop") or data.get("stationNameDrop") or station_name_pick,
                "pickup_address": data.get("StationAddressPick") or data.get("stationAddressPick") or "",
                "dropoff_address": data.get("StationAddressDrop") or data.get("stationAddressDrop") or "",
                "fuel_policy": data.get("FuelPolicy") or data.get("fuelPolicy") or None,
                "extras_included": data.get("ExtrasIncluded") or data.get("extrasIncluded") or "",
                "extras_required": data.get("ExtrasRequired") or data.get("extrasRequired") or "",
                "extras_available": data.get("ExtrasAvailable") or data.get("extrasAvailable") or "",
                "week_day_open": data.get("weekDayOpen") or None,
                "week_day_close": data.get("weekDayClose") or None,
            },
        }

        if sipp_for_parse:
            transmission = _parse_transmission_from_sipp(sipp_for_parse)
            fuel_type = _parse_fuel_from_sipp(sipp_for_parse)
            ac = _parse_ac_from_sipp(sipp_for_parse)
            if transmission is not None:
                vehicle_kwargs["transmission"] = transmission
            if fuel_type is not None:
                vehicle_kwargs["fuel_type"] = fuel_type
            if ac is not None:
                vehicle_kwargs["air_conditioning"] = ac
            vehicle_kwargs["sipp_code"] = sipp or acriss or None

        kms_included_raw = data.get("kmsIncluded") or data.get("KmsIncluded")
        if kms_included_raw not in (None, ""):
            vehicle_kwargs["mileage_policy"] = mileage_policy

        return Vehicle(**vehicle_kwargs)

    def _parse_cancellation(self, data: dict) -> CancellationPolicy:
        """Parse cancellation policy from RateRestriction element."""
        rate_restriction = data.get("RateRestriction") or data.get("rateRestriction")
        if not rate_restriction or not isinstance(rate_restriction, dict):
            return CancellationPolicy(free_cancellation=True)

        attrs = rate_restriction.get("@attributes", rate_restriction)

        cancellation_available = (attrs.get("CancellationAvailable") or "false").lower() in ("true", "1")
        cancellation_penalty = (attrs.get("CancellationPenaltyInd") or "false").lower() in ("true", "1")
        cancellation_amount = _safe_float(attrs.get("Amount"))
        cancellation_currency = attrs.get("Currency") or "EUR"
        deadline_str = attrs.get("DateTime") or ""

        free_cancellation = cancellation_available and not cancellation_penalty and cancellation_amount == 0

        # Parse deadline datetime
        free_until = None
        if deadline_str:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    free_until = datetime.strptime(deadline_str, fmt)
                    break
                except ValueError:
                    continue

        description = ""
        if free_cancellation and free_until:
            description = f"Free cancellation until {free_until.strftime('%Y-%m-%d %H:%M')}"
        elif cancellation_amount > 0:
            description = f"Cancellation fee: {cancellation_amount} {cancellation_currency}"

        return CancellationPolicy(
            free_cancellation=free_cancellation,
            free_cancellation_until=free_until,
            cancellation_fee=cancellation_amount if cancellation_amount > 0 else None,
            cancellation_fee_currency=cancellation_currency,
            description=description,
        )

    def _parse_extras(self, data: dict, rental_days: int) -> list[Extra]:
        """Parse extras from allExtras element in vehicle data."""
        all_extras = data.get("allExtras") or data.get("AllExtras")
        if not all_extras or not isinstance(all_extras, dict):
            return []

        raw_extras = all_extras.get("allExtra") or all_extras.get("AllExtra")
        if not raw_extras:
            return []

        # Normalize to list (single extra comes as dict, multiple as list)
        if isinstance(raw_extras, dict):
            raw_extras = [raw_extras]
        if not isinstance(raw_extras, list):
            return []

        extras: list[Extra] = []
        for ext in raw_extras:
            if not isinstance(ext, dict):
                continue

            extra_id = ext.get("extraID") or ext.get("ExtraID") or ""
            name = ext.get("extra") or ext.get("Extra") or ext.get("name") or ""
            if not extra_id or not name:
                continue

            price = _safe_float(ext.get("value") or ext.get("Value"))
            price_with_tax = _safe_float(ext.get("valueWithTax") or ext.get("ValueWithTax"))
            is_per_contract = (ext.get("pricePerContract") or ext.get("PricePerContract") or "false")
            if isinstance(is_per_contract, str):
                is_per_contract = is_per_contract.lower() in ("true", "1")

            is_included = (ext.get("extra_Included") or ext.get("Extra_Included") or "false")
            if isinstance(is_included, str):
                is_included = is_included.lower() in ("true", "1")

            is_required = (ext.get("extra_Required") or ext.get("Extra_Required") or "false")
            if isinstance(is_required, str):
                is_required = is_required.lower() in ("true", "1")

            # Use price with tax as the canonical price
            effective_price = price_with_tax if price_with_tax > 0 else price

            if is_per_contract:
                total_price = effective_price
                daily = round(total_price / rental_days, 2) if rental_days > 0 else total_price
            else:
                daily = effective_price
                total_price = round(effective_price * rental_days, 2)

            description = ext.get("description") or ext.get("Description") or None

            code = ext.get("code") or ext.get("Code") or extra_id

            extras.append(Extra(
                id=f"ext_{self.supplier_id}_{extra_id}",
                name=name,
                daily_rate=daily,
                total_price=total_price,
                max_quantity=1,
                type=_map_extra_type(ext),
                mandatory=is_included or is_required,
                description=description,
                supplier_data={
                    "extraID": extra_id,
                    "code": code,
                    "extra": name,
                    "value": str(price),
                    "valueWithTax": str(price_with_tax),
                    "pricePerContract": "true" if is_per_contract else "false",
                    "extra_Included": "true" if is_included else "false",
                    "extra_Required": "true" if is_required else "false",
                },
            ))

        return extras

    # ─── create_booking ───

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        settings = get_settings()
        sd = vehicle.supplier_data

        # Build extras string: comma-separated extra IDs
        extras_str = ""
        if request.extras:
            extra_ids = []
            for e in request.extras:
                # Strip our prefix to get the original OK Mobility extra ID
                raw_id = e.extra_id
                prefix = f"ext_{self.supplier_id}_"
                if raw_id.startswith(prefix):
                    raw_id = raw_id[len(prefix):]
                extra_ids.append(raw_id)
            extras_str = ",".join(extra_ids)

        body_xml = (
            "<get:createReservation>"
            "<get:objRequest>"
            f"<get:customerCode>{escape(settings.okmobility_customer_code)}</get:customerCode>"
            f"<get:companyCode>{escape(settings.okmobility_company_code)}</get:companyCode>"
            f"<get:rateCode>{escape(sd.get('rate_code', ''))}</get:rateCode>"
            "<get:MessageType>N</get:MessageType>"
            f"<get:Reference>{escape(str(request.laravel_booking_id or ''))}</get:Reference>"
            f"<get:token>{escape(sd.get('token', ''))}</get:token>"
            f"<get:groupCode>{escape(sd.get('group_id', '') or sd.get('sipp', ''))}</get:groupCode>"
            "<get:PickUp>"
            f"<get:Date>{escape(sd.get('pickup_datetime', ''))}</get:Date>"
            f"<get:rentalStation>{escape(sd.get('pickup_station_id', ''))}</get:rentalStation>"
            f"<get:Place></get:Place>"
            f"<get:Flight>{escape(request.flight_number or '')}</get:Flight>"
            "</get:PickUp>"
            "<get:DropOff>"
            f"<get:Date>{escape(sd.get('dropoff_datetime', ''))}</get:Date>"
            f"<get:rentalStation>{escape(sd.get('dropoff_station_id', ''))}</get:rentalStation>"
            "</get:DropOff>"
            "<get:Driver>"
            f"<get:Name>{escape(request.driver.first_name)} {escape(request.driver.last_name)}</get:Name>"
            f"<get:Address>{escape(request.driver.address)}</get:Address>"
            f"<get:City>{escape(request.driver.city)}</get:City>"
            f"<get:Postal_code>{escape(request.driver.postal_code)}</get:Postal_code>"
            f"<get:Phone>{escape(request.driver.phone)}</get:Phone>"
            f"<get:DriverLicenceNumber>{escape(request.driver.driving_license_number or '')}</get:DriverLicenceNumber>"
            f"<get:EMail>{escape(request.driver.email)}</get:EMail>"
            f"<get:Country>{escape(request.driver.country)}</get:Country>"
            f"<get:Date_of_Birth>{escape(request.driver.date_of_birth or '')}</get:Date_of_Birth>"
            "</get:Driver>"
            f"<get:Observations>{escape(request.special_requests)}</get:Observations>"
            f"<get:Extras>{escape(extras_str)}</get:Extras>"
            "</get:objRequest>"
            "</get:createReservation>"
        )

        soap_xml = self._soap11_envelope(body_xml)

        response_text = await self._soap_request(
            "createReservation",
            "createReservation",
            soap_xml,
        )

        # Parse response
        booking_ref = ""
        supplier_data: dict = {}
        if response_text:
            try:
                root = ET.fromstring(response_text)
                # Look for reservation number / confirmation
                for tag in ("reservationNumber", "ReservationNumber", "confirmationNumber", "ConfirmationNumber", "bookingReference"):
                    elements = _find_elements(root, tag)
                    if elements and elements[0].text:
                        booking_ref = elements[0].text.strip()
                        break

                # Also check for error
                error_elems = _find_elements(root, "errorCode")
                error_code = ""
                if error_elems and error_elems[0].text:
                    error_code = error_elems[0].text.strip()

                error_msg_elems = _find_elements(root, "errorMessage")
                error_msg = ""
                if error_msg_elems and error_msg_elems[0].text:
                    error_msg = error_msg_elems[0].text.strip()

                supplier_data = {
                    "booking_ref": booking_ref,
                    "error_code": error_code,
                    "error_message": error_msg,
                    "token": sd.get("token"),
                    "group_id": sd.get("group_id"),
                }
            except ET.ParseError:
                logger.error("[ok_mobility] Failed to parse createReservation response")

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=booking_ref,
            status=BookingStatus.CONFIRMED if booking_ref else BookingStatus.FAILED,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=supplier_data,
        )

    # ─── cancel_booking ───

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()

        body_xml = (
            "<get:cancelReservation>"
            "<get:objRequest>"
            f"<get:customerCode>{escape(settings.okmobility_customer_code)}</get:customerCode>"
            f"<get:companyCode>{escape(settings.okmobility_company_code)}</get:companyCode>"
            f"<get:reservationNumber>{escape(supplier_booking_id)}</get:reservationNumber>"
            "</get:objRequest>"
            "</get:cancelReservation>"
        )

        soap_xml = self._soap11_envelope(body_xml)

        response_text = await self._soap_request(
            "cancelReservation",
            "cancelReservation",
            soap_xml,
        )

        # Parse cancellation result
        cancellation_id = supplier_booking_id
        status = BookingStatus.CANCELLED
        cancellation_fee = 0.0

        if response_text:
            try:
                root = ET.fromstring(response_text)
                error_elems = _find_elements(root, "errorCode")
                if error_elems and error_elems[0].text:
                    error_code = error_elems[0].text.strip()
                    if error_code and error_code != "SUCCESS":
                        logger.warning("[ok_mobility] Cancel returned error: %s", error_code)
                        status = BookingStatus.FAILED
            except ET.ParseError:
                logger.error("[ok_mobility] Failed to parse cancelReservation response")

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=status,
            cancellation_fee=cancellation_fee,
            supplier_cancellation_id=cancellation_id,
        )

    # ─── get_locations ───

    async def get_locations(self) -> list[dict]:
        settings = get_settings()

        body_xml = (
            "<get:getStationsRequest>"
            "<objRequest>"
            f"<customerCode>{escape(settings.okmobility_customer_code)}</customerCode>"
            f"<companyCode>{escape(settings.okmobility_company_code)}</companyCode>"
            "</objRequest>"
            "</get:getStationsRequest>"
        )

        soap_xml = self._soap11_envelope(body_xml)

        response_text = await self._soap_request(
            "getStations",
            "getStationsOperation",
            soap_xml,
        )

        if not response_text:
            return []

        try:
            root = ET.fromstring(response_text)
        except ET.ParseError:
            logger.warning("[ok_mobility] Failed to parse getStations response")
            return []

        # Find all RentalStation elements regardless of namespace
        station_elements = _find_elements(root, "RentalStation")
        if not station_elements:
            # Try alternative element names
            station_elements = _find_elements(root, "rentalStation")
        if not station_elements:
            station_elements = _find_elements(root, "Station")

        locations: list[dict] = []
        for station in station_elements:
            data = _elem_to_dict(station)

            station_id = data.get("StationID") or data.get("stationID") or data.get("stationId") or ""
            name = data.get("Name") or data.get("name") or data.get("Station") or data.get("station") or ""
            if not station_id or not name:
                continue

            city = data.get("City") or data.get("city") or ""
            latitude = _safe_float(data.get("Lat") or data.get("lat") or data.get("Latitude"))
            longitude = _safe_float(data.get("Long") or data.get("long") or data.get("Longitude"))
            country_id = str(data.get("CountryID") or data.get("countryID") or "").strip()
            country_name, country_code = _OK_MOBILITY_COUNTRY_MAP.get(country_id, ("Spain", "ES"))

            # Determine location type from station properties
            location_type = "other"
            station_type = data.get("StationType") or data.get("stationType") or ""
            if str(station_type) == "2" or "airport" in name.lower() or "aeropuerto" in name.lower():
                location_type = "airport"
            elif "port" in name.lower() or "puerto" in name.lower():
                location_type = "port"

            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": str(station_id),
                "name": name,
                "city": city.title() if city else "",
                "country": country_name,
                "country_code": country_code,
                "latitude": latitude,
                "longitude": longitude,
                "location_type": location_type,
            })

        return locations
