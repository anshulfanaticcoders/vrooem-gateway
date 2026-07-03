"""Yesaway adapter - OTA XML over HTTP Basic auth."""

from __future__ import annotations

import logging
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date
from html import escape
from typing import Any

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
    MileagePolicy,
    PaymentOption,
    TransmissionType,
    category_from_sipp,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Fee, Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import Extra, InsuranceOption, Vehicle, VehicleLocation

logger = logging.getLogger(__name__)

OTA_NS = "http://www.opentravel.org/OTA/2003/05"
NS = f"{{{OTA_NS}}}"


class YesawayApiError(RuntimeError):
    """Raised when Yesaway returns an OTA error response."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


RATE_PLANS: list[dict[str, Any]] = [
    {
        "country": "US",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_US_ORDER_BASE_ARRIVAL",
        "tokens": ["MIA", "FLL", "MCO", "LAX", "HNL", "OGG", "TPA"],
        "coverage": CoverageType.BASIC,
        "excess_label": "Up to 3000 USD",
        "deposit_label": "350 - 1000 USD",
        "benefits": ["LDW", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "US",
        "type": "NOP",
        "name": "No Protection",
        "rate_code": "W_US_ORDER_NOPROTECTION_ARRIVAL",
        "tokens": ["MIA", "FLL", "MCO", "LAX", "HNL", "OGG", "TPA"],
        "coverage": CoverageType.BASIC,
        "excess_label": "N/A",
        "deposit_label": "350 - 1000 USD",
        "benefits": ["No insurance included", "Pay on arrival"],
    },
    {
        "country": "NZ",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_NZ_ORDER_BASE_ARRIVAL",
        "tokens": ["CHC", "AKL", "ZQN", "WEL"],
        "coverage": CoverageType.BASIC,
        "excess_label": "Up to 8500 NZD",
        "deposit_label": "1000 NZD",
        "benefits": ["CDW", "Theft Waiver", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "NZ",
        "type": "PLUS",
        "name": "Basic Protection Plus",
        "rate_code": "W_NZ_ORDER_ENBASE_ARRIVAL",
        "tokens": ["CHC", "AKL", "ZQN", "WEL"],
        "coverage": CoverageType.STANDARD,
        "excess_label": "Up to 800 NZD",
        "deposit_label": "500 NZD",
        "benefits": ["CDW", "Theft Waiver", "Third Party Liability", "Reduced excess"],
    },
    {
        "country": "NZ",
        "type": "FULL",
        "name": "Full Protection",
        "rate_code": "W_NZ_ORDER_COM_ARRIVAL",
        "tokens": ["CHC", "AKL", "ZQN", "WEL"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero NZD",
        "deposit_label": "100 - 500 NZD",
        "benefits": ["SCDW", "Theft Waiver", "Third Party Liability", "Zero excess"],
    },
    {
        "country": "AU",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_AU_ORDER_BASE_ARRIVAL",
        "tokens": ["PER", "SYD", "MEL", "AVV", "HBA", "ADL", "OOL", "BNE"],
        "coverage": CoverageType.BASIC,
        "excess_label": "Up to 8000 AUD",
        "deposit_label": "300 - 1100 AUD",
        "benefits": ["CDW", "Theft Waiver", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "AU",
        "type": "PLUS",
        "name": "Basic Protection Plus",
        "rate_code": "W_AU_ORDER_ENBASE_ARRIVAL",
        "tokens": ["PER", "SYD", "MEL", "AVV", "HBA", "ADL", "OOL", "BNE"],
        "coverage": CoverageType.STANDARD,
        "excess_label": "Up to 2000 AUD",
        "deposit_label": "300 - 1100 AUD",
        "benefits": ["CDW", "Theft Waiver", "Third Party Liability", "Reduced excess"],
    },
    {
        "country": "AU",
        "type": "FULL",
        "name": "Full Protection",
        "rate_code": "W_AU_ORDER_COM_ARRIVAL",
        "tokens": ["PER"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero AUD",
        "deposit_label": "300 - 1100 AUD",
        "benefits": ["SCDW", "Theft Waiver", "Third Party Liability", "Zero excess"],
    },
    {
        "country": "TH",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_TH_ORDER_BASE_ARRIVAL",
        "tokens": ["BKK", "HKT", "CNX", "DMK", "KBV"],
        "coverage": CoverageType.BASIC,
        "excess_label": "10000 - 15000 THB",
        "deposit_label": "10000 - 15000 THB",
        "benefits": ["CDW", "Theft Waiver", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "TH",
        "type": "FULL",
        "name": "Basic Protection Plus",
        "rate_code": "W_TH_ORDER_COM_ARRIVAL",
        "tokens": ["BKK", "HKT", "CNX", "DMK", "KBV"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero THB",
        "deposit_label": "10000 - 15000 THB",
        "benefits": ["SCDW", "Theft Waiver", "Third Party Liability", "Zero CDW excess"],
    },
    {
        "country": "MY",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_MY_ORDER_BASE_ARRIVAL",
        "tokens": ["KUL"],
        "coverage": CoverageType.BASIC,
        "excess_label": "4000 - 8000 MYR",
        "deposit_label": "500 - 1000 MYR",
        "benefits": ["CDW", "Theft Waiver", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "MY",
        "type": "FULL",
        "name": "Full Protection",
        "rate_code": "W_MY_ORDER_COM_ARRIVAL",
        "tokens": ["KUL"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero MYR",
        "deposit_label": "500 - 1000 MYR",
        "benefits": ["SCDW", "Theft Waiver", "Third Party Liability", "Zero CDW excess"],
    },
    {
        "country": "JP",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_JP_ORDER_BASE_ARRIVAL",
        "tokens": ["CTS", "OKA", "NRT", "HND", "KIX", "OSAKA", "FUK"],
        "coverage": CoverageType.BASIC,
        "excess_label": "Up to 400000 JPY",
        "deposit_label": "Zero JPY",
        "benefits": ["CDW", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "JP",
        "type": "FULL",
        "name": "Basic Protection Plus",
        "rate_code": "W_JP_ORDER_COM_ARRIVAL",
        "tokens": ["CTS", "OKA", "NRT", "HND", "KIX", "OSAKA", "FUK"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero JPY",
        "deposit_label": "Zero JPY",
        "benefits": ["SCDW", "Third Party Liability", "Zero excess"],
    },
    {
        "country": "KR",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_KR_ORDER_BASE_ARRIVAL",
        "tokens": ["SEOUL", "SOUEL", "ICN", "GMP", "CJU", "PUS"],
        "coverage": CoverageType.BASIC,
        "excess_label": "Up to 2000000 KRW",
        "deposit_label": "0 - 300000 KRW",
        "benefits": ["CDW", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "KR",
        "type": "FULL",
        "name": "Full Coverage",
        "rate_code": "W_KR_ORDER_COM_ARRIVAL",
        "tokens": ["SEOUL", "SOUEL", "ICN", "GMP", "CJU"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero KRW",
        "deposit_label": "0 - 300000 KRW",
        "benefits": ["SCDW", "Third Party Liability", "Zero excess"],
    },
    {
        "country": "CA",
        "type": "NOP",
        "name": "No Protection",
        "rate_code": "W_CA_ORDER_NOPROTECTION_ARRIVAL",
        "tokens": ["YYZ", "YVR"],
        "coverage": CoverageType.BASIC,
        "excess_label": "N/A",
        "deposit_label": "350 - 500 CAD",
        "benefits": ["No insurance included", "Pay on arrival"],
    },
    {
        "country": "CA",
        "type": "BAS",
        "name": "Basic Package",
        "rate_code": "W_CA_ORDER_BASE_ARRIVAL",
        "tokens": ["YYZ", "YVR"],
        "coverage": CoverageType.BASIC,
        "excess_label": "2500 - 3000 CAD",
        "deposit_label": "350 - 500 CAD",
        "benefits": ["CDW", "Third Party Liability", "Theft Protection", "Pay on arrival"],
    },
    {
        "country": "AE",
        "type": "BAS",
        "name": "Basic Protection",
        "rate_code": "W_UAE_ORDER_BASE_ARRIVAL",
        "tokens": ["DXB"],
        "coverage": CoverageType.BASIC,
        "excess_label": "400 USD",
        "deposit_label": "326 - 820 USD",
        "benefits": ["CDW", "Third Party Liability", "Pay on arrival"],
    },
    {
        "country": "AE",
        "type": "FULL",
        "name": "Full Coverage",
        "rate_code": "W_UAE_ORDER_COM_ARRIVAL",
        "tokens": ["DXB"],
        "coverage": CoverageType.FULL,
        "excess_label": "Zero USD",
        "deposit_label": "326 - 820 USD",
        "benefits": ["SCDW", "Third Party Liability", "Zero excess"],
    },
    {
        "country": "ID",
        "type": "BAS",
        "name": "Basic Package",
        "rate_code": "W_IDN_ORDER_BASE_ARRIVAL",
        "tokens": ["DPS"],
        "coverage": CoverageType.BASIC,
        "excess_label": "10000000 IDR",
        "deposit_label": "1000000 IDR",
        "benefits": ["CDW", "Third Party Liability", "Theft Protection", "Pay on arrival"],
    },
]

STOP_SELL_CODES = {"PHO8526S", "AUC2874S"}
CUSTOMER_SELECTABLE_EXTRA_CODES = {"7", "8", "9", "301"}
COUNTRY_ALIASES = {
    "AUS": "AU",
    "UAE": "AE",
    "USA": "US",
    "INDONISIA": "ID",
    "INDONESIA": "ID",
    "IN": "ID",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _normalize_country(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return COUNTRY_ALIASES.get(raw, raw)


def _split_make_model(name: str) -> tuple[str, str]:
    clean = re.sub(r"\s+or\s+similar$", "", name.strip(), flags=re.I)
    parts = clean.split(" ", 1)
    make = parts[0].title() if parts else "Yesaway"
    model = parts[1].title() if len(parts) > 1 else clean.title()
    return make, model


def _local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _text(node: ET.Element | None) -> str:
    return "".join(node.itertext()).strip() if node is not None else ""


@register_adapter
class YesawayAdapter(BaseAdapter):
    supplier_id = "yesaway"
    supplier_name = "Yesaway"
    supports_one_way = True
    default_timeout = 45.0
    location_refresh_timeout_seconds = 90.0

    def _settings(self):
        return get_settings()

    def _base_url(self) -> str:
        return self._settings().yesaway_api_url.rstrip("/")

    def _is_configured(self) -> bool:
        settings = self._settings()
        return bool(
            settings.yesaway_api_url
            and settings.yesaway_username
            and settings.yesaway_password
        )

    async def get_locations(self) -> list[dict]:
        if not self._is_configured():
            return []

        root = await self._post_xml(
            self._branch_request_xml(location_code=""),
            transaction_id="yesaway-location-refresh",
        )
        locations: list[dict] = []
        for loc in root.findall(f".//{NS}LocationDetail"):
            parsed = self._parse_location(loc)
            if parsed and self._rate_plans_for_location_data(parsed):
                locations.append(parsed)

        return locations

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        if not self._is_configured():
            return []

        plans = self._rate_plans_for_entry(pickup_entry)
        if not plans:
            logger.info("[yesaway] No configured rate plans for %s", pickup_entry.pickup_id)
            return []

        offerings: list[tuple[ET.Element, dict[str, Any]]] = []
        for plan in plans:
            try:
                root = await self._post_xml(
                    self._availability_request_xml(request, pickup_entry, dropoff_entry, plan),
                    transaction_id=f"yesaway-search-{plan['rate_code']}",
                )
            except Exception as exc:
                logger.warning("[yesaway] availability failed for %s: %s", plan["rate_code"], exc)
                continue

            for veh_avail in root.findall(f".//{NS}VehAvail"):
                offerings.append((veh_avail, plan))

        grouped: dict[str, dict[str, Any]] = {}
        for raw, plan in offerings:
            key = self._vehicle_group_key(raw)
            product = self._parse_product(raw, plan, request, pickup_entry, dropoff_entry)
            if not product:
                continue

            if key not in grouped:
                grouped[key] = {"raw": raw, "plan": plan, "products": [product]}
            else:
                grouped[key]["products"].append(product)

        vehicles = []
        for group in grouped.values():
            vehicle = self._parse_vehicle(
                group["raw"],
                group["plan"],
                group["products"],
                request,
                pickup_entry,
                dropoff_entry,
            )
            if vehicle:
                vehicles.append(vehicle)

        return vehicles

    async def create_booking(
        self,
        request: CreateBookingRequest,
        vehicle: Vehicle,
    ) -> BookingResponse:
        product = self._selected_product(vehicle, request.package)
        booking_ref = (
            request.laravel_booking_number
            or request.laravel_booking_id
            or uuid.uuid4().hex[:8]
        )
        root = await self._post_xml(
            self._reservation_request_xml(request, vehicle, product),
            transaction_id=f"yesaway-booking-{booking_ref}",
        )

        reservation = root.find(f".//{NS}VehReservation")
        conf = root.find(f".//{NS}ConfID")
        unique = root.find(f".//{NS}UniqueID")
        supplier_booking_id = (
            (conf.get("ID") if conf is not None else None)
            or (unique.get("ID") if unique is not None else None)
            or ""
        ).strip()

        if not supplier_booking_id:
            raise YesawayApiError("Yesaway did not return a supplier booking id")

        pickup_dt = self._parse_datetime_attr(root.find(f".//{NS}VehRentalCore"), "PickUpDateTime")
        dropoff_dt = self._parse_datetime_attr(root.find(f".//{NS}VehRentalCore"), "ReturnDateTime")

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=supplier_booking_id,
            status=BookingStatus.CONFIRMED,
            vehicle_name=vehicle.name,
            pickup_datetime=pickup_dt,
            dropoff_datetime=dropoff_dt,
            pickup_location=vehicle.pickup_location.name,
            dropoff_location=(vehicle.dropoff_location or vehicle.pickup_location).name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            cancellation_policy="Cancel with supplier policy.",
            supplier_data={
                "provider_status": "confirmed",
                "rate_code": product.get("rate_code"),
                "package_code": product.get("package_code"),
                "supplier_response": self._safe_response_summary(root),
                "reservation_present": reservation is not None,
            },
            provider_status="confirmed",
        )

    async def cancel_booking(
        self,
        supplier_booking_id: str,
        request: CancelBookingRequest,
    ) -> CancelBookingResponse:
        root = await self._post_xml(
            self._cancel_request_xml(supplier_booking_id),
            transaction_id=f"yesaway-cancel-{supplier_booking_id}",
        )
        cancel_core = root.find(f".//{NS}VehCancelRSCore")
        cancel_id = root.find(f".//{NS}UniqueID")
        cancel_fee = root.find(f".//{NS}CancelFee")

        status = (cancel_core.get("CancelStatus") if cancel_core is not None else "") or ""
        if status.strip().lower() != "cancelled":
            raise YesawayApiError(f"Yesaway cancellation did not confirm: {status or 'unknown'}")

        cancellation_currency = (
            cancel_fee.get("CurrencyCode") if cancel_fee is not None else None
        ) or ""

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            cancellation_fee=_safe_float(cancel_fee.get("Amount") if cancel_fee is not None else 0),
            cancellation_currency=cancellation_currency,
            supplier_cancellation_id=(cancel_id.get("ID") if cancel_id is not None else "") or "",
        )

    async def _post_xml(self, xml: str, transaction_id: str) -> ET.Element:
        response = await self._request(
            "POST",
            self._base_url(),
            content=xml.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "Accept": "text/xml",
            },
            auth=(self._settings().yesaway_username, self._settings().yesaway_password),
        )
        if response.status_code != 200:
            raise YesawayApiError(f"Yesaway HTTP {response.status_code}")

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise YesawayApiError("Yesaway returned invalid XML") from exc

        errors = self._extract_errors(root)
        if errors:
            code, message = errors[0]
            raise YesawayApiError(f"Yesaway error {code or 'unknown'}: {message}", code)

        if root.find(f".//{NS}Success") is None:
            raise YesawayApiError(f"Yesaway response missing Success for {transaction_id}")

        return root

    def _soap(self, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<soap:Body>{body}</soap:Body></soap:Envelope>"
        )

    def _pos_xml(self, package_code: str, include_country: bool = True) -> str:
        settings = self._settings()
        country = (
            f' ISOCountry="{escape(settings.yesaway_source_country or "NL")}"'
            if include_country
            else ""
        )
        return (
            "<POS>"
            f"<Source{country}>"
            f'<RequestorID Type="4" ID="{escape(package_code)}">'
            f'<CompanyName Code="{escape(settings.yesaway_company_code)}" '
            f'CompanyShortName="{escape(settings.yesaway_company_name)}"/>'
            "</RequestorID>"
            "</Source>"
            "<Source>"
            f'<RequestorID Type="4" ID="{escape(settings.yesaway_iata_number or "00000000")}" '
            'ID_Context="IATA"/>'
            "</Source>"
            "</POS>"
        )

    def _branch_request_xml(self, location_code: str) -> str:
        settings = self._settings()
        package_code = RATE_PLANS[0]["rate_code"]
        body = (
            f'<OTA_VehLocSearchRQ xmlns="{OTA_NS}" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'PrimaryLangID="EN" MaxResponses="500" Target="Production" Version="3.0" '
            'TransactionIdentifier="yesaway-location-refresh">'
            f"{self._pos_xml(package_code)}"
            "<VehLocSearchCriterion>"
            f'<Location Code="{escape(location_code)}"/>'
            "</VehLocSearchCriterion>"
            f'<Vendor Code="{escape(settings.yesaway_vendor_code)}"/>'
            "</OTA_VehLocSearchRQ>"
        )
        return self._soap(body)

    def _availability_request_xml(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
        plan: dict[str, Any],
    ) -> str:
        settings = self._settings()
        pickup_code = pickup_entry.pickup_id
        dropoff_code = dropoff_entry.pickup_id if dropoff_entry else pickup_code
        rate_code = plan["rate_code"]
        pickup_time = request.pickup_time.strftime("%H:%M:%S")
        dropoff_time = request.dropoff_time.strftime("%H:%M:%S")
        pickup_dt = f"{request.pickup_date.isoformat()}T{pickup_time}"
        dropoff_dt = f"{request.dropoff_date.isoformat()}T{dropoff_time}"
        body = (
            f'<OTA_VehAvailRateMoreRQ xmlns="{OTA_NS}" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'PrimaryLangID="EN" MaxResponses="50" Target="Production" Version="3.0" '
            f'TransactionIdentifier="yesaway-search-{escape(rate_code)}">'
            f"{self._pos_xml(rate_code)}"
            '<VehAvailRQCore Status="Available">'
            f'<VehRentalCore PickUpDateTime="{pickup_dt}" ReturnDateTime="{dropoff_dt}">'
            f'<PickUpLocation LocationCode="{escape(pickup_code)}"/>'
            f'<ReturnLocation LocationCode="{escape(dropoff_code)}"/>'
            "</VehRentalCore>"
            "<VendorPrefs>"
            f'<VendorPref Code="{escape(settings.yesaway_vendor_code)}"/>'
            "</VendorPrefs>"
            f'<RateQualifier RateQualifier="{escape(rate_code)}" '
            f'CorpDiscountNmbr="{escape(rate_code)}"/>'
            f'<DriverType Age="{int(request.driver_age or 30)}"/>'
            '<TPA_Extensions><TPA_Extension_Flags EnhancedTotalPrice="true"/></TPA_Extensions>'
            "</VehAvailRQCore>"
            "</OTA_VehAvailRateMoreRQ>"
        )
        return self._soap(body)

    def _reservation_request_xml(
        self,
        request: CreateBookingRequest,
        vehicle: Vehicle,
        product: dict[str, Any],
    ) -> str:
        settings = self._settings()
        driver = request.driver
        first = driver.first_name or "Guest"
        last = driver.last_name or "Customer"
        rate_code = str(product.get("rate_code") or product.get("package_code") or "").strip()
        pickup_code = str(
            product.get("pickup_code") or vehicle.pickup_location.supplier_location_id
        ).strip()
        dropoff_code = str(
            product.get("dropoff_code")
            or (vehicle.dropoff_location.supplier_location_id if vehicle.dropoff_location else "")
            or pickup_code
        ).strip()
        pickup_dt = self._booking_datetime(request.pickup_date, request.pickup_time)
        dropoff_dt = self._booking_datetime(request.dropoff_date, request.dropoff_time)
        phone_area, phone_number = self._split_phone(driver.phone)
        license_number = driver.driving_license_number or "UNKNOWN"
        country = (driver.country or settings.yesaway_source_country or "NL").upper()
        voucher_suffix = request.laravel_booking_id or uuid.uuid4().hex[:8]
        voucher = request.laravel_booking_number or f"VROOEM-{voucher_suffix}"
        remarks = f"Vrooem booking {voucher}"
        if "test" in driver.email.lower() or "test" in f"{first} {last}".lower():
            remarks = f"TEST - {remarks}"
        date_of_birth = escape(self._date_of_birth(driver.age, driver.date_of_birth))
        phone_attrs = (
            f'PhoneUseType="3" AreaCityCode="{escape(phone_area)}" '
            f'PhoneNumber="{escape(phone_number)}"'
        )

        extras_xml = "".join(
            f'<SpecialEquipPref EquipType="{escape(self._extra_equip_type(extra.extra_id))}" '
            f'Quantity="{int(extra.quantity)}" PayType="arrival"/>'
            for extra in request.extras
            if self._extra_equip_type(extra.extra_id)
        )

        body = (
            f'<OTA_VehResRQ xmlns="{OTA_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'PrimaryLangID="EN" MaxResponses="50" Target="Production" Version="3.0" '
            f'TransactionIdentifier="yesaway-booking-{escape(voucher)}">'
            f"{self._pos_xml(rate_code)}"
            '<VehResRQCore Status="Available">'
            f'<VehRentalCore PickUpDateTime="{pickup_dt}" ReturnDateTime="{dropoff_dt}">'
            f'<PickUpLocation LocationCode="{escape(pickup_code)}"/>'
            f'<ReturnLocation LocationCode="{escape(dropoff_code)}"/>'
            "</VehRentalCore>"
            "<Customer><Primary><PersonName>"
            "<NamePrefix>Mr.</NamePrefix>"
            f"<GivenName>{escape(first)}</GivenName>"
            f"<Surname>{escape(last)}</Surname>"
            f"<CnName>{escape(first)} {escape(last)}</CnName>"
            f"<DateOfBirth>{date_of_birth}</DateOfBirth>"
            "</PersonName>"
            f"<Telephone {phone_attrs}/>"
            f"<LocalPhone {phone_attrs}/>"
            f"<Email>{escape(str(driver.email))}</Email>"
            "<Address>"
            f"<AddressLine>{escape(driver.address or 'Not provided')}</AddressLine>"
            f"<CityName>{escape(driver.city or 'Not provided')}</CityName>"
            f"<PostalCode>{escape(driver.postal_code or '00000')}</PostalCode>"
            '<StateProv StateCode=""/>'
            f'<CountryName Code="{escape(country)}"/>'
            "</Address>"
            '<UserMember IsNew="true" Level=""/>'
            "</Primary></Customer>"
            f'<VendorPref Code="{escape(settings.yesaway_vendor_code)}"/>'
            f'<VehPref AirConditionInd="{str(bool(product.get("air_conditioning"))).lower()}" '
            f'TransmissionType="{escape(product.get("transmission") or "Automatic")}" '
            f'Code="{escape(product.get("sipp_code") or vehicle.sipp_code or "")}" '
            f'VehGroupID="{escape(str(product.get("vehicle_group_id") or ""))}">'
            f'<VehType VehicleCategory="{escape(str(product.get("vehicle_category") or ""))}"/>'
            f'<VehClass Size="{escape(str(product.get("vehicle_class_size") or ""))}"/>'
            "</VehPref>"
            f'<RateQualifier RateQualifier="{escape(rate_code)}" '
            f'CorpDiscountNmbr="{escape(str(product.get("discount_code") or rate_code))}"/>'
            f'<DriverType Age="{int(driver.age or 30)}" DriverLicenseType="3" '
            f'DriverLicenseName="{escape(country)}" '
            f'DriverLicenseNumber="{escape(license_number)}"/>'
            f"<SpecialEquipPrefs>{extras_xml}</SpecialEquipPrefs>"
            "<TPA_Extensions>"
            '<TPA_Extension_Flags EnhancedTotalPrice="true"/>'
            '<TPA_Extension_Test_Flag Type="production"/>'
            f"<TPA_Extension_Remark>{escape(remarks)}</TPA_Extension_Remark>"
            "<TPA_Extension_Whether_To_Pay>true</TPA_Extension_Whether_To_Pay>"
            "</TPA_Extensions>"
            "</VehResRQCore>"
            "<VehResRQInfo>"
            f'<ArrivalDetails TransportationCode="14" '
            f'Number="{escape(request.flight_number or "")}">'
            '<OperatingCompany Code=""/>'
            "</ArrivalDetails>"
            f'<RentalPaymentPref><Voucher SeriesCode="{escape(voucher)}"/></RentalPaymentPref>'
            "</VehResRQInfo>"
            "</OTA_VehResRQ>"
        )
        return self._soap(body)

    def _cancel_request_xml(self, supplier_booking_id: str) -> str:
        settings = self._settings()
        package_code = RATE_PLANS[0]["rate_code"]
        body = (
            f'<OTA_VehCancelRQ xmlns="{OTA_NS}" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'Target="Production" Version="3.0" TransactionIdentifier="yesaway-cancel">'
            f"{self._pos_xml(package_code, include_country=False)}"
            '<VehCancelRQCore CancelType="Cancel">'
            f'<UniqueID Type="14" ID="{escape(supplier_booking_id)}"/>'
            "<PersonName><GivenName>Vrooem</GivenName><Surname>Customer</Surname></PersonName>"
            "</VehCancelRQCore>"
            "<VehCancelRQInfo>"
            f'<Vendor Code="{escape(settings.yesaway_vendor_code)}"/>'
            "</VehCancelRQInfo>"
            "</OTA_VehCancelRQ>"
        )
        return self._soap(body)

    def _parse_location(self, loc: ET.Element) -> dict | None:
        code = (loc.get("Code") or "").strip()
        name = (loc.get("Name") or "").strip()
        if not code or not name:
            return None
        if code in STOP_SELL_CODES or "test" in name.lower():
            return None

        address = loc.find(f"{NS}Address")
        city = address.find(f"{NS}CityName") if address is not None else None
        country = address.find(f"{NS}CountryName") if address is not None else None
        address_line = address.find(f"{NS}AddressLine") if address is not None else None
        telephone = loc.find(f"{NS}Telephone")
        coordinate = loc.find(f"{NS}Coordinate")
        country_code = _normalize_country(country.get("Code") if country is not None else "")
        latitude = coordinate.get("Latitude") if coordinate is not None else None
        longitude = coordinate.get("Longitude") if coordinate is not None else None

        return {
            "provider_location_id": code,
            "name": name,
            "city": city.get("Code") if city is not None else "",
            "country": country_code,
            "country_code": country_code,
            "latitude": _safe_float(latitude, 0),
            "longitude": _safe_float(longitude, 0),
            "location_type": "airport" if _truthy(loc.get("AtAirport")) else "downtown",
            "iata": (loc.get("AirportCode") or "").strip() or None,
            "address": _text(address_line),
            "phone": self._format_branch_phone(telephone),
            "supports_one_way": True,
            "provider_code": self._settings().yesaway_vendor_code,
        }

    def _rate_plans_for_entry(self, entry: ProviderLocationEntry) -> list[dict[str, Any]]:
        return self._rate_plans_for_location_data(
            {
                "provider_location_id": entry.pickup_id,
                "name": entry.original_name,
                "city": entry.original_name,
                "country_code": entry.country_code,
                "iata": entry.iata,
            }
        )

    def _rate_plans_for_location_data(self, location: dict) -> list[dict[str, Any]]:
        code = str(location.get("provider_location_id") or "").strip()
        if code in STOP_SELL_CODES:
            return []

        haystack = " ".join(
            str(location.get(key) or "").upper()
            for key in ("provider_location_id", "name", "city", "iata")
        )
        country = _normalize_country(location.get("country_code") or location.get("country"))

        plans = []
        for plan in RATE_PLANS:
            if country and country != plan["country"]:
                continue
            if any(token.upper() in haystack for token in plan["tokens"]):
                plans.append(plan)
        return plans

    def _vehicle_group_key(self, raw: ET.Element) -> str:
        model = raw.find(f".//{NS}VehMakeModel")
        if model is not None:
            group = model.get("VehGroupID") or ""
            sipp = model.get("Code") or ""
            if group or sipp:
                return f"{group}:{sipp}"
        return raw.get("Unique_id") or uuid.uuid4().hex

    def _parse_product(
        self,
        raw: ET.Element,
        plan: dict[str, Any],
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> dict[str, Any] | None:
        total = raw.find(f"{NS}VehAvailCore/{NS}TotalCharge")
        vehicle = raw.find(f"{NS}VehAvailCore/{NS}Vehicle")
        model = raw.find(f".//{NS}VehMakeModel")
        veh_type = raw.find(f".//{NS}VehType")
        veh_class = raw.find(f".//{NS}VehClass")
        if total is None or vehicle is None or model is None:
            return None

        rental_days = max(1, (request.dropoff_date - request.pickup_date).days)
        total_amount = _safe_float(total.get("TotalAmount") or total.get("EstimatedTotalAmount"))
        currency = total.get("CurrencyCode") or request.currency
        excess = self._coverage_excess(raw, plan)
        deposit = (
            _safe_float(total.get("Deposit"))
            if total.get("Deposit") not in (None, "")
            else None
        )

        return {
            "type": plan["type"],
            "name": plan["name"],
            "rate_code": plan["rate_code"],
            "package_code": raw.get("package_code") or plan["rate_code"],
            "unique_id": raw.get("Unique_id") or "",
            "discount_code": raw.get("discount_code") or plan["rate_code"],
            "total": total_amount,
            "price_per_day": round(total_amount / rental_days, 2),
            "currency": currency,
            "deposit": deposit,
            "deposit_currency": currency,
            "excess": excess,
            "excess_label": plan.get("excess_label"),
            "deposit_label": plan.get("deposit_label"),
            "benefits": plan.get("benefits", []),
            "payment_type": "arrival",
            "prepaid_total_amount": _safe_float(total.get("PrepaidTotalAmount")),
            "pod_total_amount": _safe_float(total.get("PODTotalAmount")),
            "pickup_code": pickup_entry.pickup_id,
            "dropoff_code": dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id,
            "vehicle_group_id": model.get("VehGroupID") or "",
            "sipp_code": model.get("Code") or "",
            "vehicle_category": veh_type.get("VehicleCategory") if veh_type is not None else "",
            "vehicle_class_size": veh_class.get("Size") if veh_class is not None else "",
            "transmission": vehicle.get("TransmissionType") or "",
            "air_conditioning": _truthy(vehicle.get("AirConditionInd")),
        }

    def _parse_vehicle(
        self,
        raw: ET.Element,
        plan: dict[str, Any],
        products: list[dict[str, Any]],
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None,
    ) -> Vehicle | None:
        vehicle = raw.find(f"{NS}VehAvailCore/{NS}Vehicle")
        model = raw.find(f".//{NS}VehMakeModel")
        if vehicle is None or model is None or not products:
            return None

        base_product = products[0]
        name = model.get("Name") or "Yesaway vehicle"
        sipp = model.get("Code") or ""
        make, model_name = _split_make_model(name)
        rental_days = max(1, (request.dropoff_date - request.pickup_date).days)
        pickup_location = self._vehicle_location_from_entry(pickup_entry)
        dropoff_location = (
            self._vehicle_location_from_entry(dropoff_entry) if dropoff_entry else None
        )
        veh_type = vehicle.find(f"{NS}VehType")
        veh_core = raw.find(f"{NS}VehAvailCore")

        fees = self._parse_fees(raw)
        extras = self._parse_extras(raw, rental_days, base_product["currency"])
        insurance_options = self._parse_insurance_options(raw, plan, base_product["currency"])

        supplier_data = {
            "provider_code": self._settings().yesaway_vendor_code,
            "rate_code": base_product["rate_code"],
            "package_code": base_product["package_code"],
            "unique_id": base_product["unique_id"],
            "discount_code": base_product["discount_code"],
            "vehicle_group_id": base_product["vehicle_group_id"],
            "pickup_code": base_product["pickup_code"],
            "dropoff_code": base_product["dropoff_code"],
            "source_country": self._settings().yesaway_source_country,
            "company_code": self._settings().yesaway_company_code,
            "company_name": self._settings().yesaway_company_name,
            "iata_number": self._settings().yesaway_iata_number,
            "products": products,
            "product_data": dict(base_product),
            "booking_total": base_product["total"],
            "deposit_amount": base_product.get("deposit"),
            "deposit_currency": base_product["currency"],
            "excess_amount": base_product.get("excess"),
            "excess_label": base_product.get("excess_label"),
            "deposit_label": base_product.get("deposit_label"),
            "pickup_station_name": pickup_location.name,
            "pickup_address": pickup_location.address,
            "pickup_phone": pickup_location.phone,
            "dropoff_station_name": (dropoff_location or pickup_location).name,
            "dropoff_address": (dropoff_location or pickup_location).address,
            "dropoff_phone": (dropoff_location or pickup_location).phone,
            "fuel_policy": "Full to full",
            "raw_package_codes": [product["package_code"] for product in products],
        }

        return Vehicle(
            id=f"gw_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_vehicle_id=base_product["vehicle_group_id"] or base_product["unique_id"],
            provider_product_id=base_product["rate_code"],
            provider_rate_id=base_product["unique_id"],
            name=name,
            category=category_from_sipp(sipp),
            make=make,
            model=model_name,
            image_url=_text(vehicle.find(f"{NS}PictureURL")),
            transmission=self._parse_transmission(vehicle.get("TransmissionType")),
            seats=_safe_int(vehicle.get("PassengerQuantity")) or None,
            doors=_safe_int(veh_type.get("DoorCount") if veh_type is not None else None) or None,
            bags_large=_safe_int(vehicle.get("BaggageQuantity")) or None,
            air_conditioning=_truthy(vehicle.get("AirConditionInd")),
            mileage_policy=self._parse_mileage_policy(raw),
            sipp_code=sipp or None,
            availability_status=veh_core.get("Status") if veh_core is not None else None,
            pickup_location=pickup_location,
            dropoff_location=dropoff_location,
            pricing=Pricing(
                currency=base_product["currency"],
                total_price=base_product["total"],
                daily_rate=base_product["price_per_day"],
                price_includes_tax=True,
                fees=fees,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
                deposit_amount=base_product.get("deposit"),
                deposit_currency=base_product["currency"],
            ),
            insurance_options=insurance_options,
            extras=extras,
            supplier_data=supplier_data,
            raw_payload={
                "package_code": base_product["package_code"],
                "unique_id": base_product["unique_id"],
            },
            min_driver_age=self._driver_age(raw, "AllowAgeStart"),
            max_driver_age=self._driver_age(raw, "AllowAgeEnd"),
        )

    def _vehicle_location_from_entry(self, entry: ProviderLocationEntry | None) -> VehicleLocation:
        if entry is None:
            return VehicleLocation()

        return VehicleLocation(
            supplier_location_id=entry.pickup_id,
            name=entry.original_name or entry.pickup_id,
            country_code=entry.country_code or "",
            latitude=entry.latitude,
            longitude=entry.longitude,
            airport_code=entry.iata,
            is_airport=bool(entry.iata),
            location_type="airport" if entry.iata else "downtown",
        )

    def _parse_fees(self, raw: ET.Element) -> list[Fee]:
        fees = []
        for fee in raw.findall(f"{NS}VehAvailCore/{NS}Fees/{NS}Fee"):
            fees.append(
                Fee(
                    name=fee.get("Description") or "Fee",
                    amount=_safe_float(fee.get("Amount")),
                    currency=fee.get("CurrencyCode") or "EUR",
                    included_in_total=_truthy(fee.get("IncludedInEstTotalInd")),
                    description=fee.get("Purpose") or "",
                )
            )
        return fees

    def _parse_extras(self, raw: ET.Element, rental_days: int, currency: str) -> list[Extra]:
        extras = []
        for item in raw.findall(f"{NS}VehExtraInfo/{NS}SpecialEquipList/{NS}SpecialEquip"):
            equip_type = str(item.get("EquipType") or "").strip()
            if equip_type not in CUSTOMER_SELECTABLE_EXTRA_CODES:
                continue

            amount = _safe_float(item.get("Amount"))
            per = str(item.get("Per") or "Order").lower()
            total = amount * rental_days if per == "day" else amount
            description = item.get("Description") or f"Extra {equip_type}"
            max_charge = (
                _safe_float(item.get("CappedPrice")) if item.get("CappedPrice") else None
            )

            extras.append(
                Extra(
                    id=f"ext_yesaway_{equip_type}",
                    name=description.title(),
                    daily_rate=amount if per == "day" else 0,
                    total_price=round(total, 2),
                    currency=item.get("CurrencyCode") or currency,
                    max_quantity=max(1, _safe_int(item.get("MaxQuantity"), 1)),
                    type=ExtraType.EQUIPMENT,
                    mandatory=False,
                    description=description,
                    supplier_data={
                        "code": equip_type,
                        "equip_type": equip_type,
                        "amount": amount,
                        "per": per,
                        "pricing_type": "per_day" if per == "day" else "per_booking",
                        "chargeable_days": rental_days if per == "day" else 1,
                        "pay_type": "arrival",
                        "is_capped": _truthy(item.get("IsCapped")),
                        "max_charge": max_charge,
                    },
                )
            )
        return extras

    def _parse_insurance_options(
        self,
        raw: ET.Element,
        plan: dict[str, Any],
        currency: str,
    ) -> list[InsuranceOption]:
        options = []
        for coverage in raw.findall(f"{NS}VehAvailInfo/{NS}PricedCoverages/{NS}PricedCoverage"):
            charge = coverage.find(f"{NS}Charge")
            cov = coverage.find(f"{NS}Coverage")
            if charge is None:
                continue
            name = charge.get("Description") or "Included coverage"
            coverage_id = cov.get("CoverageType") if cov is not None else len(options)
            excess_amount = (
                _safe_float(charge.get("LiabilityAmount"))
                if charge.get("LiabilityAmount")
                else None
            )
            options.append(
                InsuranceOption(
                    id=f"ins_yesaway_{coverage_id}",
                    coverage_type=plan["coverage"],
                    name=name,
                    daily_rate=_safe_float(charge.get("UnitPrice")),
                    total_price=_safe_float(charge.get("Amount")),
                    currency=charge.get("CurrencyCode") or currency,
                    excess_amount=excess_amount,
                    included=_truthy(charge.get("IncludedInRate")),
                    description=name,
                )
            )
        return options

    def _coverage_excess(self, raw: ET.Element, plan: dict[str, Any]) -> float | None:
        if str(plan.get("excess_label") or "").lower().startswith("zero"):
            return 0
        coverage_path = f"{NS}VehAvailInfo/{NS}PricedCoverages/{NS}PricedCoverage/{NS}Charge"
        values = [
            _safe_float(charge.get("LiabilityAmount"))
            for charge in raw.findall(coverage_path)
            if charge.get("LiabilityAmount") not in (None, "")
        ]
        values = [value for value in values if value > 0]
        return max(values) if values else None

    def _parse_mileage_policy(self, raw: ET.Element) -> MileagePolicy | None:
        distance = raw.find(f"{NS}VehAvailCore/{NS}RentalRate/{NS}RateDistance")
        if distance is None:
            return None
        if _truthy(distance.get("Unlimited")):
            return MileagePolicy.UNLIMITED
        return MileagePolicy.LIMITED

    def _parse_transmission(self, value: str | None) -> TransmissionType | None:
        if not value:
            return None
        return TransmissionType.AUTOMATIC if "auto" in value.lower() else TransmissionType.MANUAL

    def _driver_age(self, raw: ET.Element, attr: str) -> int | None:
        age = raw.find(f"{NS}DrivingAgeInfo/{NS}AllowableAgeRange")
        return _safe_int(age.get(attr)) if age is not None and age.get(attr) else None

    def _selected_product(self, vehicle: Vehicle, package: str | None) -> dict[str, Any]:
        supplier_data = vehicle.supplier_data or {}
        products = (
            supplier_data.get("products") if isinstance(supplier_data.get("products"), list) else []
        )
        requested = str(package or "").strip().upper()
        for product in products:
            if str(product.get("type") or "").upper() == requested:
                return product
        for product in products:
            if str(product.get("rate_code") or "").upper() == requested:
                return product
        if isinstance(supplier_data.get("selected_product"), dict):
            return supplier_data["selected_product"]
        if isinstance(supplier_data.get("product_data"), dict):
            return supplier_data["product_data"]
        if products:
            return products[0]
        return supplier_data

    def _extract_errors(self, root: ET.Element) -> list[tuple[str | None, str]]:
        errors = []
        for node in root.iter():
            if _local_name(node) != "Error":
                continue
            errors.append((node.get("Code"), _text(node)))
        return errors

    def _safe_response_summary(self, root: ET.Element) -> dict[str, Any]:
        conf = root.find(f".//{NS}ConfID")
        unique = root.find(f".//{NS}UniqueID")
        return {
            "conf_id": conf.get("ID") if conf is not None else None,
            "conf_status": conf.get("Status") if conf is not None else None,
            "unique_id": unique.get("ID") if unique is not None else None,
        }

    def _booking_datetime(self, booking_date: date | None, booking_time: str | None) -> str:
        if booking_date is None:
            raise YesawayApiError("Missing booking date for Yesaway reservation")
        clean_time = (booking_time or "09:00").strip()
        if len(clean_time) == 5:
            clean_time = f"{clean_time}:00"
        return f"{booking_date.isoformat()}T{clean_time}"

    def _date_of_birth(self, age: int, explicit: str | None) -> str:
        if explicit:
            return explicit
        return f"{date.today().year - int(age or 30)}-01-01"

    def _split_phone(self, phone: str) -> tuple[str, str]:
        raw = str(phone or "").strip()
        digits = re.sub(r"\D+", "", raw)
        if not raw.startswith("+") or len(digits) < 8:
            return "", digits or raw
        if digits.startswith("1"):
            return "1", digits[1:]
        return digits[:2], digits[2:]

    def _extra_equip_type(self, extra_id: str) -> str:
        value = str(extra_id or "").strip()
        if value.startswith("ext_yesaway_"):
            return value.replace("ext_yesaway_", "", 1)
        return value

    def _format_branch_phone(self, telephone: ET.Element | None) -> str:
        if telephone is None:
            return ""
        phone = telephone.get("Phone") or telephone.get("PhoneNumber") or ""
        code = telephone.get("PhoneCode") or telephone.get("PhoneTechCode") or ""
        return f"{code}{phone}".strip()

    def _parse_datetime_attr(self, node: ET.Element | None, attr: str):
        if node is None or not node.get(attr):
            return None
        from datetime import datetime

        value = node.get(attr).replace(" ", "T")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
