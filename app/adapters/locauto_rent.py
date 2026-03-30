"""Locauto Rent adapter — OTA SOAP/XML API (Italy-only provider)."""

import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import re

import yaml

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
_GATEWAY_ROOT = Path(__file__).resolve().parents[2]
_LOCAUTO_LOCATIONS_YAML = _GATEWAY_ROOT / "config" / "suppliers" / "locauto_rent_locations.yaml"

# ─── OTA XML namespace constants ───
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_OTA = "http://www.opentravel.org/OTA/2003/05"
NS_LOCAUTO = "https://nextrent.locautorent.com"

# ─── Legacy predefined Italian locations (fallback if YAML is unavailable) ───
# Location codes follow IATA convention or Locauto-specific codes.
_LEGACY_PREDEFINED_LOCATIONS: list[dict] = [
    # Major Airports
    {"code": "AHO", "name": "Alghero Airport", "city": "Alghero", "lat": 40.6321, "lng": 8.2908},
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
    "6": "Injury Protection",
    "7": "Infant Child Seat",
    "8": "Child Toddler Seat",
    "9": "Additional Driver",
    "13": "GPS Navigation",
    "14": "Ski Rack",
    "19": "GPS Navigation",
    "23": "One-Way Fee",
    "35": "One-Way Fee (Sardinia)",
    "43": "Roadside Assistance Plus",
    "46": "Additional Driver",
    "55": "Snow Chains",
    "78": "Child Seat",
    "89": "Pet Transport (Bau the Way)",
    "136": "Don't Worry Protection",
    "137": "Additional Driver",
    "138": "Pool Driving (3+ Drivers)",
    "139": "Young Driver Surcharge (19-24)",
    "140": "Glass & Wheels Protection",
    "145": "Body Protection",
    "146": "Super Theft Protection",
    "147": "Smart Cover",
}


def _titleize(value: str) -> str:
    """Normalize vendor all-caps strings into readable title case."""
    return " ".join(part.capitalize() for part in value.strip().split())


def _normalize_phone(value: str) -> str | None:
    """Normalize vendor phone numbers into a dialable Italian format."""
    digits = " ".join(value.split())
    if not digits:
        return None
    return digits if digits.startswith("+39") else f"+39 {digits}"


def _normalize_hours(value: str | None) -> str | None:
    """Convert vendor hour strings like 7.00 - 24.00 into 07:00 - 24:00."""
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return None

    def repl(match: re.Match[str]) -> str:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"

    return re.sub(r"(\d{1,2})\.(\d{2})", repl, cleaned)


def _infer_location_type(name: str) -> str:
    """Infer a canonical location type from the official station label."""
    lowered = name.lower()
    if "airport" in lowered:
        return "airport"
    if "station" in lowered:
        return "train_station"
    return "downtown"


def _infer_iata(code: str, location_type: str) -> str | None:
    """Infer IATA only when the code itself is clearly an airport code."""
    if location_type != "airport":
        return None
    normalized = code.strip().upper()
    return normalized if len(normalized) == 3 and normalized.isalpha() else None


def _normalize_legacy_location(location: dict) -> dict:
    """Preserve existing fallback behavior when the YAML file is unavailable."""
    name = location["name"]
    location_type = _infer_location_type(name)
    return {
        "code": location["code"],
        "name": name,
        "city": location["city"],
        "province": "",
        "country": "Italy",
        "country_code": "IT",
        "location_type": location_type,
        "is_airport": location_type == "airport",
        "iata": _infer_iata(location["code"], location_type),
        "latitude": location["lat"],
        "longitude": location["lng"],
        "address": None,
        "postal_code": None,
        "phone": None,
        "email": None,
        "operating_hours": None,
        "pickup_instructions": None,
        "dropoff_instructions": None,
        "out_of_hours": None,
    }


def _load_locations() -> list[dict]:
    """Load Locauto locations from the generated YAML file, falling back to legacy data."""
    if _LOCAUTO_LOCATIONS_YAML.exists():
        with _LOCAUTO_LOCATIONS_YAML.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        loaded = data.get("locations", [])
        if loaded:
            logger.info("[locauto_rent] Loaded %d locations from %s", len(loaded), _LOCAUTO_LOCATIONS_YAML)
            return loaded

    logger.warning(
        "[locauto_rent] Falling back to legacy predefined locations because %s is missing or empty",
        _LOCAUTO_LOCATIONS_YAML,
    )
    return [_normalize_legacy_location(location) for location in _LEGACY_PREDEFINED_LOCATIONS]


