"""Easirent adapter using the live quotereact JSON flow."""

from __future__ import annotations

import logging
import re
import uuid

from app.adapters.base import BaseAdapter
from app.adapters.easirent_reference import (
    build_static_locations,
    resolve_fleet_metadata,
    resolve_location_metadata,
)
from app.adapters.easirent_rules import is_placeholder_vehicle_code, select_account_code
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
    FuelType,
    PaymentOption,
    TransmissionType,
    VehicleCategory,
    category_from_sipp,
)
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import CancellationPolicy, Vehicle, VehicleLocation

logger = logging.getLogger(__name__)

_QUOTE_URL = "https://easirent.com/account/index.php/quotereact/"
_CHECKOUT_URL = "https://www.easirent.com/account/index.php/checkout/index"
_COUNTRY_PARAM_MAP = {"ROI": "IE", "IE": "IE", "US": "US"}


def _parse_decimal(value, default: float = 0.0) -> float:
    if value in (None, "", "?"):
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return default


def _parse_int(value) -> int | None:
    if value in (None, "", "?"):
        return None

    match = re.search(r"\d+", str(value))
    if not match:
        return None

    try:
        return int(match.group())
    except ValueError:
        return None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).replace("\n", " ").split()).strip()


def _strip_or_similar(value: str | None) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return re.sub(r"\s+or similar\s*$", "", text, flags=re.IGNORECASE).strip()


def _parse_transmission(value: str | None) -> TransmissionType | None:
    text = _clean_text(value).lower()
    if not text or text == "null":
        return None
    if "auto" in text:
        return TransmissionType.AUTOMATIC
    if "manual" in text:
        return TransmissionType.MANUAL
    return None


def _parse_fuel(value: str | None) -> FuelType | None:
    text = _clean_text(value).lower()
    if not text or text == "?":
        return None
    if "diesel" in text:
        return FuelType.DIESEL
    if "petrol" in text or "gas" in text:
        return FuelType.PETROL
    if "hybrid" in text:
        return FuelType.HYBRID
    if "electric" in text:
        return FuelType.ELECTRIC
    return None


def _parse_aircon(value: str | None) -> bool | None:
    text = _clean_text(value).lower()
    if not text or text == "?":
        return None
    if "no air con" in text:
        return False
    if "air con" in text:
        return True
    return None


def _parse_bags(value) -> int | None:
    count = _parse_int(value)
    if count is None:
        return None
    if count > 10:
        return None
    return count


def _default_total(raw: dict) -> float:
    for key in ("displayprice", "exTotalPriceDisp", "priceDispPP", "displayexprice", "price"):
        value = _parse_decimal(raw.get(key))
        if value > 0:
            return value
    return 0.0


def _default_daily(raw: dict) -> float:
    for key in ("dailyrate", "exDailyRate", "dailyDispPP", "xsdailyrate", "secpricedaily"):
        value = _parse_decimal(raw.get(key))
        if value > 0:
            return value
    return 0.0


def _country_param(country_code: str | None) -> str | None:
    return _COUNTRY_PARAM_MAP.get((country_code or "").strip().upper())


def _build_products(raw: dict, currency: str) -> tuple[list[dict], str | None]:
    products: list[dict] = []
    default_token = None

    if raw.get("postData1"):
        product_specs = [
            ("BAS", "Super Saver", raw.get("displayprice"), raw.get("dailyrate"), raw.get("postData1")),
            ("SEC", "Super Secure", raw.get("secpricedisp"), raw.get("secpricedaily"), raw.get("postData2")),
            ("REL", "Super Relax", raw.get("displayxsprice"), raw.get("xsdailyrate"), raw.get("postData3")),
        ]
        default_token = raw.get("postData1")
    elif raw.get("postDataPOA") or raw.get("postDataPP"):
        # Our commercial model is always pay-on-arrival for provider inventory.
        # Easirent may send an internal PAY_NOW alternative, but we should not
        # expose or book against it through the Vrooem flow.
        product_specs = [
            ("POA", "Pay at Pick-up", raw.get("displayprice"), raw.get("dailyrate"), raw.get("postDataPOA")),
        ]
        default_token = raw.get("postDataPOA")
    else:
        product_specs = []

    for product_type, name, total_value, daily_value, token in product_specs:
        if not token:
            continue
        total = _parse_decimal(total_value)
        daily = _parse_decimal(daily_value)
        if total <= 0:
            continue
        products.append({
            "type": product_type,
            "name": name,
            "total": total,
            "price_per_day": daily if daily > 0 else None,
            "currency": currency,
            "checkout_url": _CHECKOUT_URL,
            "post_data": token,
        })

    return products, default_token


