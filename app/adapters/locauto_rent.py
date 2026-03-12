"""Locauto Rent adapter — OTA SOAP/XML API (Italy-only provider)."""

import logging
import uuid
import xml.etree.ElementTree as ET
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
    ExtraType,
    FuelType,
    MileagePolicy,
    TransmissionType,
    category_from_sipp,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Fee, Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import (
    Extra,
    Vehicle,
    VehicleLocation,
)

logger = logging.getLogger(__name__)

# ─── OTA XML namespace constants ───
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_OTA = "http://www.opentravel.org/OTA/2003/05"
NS_LOCAUTO = "https://nextrent.locautorent.com"

# ─── Predefined Italian locations (API returns empty) ───
# Location codes follow IATA convention or Locauto-specific codes.
_PREDEFINED_LOCATIONS: list[dict] = [
    # Major Airports
    {"code": "AHO", "name": "Alghero Airport", "city": "Alghero", "lat": 40.6361, "lng": 8.1200},
    {"code": "BGY", "name": "Bergamo Orio al Serio Airport", "city": "Bergamo", "lat": 45.6745, "lng": 9.7046},
    {"code": "BLQ", "name": "Bologna Airport", "city": "Bologna", "lat": 44.5354, "lng": 11.2887},
    {"code": "CAG", "name": "Cagliari Airport", "city": "Cagliari", "lat": 39.2515, "lng": 9.0544},
    {"code": "CT", "name": "Catania Airport", "city": "Catania", "lat": 37.4667, "lng": 15.0664},
    {"code": "FCO", "name": "Rome Fiumicino Airport", "city": "Rome", "lat": 41.8012, "lng": 12.2389},
    {"code": "FLR", "name": "Florence Airport", "city": "Florence", "lat": 43.8100, "lng": 11.2056},
    {"code": "GOA", "name": "Genoa Airport", "city": "Genoa", "lat": 44.4133, "lng": 8.8326},
    {"code": "LIN", "name": "Milan Linate Airport", "city": "Milan", "lat": 45.4465, "lng": 9.2774},
    {"code": "MXP", "name": "Milan Malpensa Airport T1", "city": "Milan", "lat": 45.6326, "lng": 8.7261},
    {"code": "MXP2", "name": "Milan Malpensa Airport T2", "city": "Milan", "lat": 45.6326, "lng": 8.7261},
    {"code": "NAP", "name": "Naples Airport", "city": "Naples", "lat": 40.8860, "lng": 14.2908},
    {"code": "PA", "name": "Palermo Airport", "city": "Palermo", "lat": 38.1607, "lng": 13.2919},
    {"code": "PI", "name": "Pisa Airport", "city": "Pisa", "lat": 43.6839, "lng": 10.3966},
    {"code": "PSR", "name": "Pescara Airport", "city": "Pescara", "lat": 42.4433, "lng": 14.2016},
    {"code": "SUF", "name": "Lamezia Terme Airport", "city": "Lamezia Terme", "lat": 38.8945, "lng": 16.2445},
    {"code": "TO", "name": "Turin Caselle Airport", "city": "Turin", "lat": 45.2009, "lng": 7.6495},
    {"code": "TS", "name": "Trieste Airport Ronchi dei Legionari", "city": "Trieste", "lat": 45.8289, "lng": 13.4767},
    {"code": "TV", "name": "Treviso Airport", "city": "Treviso", "lat": 45.6579, "lng": 12.1916},
    {"code": "VE", "name": "Venice Marco Polo Airport", "city": "Venice", "lat": 45.5053, "lng": 12.3519},
    {"code": "VCE", "name": "Venice Airport", "city": "Venice", "lat": 45.5053, "lng": 12.3519},
    {"code": "VRA", "name": "Verona Airport", "city": "Verona", "lat": 45.3944, "lng": 10.8967},
    {"code": "AOT", "name": "Aosta Airport", "city": "Aosta", "lat": 45.7366, "lng": 7.3639},
    {"code": "BAAPT", "name": "Bari Airport", "city": "Bari", "lat": 41.1395, "lng": 16.7753},
    {"code": "BDS", "name": "Brindisi Airport", "city": "Brindisi", "lat": 40.6542, "lng": 17.9440},
    {"code": "OLB", "name": "Olbia Airport", "city": "Olbia", "lat": 40.8941, "lng": 9.4975},
    {"code": "RC", "name": "Reggio Calabria Airport", "city": "Reggio Calabria", "lat": 38.0666, "lng": 15.6459},
    {"code": "TPS", "name": "Trapani Airport", "city": "Trapani", "lat": 37.9100, "lng": 12.4778},
    # Train Stations
    {"code": "BOC", "name": "Bologna Centrale Train Station", "city": "Bologna", "lat": 44.5043, "lng": 11.3417},
    {"code": "TBS", "name": "Brescia Central Station", "city": "Brescia", "lat": 45.5395, "lng": 10.2091},
    {"code": "MIT", "name": "Milan Central Station", "city": "Milan", "lat": 45.4864, "lng": 9.1876},
    {"code": "PAD", "name": "Padua Station", "city": "Padua", "lat": 45.4064, "lng": 11.8768},
    {"code": "RMT", "name": "Rome Termini Station", "city": "Rome", "lat": 41.9027, "lng": 12.4964},
    {"code": "RMTIB", "name": "Rome Tiburtina Station", "city": "Rome", "lat": 41.9042, "lng": 12.5286},
    {"code": "VRS", "name": "Verona Station", "city": "Verona", "lat": 45.4286, "lng": 10.9857},
    {"code": "VMS", "name": "Venice Mestre Station", "city": "Venice", "lat": 45.4932, "lng": 12.2436},
    {"code": "BAT", "name": "Bari Train Station", "city": "Bari", "lat": 41.1171, "lng": 16.8719},
    # Downtown / City Locations
    {"code": "ANCD", "name": "Ancona Downtown", "city": "Ancona", "lat": 43.6158, "lng": 13.5189},
    {"code": "ARD", "name": "Arezzo Downtown", "city": "Arezzo", "lat": 43.4625, "lng": 11.8791},
    {"code": "ATD", "name": "Asti Downtown", "city": "Asti", "lat": 44.9041, "lng": 8.2082},
    {"code": "BID", "name": "Biella Downtown", "city": "Biella", "lat": 45.5658, "lng": 8.0568},
    {"code": "CED", "name": "Cuneo Downtown", "city": "Cuneo", "lat": 44.3845, "lng": 7.5425},
    {"code": "CTD", "name": "Caltanissetta Downtown", "city": "Caltanissetta", "lat": 37.4895, "lng": 14.0574},
    {"code": "CZD", "name": "Catanzaro Downtown", "city": "Catanzaro", "lat": 38.9075, "lng": 16.5886},
    {"code": "CVT", "name": "Civitavecchia Downtown", "city": "Civitavecchia", "lat": 42.0906, "lng": 11.7977},
    {"code": "COD", "name": "Codogno Downtown", "city": "Codogno", "lat": 45.1583, "lng": 9.6965},
    {"code": "CON", "name": "Conegliano Downtown", "city": "Conegliano", "lat": 45.8844, "lng": 12.2972},
    {"code": "DCS", "name": "Cosenza Downtown", "city": "Cosenza", "lat": 39.3001, "lng": 16.2556},
    {"code": "CRD", "name": "Cremona Downtown", "city": "Cremona", "lat": 45.1343, "lng": 10.0224},
    {"code": "DFE", "name": "Ferrara Downtown", "city": "Ferrara", "lat": 44.8367, "lng": 11.6198},
    {"code": "EFD", "name": "Empoli Downtown", "city": "Empoli", "lat": 43.7208, "lng": 10.9456},
    {"code": "FID", "name": "Florence Downtown", "city": "Florence", "lat": 43.7696, "lng": 11.2558},
    {"code": "FGD", "name": "Foggia Downtown", "city": "Foggia", "lat": 41.4626, "lng": 15.5447},
    {"code": "FRD", "name": "Fiumicino Downtown", "city": "Fiumicino", "lat": 41.7730, "lng": 12.2367},
    {"code": "DGE", "name": "Genoa Downtown", "city": "Genoa", "lat": 44.4056, "lng": 8.9463},
    {"code": "DIM", "name": "Imperia Downtown", "city": "Imperia", "lat": 43.8895, "lng": 8.0404},
    {"code": "DMIA", "name": "Milan Assago Cassala", "city": "Milan", "lat": 45.4175, "lng": 9.1660},
    {"code": "MICOD", "name": "Milan Corvetto Downtown", "city": "Milan", "lat": 45.4456, "lng": 9.2147},
    {"code": "MND", "name": "Mantua Downtown", "city": "Mantua", "lat": 45.1667, "lng": 10.7833},
    {"code": "DMT", "name": "Modena Downtown", "city": "Modena", "lat": 44.6478, "lng": 10.9254},
    {"code": "MED", "name": "Merano Downtown", "city": "Merano", "lat": 46.6729, "lng": 11.1589},
    {"code": "MZD", "name": "Monza Downtown", "city": "Monza", "lat": 45.5845, "lng": 9.2744},
    {"code": "MOD", "name": "Modena City", "city": "Modena", "lat": 44.6478, "lng": 10.9254},
    {"code": "MBD", "name": "Milan Bergamo Downtown", "city": "Bergamo", "lat": 45.6981, "lng": 9.6773},
    {"code": "TNA", "name": "Naples Downtown", "city": "Naples", "lat": 40.8518, "lng": 14.2681},
    {"code": "NOD", "name": "Novara Downtown", "city": "Novara", "lat": 45.4452, "lng": 8.6186},
    {"code": "TPD", "name": "Padua Downtown", "city": "Padua", "lat": 45.4064, "lng": 11.8768},
    {"code": "PRD", "name": "Parma Downtown", "city": "Parma", "lat": 44.8014, "lng": 10.3279},
    {"code": "PVD", "name": "Pavia Downtown", "city": "Pavia", "lat": 45.1883, "lng": 9.1572},
    {"code": "PGD", "name": "Perugia Downtown", "city": "Perugia", "lat": 43.1107, "lng": 12.3908},
    {"code": "DPOM", "name": "Pomezia Downtown", "city": "Pomezia", "lat": 41.6747, "lng": 12.4974},
    {"code": "RMV", "name": "Rome Via Veneto", "city": "Rome", "lat": 41.9076, "lng": 12.4919},
    {"code": "RME", "name": "Rome EUR", "city": "Rome", "lat": 41.8359, "lng": 12.4697},
    {"code": "DRP", "name": "Rome Prati", "city": "Rome", "lat": 41.9061, "lng": 12.4470},
    {"code": "SSG", "name": "Sesto San Giovanni Downtown", "city": "Milan", "lat": 45.5324, "lng": 9.2351},
    {"code": "DNAF", "name": "Naples Fuorigrotta", "city": "Naples", "lat": 40.8397, "lng": 14.1708},
]