_LOCATIONS: list[dict] = _load_locations()
_LOCATION_BY_CODE: dict[str, dict] = {
    str(location.get("code", "")).strip().upper(): location
    for location in _LOCATIONS
    if location.get("code")
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
            f'<ns1:RequestorID ID="{settings.locauto_username}"'
            f' ID_Context="{settings.locauto_username}"'
            f' MessagePassword="{settings.locauto_password}">'
            "<ns1:CompanyName>Vrooem</ns1:CompanyName>"
            "</ns1:RequestorID>"
            "</ns1:Source>"
            "</ns1:POS>"
        )

    def _format_datetime(self, d: object, t: object) -> str:
        """Format date + time into Locauto's required ISO format with timezone offset.

        Locauto requires +02:00 (CET) timezone offset.
        """
        return f"{d.isoformat()}T{t.strftime('%H:%M')}:00+02:00"

    def _location_metadata(self, code: str) -> dict:
        """Return the enriched Locauto location payload for a provider code."""
        return _LOCATION_BY_CODE.get(code.strip().upper(), {})

    def _build_vehicle_location(
        self,
        entry: ProviderLocationEntry | None,
        fallback_country_code: str = "IT",
    ) -> VehicleLocation | None:
        """Attach enriched Locauto location metadata to a vehicle endpoint payload."""
        if entry is None:
            return None

        metadata = self._location_metadata(entry.pickup_id)
        location_type = metadata.get("location_type", "other")
        address = metadata.get("address")
        phone = metadata.get("phone")
        email = metadata.get("email")
        operating_hours = metadata.get("operating_hours")
        pickup_instructions = metadata.get("pickup_instructions")
        dropoff_instructions = metadata.get("dropoff_instructions")
        out_of_hours = metadata.get("out_of_hours")
        is_airport = bool(metadata.get("is_airport"))
        airport_code = metadata.get("iata")

        return VehicleLocation(
            supplier_location_id=entry.pickup_id,
            name=metadata.get("name") or entry.original_name or "",
            city=metadata.get("city") or "",
            country_code=metadata.get("country_code") or fallback_country_code,
            latitude=metadata.get("latitude") if metadata.get("latitude") is not None else entry.latitude,
            longitude=metadata.get("longitude") if metadata.get("longitude") is not None else entry.longitude,
            location_type=location_type,
            airport_code=airport_code,
            address=address,
            phone=phone,
            email=email,
            is_airport=is_airport,
            operating_hours=operating_hours,
            pickup_instructions=pickup_instructions,
            dropoff_instructions=dropoff_instructions,
            out_of_hours=out_of_hours,
        )

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
        booking_ref: str = "",
        flight_number: str = "",
    ) -> str:
        """Build OTA_VehResRQ SOAP request XML."""
        timestamp = datetime.utcnow().isoformat() + "Z"
        echo_token = uuid.uuid4().hex[:12]

        extras_xml = "<ns1:SpecialEquipPrefs>"
        if extras:
            for ext in extras:
                extras_xml += (
                    f'<ns1:SpecialEquipPref Code="{ext["code"]}"'
                    f' Quantity="{ext.get("quantity", 1)}"/>'
                )
        extras_xml += "</ns1:SpecialEquipPrefs>"

        inner = (
            "<ns2:OTA_VehResRS>"
            '<ns1:OTA_VehResRQ>'
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
            "</ns1:Primary>"
            "</ns1:Customer>"
            f'<ns1:VehPref Code="{sipp_code}" CodeContext="SIPP"/>'
            f"{extras_xml}"
            "</ns1:VehResRQCore>"
            "<ns1:VehResRQInfo>"
            "<ns1:SpecialReqPref></ns1:SpecialReqPref>"
            "<ns1:RentalPaymentPref>"
            f'<ns1:Voucher Identifier="{_xml_escape(booking_ref)}"/>'
            "</ns1:RentalPaymentPref>"
            f'{f"""<ns1:ArrivalDetails TransportationCode="14"><ns1:OperatingCompany Code="{_xml_escape(flight_number)}"/></ns1:ArrivalDetails>""" if flight_number else ""}'
            "</ns1:VehResRQInfo>"
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
        transmission_raw = _safe_attr(vehicle_el, "TransmissionType", "").lower()
        passengers_raw = _safe_attr(vehicle_el, "PassengerQuantity", "")
        baggage_raw = _safe_attr(vehicle_el, "BaggageQuantity", "")
        air_con_raw = _safe_attr(vehicle_el, "AirConditionInd", "").lower()

        # VehType > DoorCount
        veh_type = self._find_child(vehicle_el, "VehType")
        door_count_raw = _safe_attr(veh_type, "DoorCount", "")

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
        pickup_loc = self._build_vehicle_location(pickup_entry)

        dropoff_loc = None
        if dropoff_entry and dropoff_entry.pickup_id != pickup_entry.pickup_id:
            dropoff_loc = self._build_vehicle_location(dropoff_entry)

        pickup_metadata = self._location_metadata(pickup_entry.pickup_id)
        dropoff_metadata = (
            self._location_metadata(dropoff_entry.pickup_id)
            if dropoff_entry
            else pickup_metadata
        )

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": effective_sipp or sipp_code,
            "provider_product_id": effective_sipp or sipp_code or None,
            "availability_status": status.lower() if status else ("available" if is_available else None),
            "name": f"{vehicle_name} or similar",
            "category": category_from_sipp(effective_sipp),
            "make": make,
            "model": model,
            "image_url": image_url,
            "is_available": is_available,
            "pickup_location": pickup_loc,
            "dropoff_location": dropoff_loc,
            "pricing": Pricing(
                currency=currency,
                total_price=price,
                daily_rate=daily_rate,
                price_includes_tax=True,
                fees=fees,
            ),
            "extras": extras,
            "cancellation_policy": None,  # API does not return cancellation terms
            "supplier_data": {
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
                "pickup_address": pickup_metadata.get("address"),
                "dropoff_address": dropoff_metadata.get("address"),
                "pickup_phone": pickup_metadata.get("phone"),
                "dropoff_phone": dropoff_metadata.get("phone"),
                "pickup_email": pickup_metadata.get("email"),
                "dropoff_email": dropoff_metadata.get("email"),
                "pickup_hours": pickup_metadata.get("operating_hours"),
                "dropoff_hours": dropoff_metadata.get("operating_hours"),
                "pickup_instructions": pickup_metadata.get("pickup_instructions"),
                "dropoff_instructions": dropoff_metadata.get("dropoff_instructions"),
                "pickup_out_of_hours": pickup_metadata.get("out_of_hours"),
                "dropoff_out_of_hours": dropoff_metadata.get("out_of_hours"),
                "pickup_station_name": pickup_metadata.get("name") or pickup_entry.original_name,
                "dropoff_station_name": (
                    dropoff_metadata.get("name")
                    or (dropoff_entry.original_name if dropoff_entry else pickup_entry.original_name)
                ),
                "office_address": pickup_metadata.get("address"),
                "office_phone": pickup_metadata.get("phone"),
                "office_schedule": pickup_metadata.get("operating_hours"),
                "at_airport": bool(pickup_metadata.get("is_airport")),
            },
        }

        if transmission_raw:
            vehicle_kwargs["transmission"] = (
                TransmissionType.AUTOMATIC
                if "auto" in transmission_raw
                else TransmissionType.MANUAL
            )
        if passengers_raw:
            vehicle_kwargs["seats"] = int(passengers_raw)
        if door_count_raw:
            vehicle_kwargs["doors"] = int(door_count_raw)
        if baggage_raw:
            vehicle_kwargs["bags_large"] = int(baggage_raw)
        if air_con_raw in ("true", "false"):
            vehicle_kwargs["air_conditioning"] = air_con_raw == "true"
        if effective_sipp:
            vehicle_kwargs["sipp_code"] = effective_sipp

        return Vehicle(**vehicle_kwargs)

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
            booking_ref=str(request.laravel_booking_id or ""),
            flight_number=request.flight_number or "",
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

        logger.info("[%s] Booking response: %s", self.supplier_id, response.text[:1000])
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
                confirmation_id = (
                    elem.get("ID_Context", "")
                    or elem.get("ID", "")
                    or elem.get("id", "")
                )
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
        """Return Locauto locations from the generated YAML source of truth."""
        locations: list[dict] = []
        for loc in _LOCATIONS:
            locations.append({
                "provider": self.supplier_id,
                "provider_location_id": loc["code"],
                "name": loc["name"],
                "city": loc["city"],
                "country": loc.get("country", "Italy"),
                "country_code": loc.get("country_code", "IT"),
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "location_type": loc.get("location_type", "other"),
                "iata": loc.get("iata"),
                "is_airport": bool(loc.get("is_airport")),
                "address": loc.get("address"),
                "postal_code": loc.get("postal_code"),
                "phone": loc.get("phone"),
                "email": loc.get("email"),
                "operating_hours": loc.get("operating_hours"),
                "pickup_instructions": loc.get("pickup_instructions"),
                "dropoff_instructions": loc.get("dropoff_instructions"),
                "out_of_hours": loc.get("out_of_hours"),
            })

        logger.info("[%s] Returning %d configured locations", self.supplier_id, len(locations))
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