def _derive_excess_amount(metadata: dict | None, account_code: str | None) -> float | None:
    if not metadata or not account_code:
        return None

    if account_code == "$USA202":
        value = metadata.get("excess_us_domestic")
    elif account_code == "$USA202A":
        value = metadata.get("excess_inbound")
    else:
        value = metadata.get("excess")

    amount = _parse_decimal(value)
    return amount if amount > 0 else None


def _derive_make_model(display_name: str, metadata: dict | None) -> tuple[str, str]:
    if metadata:
        make = _clean_text(metadata.get("example_make"))
        model = _clean_text(metadata.get("example_model"))
        if make or model:
            return make, model

    text = _strip_or_similar(display_name)
    if not text:
        return "", ""

    if "," in text:
        text = text.split(",", 1)[0].strip()

    parts = text.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _quote_name(raw: dict, metadata: dict | None) -> str:
    raw_name = _clean_text(raw.get("model"))
    if raw_name:
        return raw_name

    if metadata:
        example_model = _clean_text(metadata.get("example_model"))
        if example_model:
            return f"{example_model} or similar"

        description = _clean_text(metadata.get("description"))
        if description:
            return f"{description} or similar"

    return "Easirent vehicle"


@register_adapter
class EasirentAdapter(BaseAdapter):
    supplier_id = "easirent"
    supplier_name = "Easirent"
    supports_one_way = True
    default_timeout = 30.0

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        pickup_country = (pickup_entry.country_code or "").strip().upper()
        settings = get_settings()
        account_code = select_account_code(
            customer_country_code=request.country_code,
            pickup_country_code=pickup_country,
            settings=settings,
        )

        if not account_code:
            logger.info(
                "[%s] No eligible account code for customer_country=%s pickup_country=%s",
                self.supplier_id,
                request.country_code,
                pickup_country,
            )
            return []

        country_param = _country_param(pickup_country)
        if not country_param:
            logger.info("[%s] Unsupported pickup country for Easirent: %s", self.supplier_id, pickup_country)
            return []

        raw_results = []
        for vehicle_type in ("1", "2"):
            payload = {
                "VType": vehicle_type,
                "dateformat": "ddmmyyyy",
                "DepotStart": pickup_entry.pickup_id,
                "from": request.pickup_date.isoformat(),
                "HourStart": request.pickup_time.strftime("%H.%M"),
                "DepotReturn": dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id,
                "to": request.dropoff_date.isoformat(),
                "HourReturn": request.dropoff_time.strftime("%H.%M"),
                "promCode": "",
                "driversAge": str(request.driver_age),
                "FuelType": "all",
                "VehicleType": "all",
                "currID": request.currency,
                "country": country_param,
                "vehicleID": "",
            }

            response = await self._request("POST", _QUOTE_URL, data=payload)
            data = response.json()
            if not isinstance(data, dict):
                logger.warning("[%s] Unexpected Easirent response for VType=%s", self.supplier_id, vehicle_type)
                continue

            if data.get("redirect") is True:
                logger.warning("[%s] Easirent returned redirect payload for VType=%s", self.supplier_id, vehicle_type)
                continue

            if int(data.get("success") or 0) != 1:
                description = _clean_text((data.get("error") or {}).get("description"))
                if vehicle_type == "2" and "not available for the location you selected" in description.lower():
                    logger.info("[%s] Easirent vans unavailable for %s", self.supplier_id, pickup_entry.pickup_id)
                else:
                    logger.warning("[%s] Easirent returned unsuccessful search: %s", self.supplier_id, data)
                continue

            raw_results.append(data)

        vehicles: list[Vehicle] = []
        for result in raw_results:
            for quote in result.get("quotes") or []:
                vehicle = self._parse_vehicle(
                    raw=quote,
                    result=result,
                    request=request,
                    account_code=account_code,
                    pickup_entry=pickup_entry,
                    dropoff_entry=dropoff_entry,
                )
                if vehicle is not None:
                    vehicles.append(vehicle)

        logger.info("[%s] Parsed %d Easirent vehicles", self.supplier_id, len(vehicles))
        return vehicles

    def _parse_vehicle(
        self,
        raw: dict,
        result: dict,
        request: SearchRequest,
        account_code: str,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> Vehicle | None:
        if str(raw.get("status") or "").upper() != "A":
            return None

        sipp_code = _clean_text(raw.get("sipp")).upper() or None
        if is_placeholder_vehicle_code(sipp_code):
            return None

        pickup_country = (pickup_entry.country_code or "").strip().upper()
        metadata = resolve_fleet_metadata(pickup_country, sipp_code)
        pickup_metadata = resolve_location_metadata(pickup_country, pickup_entry.pickup_id)
        dropoff_metadata = resolve_location_metadata(
            (dropoff_entry.country_code if dropoff_entry else pickup_country) or pickup_country,
            dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id,
        )

        name = _quote_name(raw, metadata)
        make, model = _derive_make_model(name, metadata)
        total_price = _default_total(raw)
        daily_rate = _default_daily(raw)
        if total_price <= 0:
            return None

        products, default_post_data = _build_products(raw, request.currency)
        if not default_post_data:
            logger.info("[%s] Easirent quote skipped because no pay-on-arrival token is available", self.supplier_id)
            return None
        excess_amount = _derive_excess_amount(metadata, account_code)
        image_url = ""
        if metadata:
            image_url = _clean_text(metadata.get("image_jpg") or metadata.get("image_png"))

        pickup_location = VehicleLocation(
            supplier_location_id=pickup_entry.pickup_id,
            name=pickup_entry.original_name
            or (pickup_metadata or {}).get("name")
            or result.get("depotstart")
            or pickup_entry.pickup_id,
            city=(pickup_metadata or {}).get("city") or "",
            latitude=pickup_entry.latitude if pickup_entry.latitude is not None else (pickup_metadata or {}).get("latitude"),
            longitude=pickup_entry.longitude if pickup_entry.longitude is not None else (pickup_metadata or {}).get("longitude"),
            country_code=pickup_country,
            airport_code=pickup_entry.iata or pickup_entry.pickup_id,
            is_airport=True,
            address=_clean_text((pickup_metadata or {}).get("address")) or None,
            phone=_clean_text(result.get("puphone")) or _clean_text((pickup_metadata or {}).get("phone")) or None,
            pickup_instructions=_clean_text((pickup_metadata or {}).get("pickup_instructions")) or None,
            dropoff_instructions=_clean_text((pickup_metadata or {}).get("dropoff_instructions")) or None,
        )

        dropoff_location = VehicleLocation(
            supplier_location_id=dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id,
            name=(dropoff_entry.original_name if dropoff_entry else pickup_entry.original_name)
            or (dropoff_metadata or {}).get("name")
            or result.get("depotreturn")
            or (dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id),
            city=(dropoff_metadata or {}).get("city") or "",
            latitude=(
                dropoff_entry.latitude if dropoff_entry and dropoff_entry.latitude is not None
                else pickup_entry.latitude if pickup_entry.latitude is not None
                else (dropoff_metadata or {}).get("latitude")
            ),
            longitude=(
                dropoff_entry.longitude if dropoff_entry and dropoff_entry.longitude is not None
                else pickup_entry.longitude if pickup_entry.longitude is not None
                else (dropoff_metadata or {}).get("longitude")
            ),
            country_code=(dropoff_entry.country_code if dropoff_entry else pickup_country) or pickup_country,
            airport_code=(dropoff_entry.iata if dropoff_entry else pickup_entry.iata)
            or (dropoff_entry.pickup_id if dropoff_entry else pickup_entry.pickup_id),
            is_airport=True,
            address=_clean_text((dropoff_metadata or {}).get("address")) or None,
            phone=_clean_text((dropoff_metadata or {}).get("phone")) or None,
            pickup_instructions=_clean_text((dropoff_metadata or {}).get("pickup_instructions")) or None,
            dropoff_instructions=_clean_text((dropoff_metadata or {}).get("dropoff_instructions")) or None,
        )

        supplier_data = {
            "provider_code": self.supplier_id,
            "quote_id": raw.get("quoteID"),
            "search_id": result.get("searchID"),
            "product_id": raw.get("productid") or None,
            "group": raw.get("group"),
            "vehicle_type": raw.get("vtype"),
            "account_code": account_code,
            "quote_source": result.get("source"),
            "pickup_country": result.get("pucountry"),
            "pickup_phone": _clean_text(result.get("puphone")) or None,
            "office_name": (pickup_metadata or {}).get("name"),
            "office_address": _clean_text((pickup_metadata or {}).get("address")) or None,
            "office_phone": _clean_text(result.get("puphone")) or _clean_text((pickup_metadata or {}).get("phone")) or None,
            "pickup_station_name": (pickup_metadata or {}).get("name"),
            "dropoff_station_name": (dropoff_metadata or {}).get("name"),
            "pickup_address": _clean_text((pickup_metadata or {}).get("address")) or None,
            "dropoff_address": _clean_text((dropoff_metadata or {}).get("address")) or None,
            "pickup_instructions": _clean_text((pickup_metadata or {}).get("pickup_instructions")) or None,
            "dropoff_instructions": _clean_text((dropoff_metadata or {}).get("dropoff_instructions")) or None,
            "checkout_url": _CHECKOUT_URL,
            "default_post_data": default_post_data,
            "products": products,
            "excess_amount": excess_amount,
            "availability_status": "available",
            "start_date": request.pickup_date.isoformat(),
            "start_time": request.pickup_time.strftime("%H:%M"),
            "end_date": request.dropoff_date.isoformat(),
            "end_time": request.dropoff_time.strftime("%H:%M"),
        }

        vehicle_kwargs = {
            "id": f"gw_{uuid.uuid4().hex[:16]}",
            "supplier_id": self.supplier_id,
            "supplier_vehicle_id": str(raw.get("id") or raw.get("quoteID") or ""),
            "provider_product_id": _clean_text(raw.get("productid")) or None,
            "provider_rate_id": _clean_text(raw.get("quoteID")) or None,
            "availability_status": "available",
            "name": name,
            "category": VehicleCategory.VAN if str(raw.get("vtype")) == "2" and not sipp_code else category_from_sipp(sipp_code),
            "make": make,
            "model": model,
            "image_url": image_url,
            "pickup_location": pickup_location,
            "dropoff_location": dropoff_location,
            "pricing": Pricing(
                currency=request.currency,
                total_price=total_price,
                daily_rate=daily_rate if daily_rate > 0 else round(total_price / max(int(result.get("hiredays") or 1), 1), 2),
                price_includes_tax=True,
                payment_options=[PaymentOption.PAY_AT_PICKUP],
            ),
            "cancellation_policy": CancellationPolicy(
                free_cancellation=True,
                description="Checkout token issued by Easirent. Final cancellation terms depend on the selected package on Easirent checkout.",
            ),
            "supplier_data": supplier_data,
            "raw_payload": raw,
        }

        transmission = _parse_transmission(raw.get("transmition"))
        if transmission is not None:
            vehicle_kwargs["transmission"] = transmission

        fuel_type = _parse_fuel(raw.get("fuel"))
        if fuel_type is not None:
            vehicle_kwargs["fuel_type"] = fuel_type

        seats = _parse_int(raw.get("people"))
        if seats is not None:
            vehicle_kwargs["seats"] = seats

        doors = _parse_int(raw.get("doors"))
        if doors is not None:
            vehicle_kwargs["doors"] = doors

        bags_large = _parse_bags(raw.get("luggage"))
        if bags_large is not None:
            vehicle_kwargs["bags_large"] = bags_large

        air_conditioning = _parse_aircon(raw.get("aircon"))
        if air_conditioning is not None:
            vehicle_kwargs["air_conditioning"] = air_conditioning

        if sipp_code:
            vehicle_kwargs["sipp_code"] = sipp_code

        if raw.get("payload") or raw.get("loadlength") or raw.get("loadwidth") or raw.get("vanspecial"):
            supplier_data["van_payload"] = _clean_text(raw.get("payload")) or None
            supplier_data["load_length"] = _clean_text(raw.get("loadlength")) or None
            supplier_data["load_width"] = _clean_text(raw.get("loadwidth")) or None
            supplier_data["van_special"] = _clean_text(raw.get("vanspecial")) or None

        if raw.get("pricePP") or raw.get("postDataPP") or raw.get("postDataPOA"):
            supplier_data["us_quote_fields"] = {
                "displayprice": raw.get("displayprice"),
                "dailyrate": raw.get("dailyrate"),
                "priceDispPP": raw.get("priceDispPP"),
                "dailyDispPP": raw.get("dailyDispPP"),
                "postDataPP": raw.get("postDataPP"),
                "postDataPOA": raw.get("postDataPOA"),
            }

        if raw.get("postData1") or raw.get("postData2") or raw.get("postData3"):
            supplier_data["roi_quote_fields"] = {
                "displayprice": raw.get("displayprice"),
                "secpricedisp": raw.get("secpricedisp"),
                "displayxsprice": raw.get("displayxsprice"),
                "postData1": raw.get("postData1"),
                "postData2": raw.get("postData2"),
                "postData3": raw.get("postData3"),
            }

        return Vehicle(**vehicle_kwargs)

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        raise NotImplementedError(
            "Easirent search and checkout-token integration is implemented, but supplier-side booking confirmation is not exposed as an API."
        )

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        raise NotImplementedError(
            "Easirent cancellation integration is not available because Easirent has not provided a cancellable booking API contract."
        )

    async def get_locations(self) -> list[dict]:
        return build_static_locations()

    def _endpoint_url(self) -> str:
        return _QUOTE_URL

    def _build_placeholder_booking_response(self) -> BookingResponse:
        return BookingResponse(
            id="",
            supplier_id=self.supplier_id,
            supplier_booking_id="",
            status=BookingStatus.PENDING,
        )