# OTA equipment type code → human-readable name mapping
_EQUIP_TYPE_NAMES: dict[str, str] = {
    "7": "Infant Child Seat",
    "8": "Child Toddler Seat",
    "9": "Child Booster Seat",
    "13": "GPS Navigation",
    "14": "Ski Rack",
    "23": "One-Way Fee",
    "35": "One-Way Fee (Sardinia)",
    "46": "Additional Driver",
}


def _safe_attr(element: ET.Element | None, attr: str, default: str = "") -> str:
    """Safely read an XML attribute."""
    if element is None:
        return default
    return element.get(attr, default)


def _safe_float(value: str, default: float = 0.0) -> float:
    """Parse a string to float, returning default on failure."""
    if not value:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


@register_adapter
class LocautoRentAdapter(BaseAdapter):
    """Adapter for Locauto Rent (NextRent) OTA SOAP/XML API.

    Italy-only provider with 75+ predefined locations.
    Uses OTA (OpenTravel Alliance) SOAP XML for all operations.
    """

    supplier_id = "locauto_rent"
    supplier_name = "Locauto Rent"
    supports_one_way = True
    default_timeout = 30.0

    # ─── SOAP XML builders ───

    def _build_soap_envelope(self, inner_xml: str) -> str:
        """Wrap inner XML in a SOAP envelope with the correct namespaces."""
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<SOAP-ENV:Envelope'
            ' xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:ns1="http://www.opentravel.org/OTA/2003/05"'
            ' xmlns:ns2="https://nextrent.locautorent.com">'
            "<SOAP-ENV:Body>"
            f"{inner_xml}"
            "</SOAP-ENV:Body>"
            "</SOAP-ENV:Envelope>"
        )

    def _build_pos_element(self) -> str:
        """Build the OTA POS (Point of Sale) authentication element."""
        settings = get_settings()
        return (
            "<ns1:POS>"
            '<ns1:Source ISOCountry="IT" ISOCurrency="EUR">'
            f'<ns1:RequestorID ID_Context="{settings.locauto_username}"'
            f' MessagePassword="{settings.locauto_password}"/>'
            "</ns1:Source>"
            "</ns1:POS>"
        )

    def _format_datetime(self, d: object, t: object) -> str:
        """Format date + time into Locauto's required ISO format with timezone offset.

        Locauto requires +02:00 (CET) timezone offset.
        """
        return f"{d.isoformat()}T{t.strftime('%H:%M')}:00+02:00"

    def _build_availability_request(
        self,
        pickup_code: str,
        dropoff_code: str,
        pickup_dt: str,
        return_dt: str,
        driver_age: int,
    ) -> str:
        """Build OTA_VehAvailRateRQ SOAP request XML."""
        inner = (
            "<ns2:OTA_VehAvailRateRS>"
            '<ns1:OTA_VehAvailRateRQ MaxResponses="100" Version="1.0"'
            ' Target="Production" SequenceNmbr="1" PrimaryLangID="en">'
            f"{self._build_pos_element()}"
            '<ns1:VehAvailRQCore Status="Available">'
            f'<ns1:VehRentalCore PickUpDateTime="{pickup_dt}" ReturnDateTime="{return_dt}">'
            f'<ns1:PickUpLocation LocationCode="{pickup_code}"/>'
            f'<ns1:ReturnLocation LocationCode="{dropoff_code}"/>'
            "</ns1:VehRentalCore>"
            f'<ns1:DriverType Age="{driver_age}"/>'
            "</ns1:VehAvailRQCore>"
            "</ns1:OTA_VehAvailRateRQ>"
            "</ns2:OTA_VehAvailRateRS>"
        )
        return self._build_soap_envelope(inner)

    def _build_reservation_request(
        self,
        pickup_code: str,
        dropoff_code: str,
        pickup_dt: str,
        return_dt: str,
        sipp_code: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        extras: list[dict] | None = None,
    ) -> str:
        """Build OTA_VehResRQ SOAP request XML."""
        timestamp = datetime.utcnow().isoformat() + "Z"
        echo_token = uuid.uuid4().hex[:12]

        extras_xml = ""
        if extras:
            extras_xml = "<ns1:SpecialEquipPrefs>"
            for ext in extras:
                extras_xml += (
                    f'<ns1:SpecialEquipPref Code="{ext["code"]}"'
                    f' Quantity="{ext.get("quantity", 1)}"/>'
                )
            extras_xml += "</ns1:SpecialEquipPrefs>"

        inner = (
            "<ns2:OTA_VehResRS>"
            f'<ns1:OTA_VehResRQ EchoToken="{echo_token}"'
            f' TimeStamp="{timestamp}" Target="Production" Version="1.0">'
            f"{self._build_pos_element()}"
            "<ns1:VehResRQCore>"
            f'<ns1:VehRentalCore PickUpDateTime="{pickup_dt}" ReturnDateTime="{return_dt}">'
            f'<ns1:PickUpLocation LocationCode="{pickup_code}"/>'
            f'<ns1:ReturnLocation LocationCode="{dropoff_code}"/>'
            "</ns1:VehRentalCore>"
            "<ns1:Customer>"
            "<ns1:Primary>"
            "<ns1:PersonName>"
            f"<ns1:GivenName>{_xml_escape(first_name)}</ns1:GivenName>"
            f"<ns1:Surname>{_xml_escape(last_name)}</ns1:Surname>"
            "</ns1:PersonName>"
            f"<ns1:Email>{_xml_escape(email)}</ns1:Email>"
            f"<ns1:Telephone>{_xml_escape(phone)}</ns1:Telephone>"
            "</ns1:Primary>"
            "</ns1:Customer>"
            f'<ns1:VehPref Code="{sipp_code}" CodeContext="SIPP"/>'
            f"{extras_xml}"
            "</ns1:VehResRQCore>"
            "</ns1:OTA_VehResRQ>"
            "</ns2:OTA_VehResRS>"
        )
        return self._build_soap_envelope(inner)

    def _build_cancel_request(self, confirmation_id: str) -> str:
        """Build OTA_VehCancelRQ SOAP request XML."""
        timestamp = datetime.utcnow().isoformat() + "Z"
        echo_token = uuid.uuid4().hex[:12]

        inner = (
            "<ns2:OTA_VehCancelRS>"
            f'<ns1:OTA_VehCancelRQ EchoToken="{echo_token}"'
            f' TimeStamp="{timestamp}" Target="Production" Version="1.0">'
            f"{self._build_pos_element()}"
            "<ns1:VehCancelRQCore>"
            "<ns1:UniqueID"
            f' Type="14" ID="{confirmation_id}"/>'
            "</ns1:VehCancelRQCore>"
            "</ns1:OTA_VehCancelRQ>"
            "</ns2:OTA_VehCancelRS>"
        )
        return self._build_soap_envelope(inner)

    # ─── XML response parsing helpers ───

    def _find_veh_avails(self, xml_text: str) -> list[ET.Element]:
        """Parse SOAP XML response and locate all VehAvail elements.

        Handles the deeply nested OTA namespace structure by trying
        multiple XPath strategies (namespace-aware, then fallback).
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("[%s] XML parse error: %s", self.supplier_id, exc)
            return []

        # Register namespaces for XPath
        ns = {"soap": NS_SOAP, "ota": NS_OTA, "loc": NS_LOCAUTO}

        # Strategy 1: Direct namespace XPath
        veh_avails = root.findall(".//ota:VehAvail", ns)
        if veh_avails:
            return veh_avails

        # Strategy 2: Try without namespace prefix (some responses strip namespaces)
        veh_avails = root.findall(".//{%s}VehAvail" % NS_OTA)
        if veh_avails:
            return veh_avails

        # Strategy 3: Walk the tree looking for VehAvail local name
        veh_avails = []
        for elem in root.iter():
            local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_name == "VehAvail":
                veh_avails.append(elem)

        if veh_avails:
            return veh_avails

        logger.warning("[%s] No VehAvail elements found in response", self.supplier_id)
        return []

    def _find_child(self, element: ET.Element, local_name: str) -> ET.Element | None:
        """Find a child element by local name, ignoring namespace prefixes."""
        # Try with OTA namespace first
        child = element.find(f"{{{NS_OTA}}}{local_name}")
        if child is not None:
            return child
        # Try without namespace
        child = element.find(local_name)
        if child is not None:
            return child
        # Walk direct children by local name
        for ch in element:
            tag = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
            if tag == local_name:
                return ch
        return None

    def _find_children(self, element: ET.Element, local_name: str) -> list[ET.Element]:
        """Find all child elements by local name, ignoring namespace prefixes."""
        results = element.findall(f"{{{NS_OTA}}}{local_name}")
        if results:
            return results
        results = element.findall(local_name)
        if results:
            return results
        return [
            ch for ch in element
            if (ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag) == local_name
        ]

    # ─── Core adapter methods ───

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()

        pickup_code = pickup_entry.pickup_id
        dropoff_code = (
            dropoff_entry.pickup_id
            if dropoff_entry and dropoff_entry.pickup_id != pickup_code
            else pickup_code
        )

        pickup_dt = self._format_datetime(request.pickup_date, request.pickup_time)
        return_dt = self._format_datetime(request.dropoff_date, request.dropoff_time)

        xml_payload = self._build_availability_request(
            pickup_code=pickup_code,
            dropoff_code=dropoff_code,
            pickup_dt=pickup_dt,
            return_dt=return_dt,
            driver_age=request.driver_age,
        )

        response = await self._request(
            "POST",
            settings.locauto_api_url,
            content=xml_payload,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": '"https://nextrent.locautorent.com/OTA_VehAvailRateRS"',
            },
        )

        return self._parse_vehicles(
            response.text, request, pickup_entry, dropoff_entry
        )

    def _parse_vehicles(
        self,
        xml_text: str,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> list[Vehicle]:
        veh_avails = self._find_veh_avails(xml_text)
        if not veh_avails:
            return []

        rental_days = (request.dropoff_date - request.pickup_date).days or 1

        vehicles: list[Vehicle] = []
        for veh_avail in veh_avails:
            try:
                vehicle = self._parse_single_vehicle(
                    veh_avail, rental_days, request, pickup_entry, dropoff_entry
                )
                if vehicle:
                    vehicles.append(vehicle)
            except Exception:
                logger.debug(
                    "[%s] Skipping unparseable vehicle element",
                    self.supplier_id,
                    exc_info=True,
                )
                continue

        logger.info("[%s] Parsed %d vehicles", self.supplier_id, len(vehicles))
        return vehicles

    def _parse_single_vehicle(
        self,
        veh_avail: ET.Element,
        rental_days: int,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> Vehicle | None:
        # Navigate: VehAvail > VehAvailCore > Vehicle
        core = self._find_child(veh_avail, "VehAvailCore")
        if core is None:
            return None

        vehicle_el = self._find_child(core, "Vehicle")
        if vehicle_el is None:
            return None

        # ── Availability status ──
        status = _safe_attr(core, "Status", "Available")
        is_available = status == "Available"

        # ── Vehicle attributes ──
        sipp_code = _safe_attr(vehicle_el, "Code", "")
        transmission_raw = _safe_attr(vehicle_el, "TransmissionType", "Manual").lower()
        passengers = int(_safe_attr(vehicle_el, "PassengerQuantity", "4") or "4")
        baggage = int(_safe_attr(vehicle_el, "BaggageQuantity", "2") or "2")
        air_con = _safe_attr(vehicle_el, "AirConditionInd", "true").lower() == "true"

        # VehType > DoorCount
        veh_type = self._find_child(vehicle_el, "VehType")
        door_count = int(_safe_attr(veh_type, "DoorCount", "4") or "4")

        # VehMakeModel > ModelYear contains vehicle name (e.g. "Fiat Panda")
        veh_make_model = self._find_child(vehicle_el, "VehMakeModel")
        vehicle_name = _safe_attr(veh_make_model, "ModelYear", "Unknown Vehicle")

        # VehIdentity > VehicleAssetNumber is the more accurate ACRISS code
        veh_identity = self._find_child(vehicle_el, "VehIdentity")
        acriss_code = _safe_attr(veh_identity, "VehicleAssetNumber", "")
        # Prefer VehicleAssetNumber as the canonical SIPP code if available
        effective_sipp = acriss_code or sipp_code

        # PictureURL
        picture_el = self._find_child(vehicle_el, "PictureURL")
        image_url = ""
        if picture_el is not None and picture_el.text:
            image_url = picture_el.text.strip()

        # ── Pricing ──
        total_charge = self._find_child(core, "TotalCharge")
        total_amount = _safe_float(_safe_attr(total_charge, "RateTotalAmount"))
        estimated_total = _safe_float(_safe_attr(total_charge, "EstimatedTotalAmount"))
        currency = _safe_attr(total_charge, "CurrencyCode", "EUR") or "EUR"

        # Use EstimatedTotalAmount as it includes mandatory charges
        price = estimated_total if estimated_total > 0 else total_amount

        # Fallback to VehicleCharge if TotalCharge is missing
        if price <= 0:
            rental_rate = self._find_child(core, "RentalRate")
            if rental_rate is not None:
                veh_charges = self._find_child(rental_rate, "VehicleCharges")
                if veh_charges is not None:
                    veh_charge = self._find_child(veh_charges, "VehicleCharge")
                    if veh_charge is not None:
                        price = _safe_float(_safe_attr(veh_charge, "Amount"))
                        currency = _safe_attr(veh_charge, "CurrencyCode", "EUR") or "EUR"

        if price <= 0:
            return None

        daily_rate = round(price / rental_days, 2)

        # ── Tax info ──
        fees: list[Fee] = []
        rental_rate = self._find_child(core, "RentalRate")
        if rental_rate is not None:
            veh_charges = self._find_child(rental_rate, "VehicleCharges")
            if veh_charges is not None:
                veh_charge = self._find_child(veh_charges, "VehicleCharge")
                if veh_charge is not None:
                    tax_amounts = self._find_child(veh_charge, "TaxAmounts")
                    if tax_amounts is not None:
                        for tax_el in self._find_children(tax_amounts, "TaxAmount"):
                            tax_total = _safe_float(_safe_attr(tax_el, "Total"))
                            tax_desc = _safe_attr(tax_el, "Description", "VAT")
                            tax_pct = _safe_attr(tax_el, "Percentage", "22")
                            if tax_total > 0:
                                fees.append(Fee(
                                    name=f"{tax_desc} ({tax_pct}%)",
                                    amount=tax_total,
                                    currency=currency,
                                    included_in_total=True,
                                    description=f"{tax_desc} at {tax_pct}%",
                                ))

        # ── Extras (PricedEquips) ──
        extras: list[Extra] = []
        priced_equips = self._find_child(core, "PricedEquips")
        if priced_equips is not None:
            for pe in self._find_children(priced_equips, "PricedEquip"):
                equipment = self._find_child(pe, "Equipment")
                charge = self._find_child(pe, "Charge")
                if equipment is None:
                    continue

                equip_type = _safe_attr(equipment, "EquipType", "")
                desc_el = self._find_child(equipment, "Description")
                description = ""
                if desc_el is not None and desc_el.text:
                    description = desc_el.text.strip()

                # Use description as name, fall back to equip type lookup
                extra_name = description or _EQUIP_TYPE_NAMES.get(equip_type, f"Equipment {equip_type}")
                extra_amount = _safe_float(_safe_attr(charge, "Amount")) if charge else 0.0
                extra_currency = _safe_attr(charge, "CurrencyCode", "EUR") if charge else "EUR"
                included_in_rate = (
                    _safe_attr(charge, "IncludedInRate", "false").lower() == "true"
                    if charge else False
                )

                # Determine extra type: one-way fees are FEE, rest are EQUIPMENT
                extra_type = ExtraType.EQUIPMENT
                if equip_type in ("23", "35") or "one way" in extra_name.lower():
                    extra_type = ExtraType.FEE

                extras.append(Extra(
                    id=f"ext_{self.supplier_id}_{equip_type}",
                    name=extra_name,
                    daily_rate=round(extra_amount / rental_days, 2) if rental_days > 0 else extra_amount,
                    total_price=extra_amount,
                    currency=extra_currency,
                    max_quantity=1,
                    type=extra_type,
                    mandatory=included_in_rate,
                    description=description or None,
                    supplier_data={
                        "code": equip_type,
                        "amount": extra_amount,
                        "included_in_rate": included_in_rate,
                    },
                ))

        # ── Derive make / model ──
        name_parts = vehicle_name.split(" ", 1)
        make = name_parts[0].strip() if name_parts else "Locauto"
        model = name_parts[1].strip() if len(name_parts) > 1 else ""

        # ── Pickup / Dropoff locations ──
        pickup_loc = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_entry.original_name,
            latitude=pickup_entry.latitude,
            longitude=pickup_entry.longitude,
            country_code="IT",
        )

        dropoff_loc = None
        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            dropoff_loc = VehicleLocation(
                supplier_location_id=dropoff_entry.pickup_id,
                name=dropoff_entry.original_name,
                latitude=dropoff_entry.latitude,
                longitude=dropoff_entry.longitude,
                country_code="IT",
            )

        return Vehicle(
            id=f"gw_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_vehicle_id=effective_sipp or sipp_code,
            name=f"{vehicle_name} or similar",
            category=category_from_sipp(effective_sipp),
            make=make,
            model=model,
            image_url=image_url,
            transmission=(
                TransmissionType.AUTOMATIC
                if "auto" in transmission_raw
                else TransmissionType.MANUAL
            ),
            fuel_type=FuelType.UNKNOWN,  # Locauto does not provide fuel type
            seats=passengers,
            doors=door_count,
            bags_large=baggage,
            bags_small=0,
            air_conditioning=air_con,
            mileage_policy=MileagePolicy.UNLIMITED,
            sipp_code=effective_sipp or None,
            is_available=is_available,
            pickup_location=pickup_loc,
            dropoff_location=dropoff_loc,
            pricing=Pricing(
                currency=currency,
                total_price=price,
                daily_rate=daily_rate,
                price_includes_tax=True,
                fees=fees,
            ),
            extras=extras,
            cancellation_policy=None,  # API does not return cancellation terms
            supplier_data={
                "sipp_code": sipp_code,
                "acriss_code": acriss_code,
                "pickup_code": pickup_entry.pickup_id,
                "dropoff_code": (
                    dropoff_entry.pickup_id
                    if dropoff_entry
                    else pickup_entry.pickup_id
                ),
                "status": status,
                "rate_total": total_amount,
                "estimated_total": estimated_total,
                "pickup_datetime": self._format_datetime(request.pickup_date, request.pickup_time),
                "return_datetime": self._format_datetime(request.dropoff_date, request.dropoff_time),
            },
        )

    async def create_booking(
        self, request: CreateBookingRequest, vehicle: Vehicle
    ) -> BookingResponse:
        settings = get_settings()
        sd = vehicle.supplier_data

        pickup_code = sd.get("pickup_code", "")
        dropoff_code = sd.get("dropoff_code", pickup_code)
        sipp_code = sd.get("sipp_code") or sd.get("acriss_code") or vehicle.sipp_code or ""

        # Build extras list for the SOAP request
        booking_extras: list[dict] = []
        for ext in request.extras:
            # Extract the OTA equip type code from our extra ID (ext_locauto_rent_<code>)
            code = ext.extra_id.replace(f"ext_{self.supplier_id}_", "")
            booking_extras.append({"code": code, "quantity": ext.quantity})

        # Pickup/dropoff datetimes are stored in supplier_data by the search
        # orchestrator when caching the vehicle result.
        pickup_datetime_str = sd.get("pickup_datetime", "")
        return_datetime_str = sd.get("return_datetime", "")

        xml_payload = self._build_reservation_request(
            pickup_code=pickup_code,
            dropoff_code=dropoff_code,
            pickup_dt=pickup_datetime_str,
            return_dt=return_datetime_str,
            sipp_code=sipp_code,
            first_name=request.driver.first_name,
            last_name=request.driver.last_name,
            email=request.driver.email,
            phone=request.driver.phone,
            extras=booking_extras if booking_extras else None,
        )

        response = await self._request(
            "POST",
            settings.locauto_api_url,
            content=xml_payload,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": '"https://nextrent.locautorent.com/OTA_VehResRS"',
            },
        )

        return self._parse_booking_response(response.text, vehicle)

    def _parse_booking_response(self, xml_text: str, vehicle: Vehicle) -> BookingResponse:
        """Parse OTA_VehResRS response to extract the confirmation number."""
        confirmation_id = ""
        status = BookingStatus.FAILED

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("[%s] Booking response XML parse error: %s", self.supplier_id, exc)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"error": "XML parse error", "raw": xml_text[:500]},
            )

        # Look for VehReservation > VehSegmentCore > ConfID
        for elem in root.iter():
            local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_name == "ConfID":
                confirmation_id = elem.get("ID", "") or elem.get("id", "")
                if confirmation_id:
                    status = BookingStatus.CONFIRMED
                    break

        # Also check for UniqueID elements
        if not confirmation_id:
            for elem in root.iter():
                local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local_name == "UniqueID":
                    uid = elem.get("ID", "") or elem.get("id", "")
                    if uid:
                        confirmation_id = uid
                        status = BookingStatus.CONFIRMED
                        break

        # Check for errors in the response
        for elem in root.iter():
            local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_name == "Error" or local_name == "Errors":
                error_text = elem.get("ShortText", "") or elem.text or ""
                logger.error("[%s] Booking error from provider: %s", self.supplier_id, error_text)
                if not confirmation_id:
                    return BookingResponse(
                        id=f"bk_{uuid.uuid4().hex[:16]}",
                        supplier_id=self.supplier_id,
                        supplier_booking_id="",
                        status=BookingStatus.FAILED,
                        vehicle_name=vehicle.name,
                        total_price=vehicle.pricing.total_price,
                        currency=vehicle.pricing.currency,
                        supplier_data={"error": error_text},
                    )

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=confirmation_id,
            status=status,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data={
                "confirmation_id": confirmation_id,
                "sipp_code": vehicle.sipp_code,
            },
        )

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()

        xml_payload = self._build_cancel_request(supplier_booking_id)

        response = await self._request(
            "POST",
            settings.locauto_api_url,
            content=xml_payload,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": '"https://nextrent.locautorent.com/OTA_VehCancelRS"',
            },
        )

        # Parse cancel response for status
        cancel_status = BookingStatus.CANCELLED
        try:
            root = ET.fromstring(response.text)
            # Check for errors
            for elem in root.iter():
                local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local_name in ("Error", "Errors"):
                    error_text = elem.get("ShortText", "") or elem.text or ""
                    logger.error(
                        "[%s] Cancel error from provider: %s",
                        self.supplier_id,
                        error_text,
                    )
                    cancel_status = BookingStatus.FAILED
                    break
        except ET.ParseError:
            logger.error("[%s] Cancel response XML parse error", self.supplier_id)
            cancel_status = BookingStatus.FAILED

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=cancel_status,
            supplier_cancellation_id=supplier_booking_id,
        )

    async def get_locations(self) -> list[dict]:
        """Return predefined Italian locations.

        Locauto's getLocations() API returns empty, so we use the
        hardcoded list that mirrors the PHP service implementation.
        """
        locations: list[dict] = []
        for loc in _PREDEFINED_LOCATIONS:
            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": loc["code"],
                "name": loc["name"],
                "city": loc["city"],
                "country": "Italy",
                "country_code": "IT",
                "latitude": loc["lat"],
                "longitude": loc["lng"],
            })

        logger.info("[%s] Returning %d predefined locations", self.supplier_id, len(locations))
        return locations


def _xml_escape(text: str) -> str:
    """Escape special XML characters in text content."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
