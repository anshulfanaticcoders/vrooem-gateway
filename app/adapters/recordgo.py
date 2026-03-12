"""Record Go adapter — REST JSON with Azure APIM subscription key."""

import logging
import time
import uuid

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

# Country code → sell code mapping
_SELL_CODES: dict[str, int] = {
    "ES": 95,   # Spain mainland
    "IC": 96,   # Canary Islands (non-standard ISO)
    "IT": 101,  # Italy
    "GR": 108,  # Greece
    "PT": 110,  # Portugal
}


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


def _parse_transmission(gearbox_type: str | None) -> TransmissionType:
    """Parse gearboxType from RecordGo response."""
    if gearbox_type and gearbox_type.lower() == "automatic":
        return TransmissionType.AUTOMATIC
    return TransmissionType.MANUAL


def _parse_fuel_type(sipp: str | None) -> FuelType:
    """Derive fuel type from 4th character of SIPP/ACRISS code.

    H = hybrid, E = electric, D = diesel. Default to unknown.
    """
    if not sipp or len(sipp) < 4:
        return FuelType.UNKNOWN
    fourth = sipp[3].upper()
    if fourth == "E":
        return FuelType.ELECTRIC
    if fourth == "H":
        return FuelType.HYBRID
    if fourth == "D":
        return FuelType.DIESEL
    return FuelType.UNKNOWN


def _parse_mileage_policy(km_policy: dict | None) -> tuple[MileagePolicy, int | None]:
    """Parse mileage policy from product kmPolicyComercial."""
    if not km_policy:
        return MileagePolicy.UNLIMITED, None
    name = (km_policy.get("kmPolicyTransName") or "").lower()
    if "unlimited" in name:
        return MileagePolicy.UNLIMITED, None
    km_limited = _safe_int(km_policy.get("kmLimited"))
    km_max_daily = _safe_int(km_policy.get("kmMaxDaily"))
    if km_limited > 0 or km_max_daily > 0:
        return MileagePolicy.LIMITED, km_limited or km_max_daily or None
    return MileagePolicy.UNLIMITED, None


def _extract_preauth_excess(included_complements: list[dict]) -> dict:
    """Extract deposit (preauth), excess, and excess_low amounts from included complements."""
    deposit = None
    excess = None
    excess_low = None
    for comp in included_complements:
        if not isinstance(comp, dict):
            continue
        entries = comp.get("preauth&Excess") or comp.get("preauthExcess") or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_type = (entry.get("type") or "").lower()
            value = entry.get("value")
            if value is None:
                continue
            value = float(value)
            if "preauth" in entry_type:
                deposit = max(deposit, value) if deposit is not None else value
            elif "excesslow" in entry_type:
                excess_low = max(excess_low, value) if excess_low is not None else value
            elif "excess" in entry_type:
                excess = max(excess, value) if excess is not None else value
    return {"deposit": deposit, "excess": excess, "excess_low": excess_low}


def _map_complement_type(category: str | None) -> ExtraType:
    """Map RecordGo complement category to ExtraType."""
    cat = (category or "").upper()
    if cat == "COVERAGE":
        return ExtraType.INSURANCE
    if cat == "FEE":
        return ExtraType.FEE
    return ExtraType.EQUIPMENT


@register_adapter
class RecordGoAdapter(BaseAdapter):
    supplier_id = "recordgo"
    supplier_name = "Record Go"
    supports_one_way = True
    default_timeout = 20.0

    # ─── Auth ───

    def _auth_headers(self) -> dict[str, str]:
        """Build headers with subscription key only (no OAuth needed)."""
        settings = get_settings()
        return {
            "Ocp-Apim-Subscription-Key": settings.recordgo_subscription_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api_base(self) -> str:
        """Return the base API URL with /brokers suffix."""
        settings = get_settings()
        base = settings.recordgo_api_url.rstrip("/")
        if not base.endswith("/brokers"):
            base += "/brokers"
        return base

    # ─── Search ───

    async def search_vehicles(
        self,
        request: SearchRequest,
        pickup_entry: ProviderLocationEntry,
        dropoff_entry: ProviderLocationEntry | None = None,
    ) -> list[Vehicle]:
        settings = get_settings()
        base_url = self._api_base()
        headers = self._auth_headers()

        pickup_branch = int(pickup_entry.pickup_id)
        dropoff_branch = int(dropoff_entry.pickup_id) if dropoff_entry else pickup_branch

        # Resolve country code and sell code
        country_code = (request.country_code or "").upper()
        sell_code = _SELL_CODES.get(country_code)
        if not sell_code:
            logger.warning("[recordgo] No sell code for country %s, skipping", country_code)
            return []

        pickup_dt = f"{request.pickup_date.isoformat()}T{request.pickup_time.strftime('%H:%M:%S')}"
        dropoff_dt = f"{request.dropoff_date.isoformat()}T{request.dropoff_time.strftime('%H:%M:%S')}"

        payload = {
            "partnerUser": settings.recordgo_partner_user,
            "country": country_code,
            "sellCode": sell_code,
            "pickupBranch": pickup_branch,
            "dropoffBranch": dropoff_branch,
            "pickupDateTime": pickup_dt,
            "dropoffDateTime": dropoff_dt,
            "driverAge": request.driver_age,
            "language": "EN",
        }

        response = await self._request(
            "POST",
            f"{base_url}/booking_getAvailability/",
            json=payload,
            headers=headers,
        )

        data = response.json()

        # RecordGo wraps responses: check for API-level errors
        if not isinstance(data, dict):
            return []

        status_payload = data.get("status")
        if isinstance(status_payload, dict):
            id_status = status_payload.get("idStatus")
            if id_status is not None and int(id_status) != 200:
                logger.warning(
                    "[recordgo] API error status %s: %s",
                    id_status,
                    status_payload.get("detailedStatus", ""),
                )
                return []

        sell_code_ver = data.get("sellCodeVer")
        acriss_list = data.get("acriss") or []
        if not isinstance(acriss_list, list):
            return []

        rental_days = (request.dropoff_date - request.pickup_date).days or 1

        vehicles: list[Vehicle] = []
        for acriss in acriss_list:
            if not isinstance(acriss, dict):
                continue
            if not acriss.get("available", True):
                continue

            parsed = self._parse_acriss_vehicles(
                acriss=acriss,
                request=request,
                rental_days=rental_days,
                pickup_entry=pickup_entry,
                sell_code_ver=sell_code_ver,
                sell_code=sell_code,
                country_code=country_code,
                pickup_branch=pickup_branch,
                dropoff_branch=dropoff_branch,
            )
            vehicles.extend(parsed)

        # Fetch purchasable extras (associated complements) per product
        # Cache by (productId, acrissCode) to avoid duplicate calls
        complements_cache: dict[str, tuple[list[Extra], dict]] = {}
        for vehicle in vehicles:
            sd = vehicle.supplier_data
            product_id = sd.get("product_id")
            acriss_code = sd.get("acriss_code", "")
            cache_key = f"{product_id}|{acriss_code}|{pickup_branch}|{dropoff_branch}"

            if cache_key not in complements_cache:
                try:
                    purchasable, raw_data = await self._fetch_associated_complements(
                        base_url=base_url,
                        headers=headers,
                        sell_code=sell_code,
                        sell_code_ver=sell_code_ver,
                        pickup_branch=pickup_branch,
                        dropoff_branch=dropoff_branch,
                        product_id=product_id,
                        product_ver=sd.get("product_ver"),
                        rate_prod_ver=sd.get("rate_prod_ver", ""),
                        acriss_code=acriss_code,
                        partner_user=settings.recordgo_partner_user,
                        country=country_code,
                        pickup_datetime=pickup_dt,
                        dropoff_datetime=dropoff_dt,
                        driver_age=request.driver_age,
                    )
                    complements_cache[cache_key] = (purchasable, raw_data)
                except Exception as exc:
                    logger.warning("[recordgo] Failed to fetch complements for %s: %s", cache_key, exc)
                    complements_cache[cache_key] = ([], {})

            # Merge purchasable extras into the vehicle's extras list
            associated_extras, raw_complement_data = complements_cache.get(cache_key, ([], {}))
            if associated_extras:
                vehicle.extras = list(vehicle.extras) + associated_extras
            # Store raw complement data in supplier_data.product_data for frontend
            if raw_complement_data and isinstance(sd.get("product_data"), dict):
                sd["product_data"]["complements_associated"] = raw_complement_data.get("associated", [])
                sd["product_data"]["complements_automatic"] = raw_complement_data.get("automatic", [])

        return vehicles

    def _parse_acriss_vehicles(
        self,
        acriss: dict,
        request: SearchRequest,
        rental_days: int,
        pickup_entry: ProviderLocationEntry,
        sell_code_ver: str | None,
        sell_code: int,
        country_code: str,
        pickup_branch: int,
        dropoff_branch: int,
    ) -> list[Vehicle]:
        """Parse an ACRISS group into one Vehicle per product (rate tier)."""
        acriss_code = acriss.get("acrissCode", "")
        acriss_id = acriss.get("acrissId")
        seats = _safe_int(acriss.get("acrissSeats"), 5)
        doors = _safe_int(acriss.get("acrissDoors"), 4)
        bags = _safe_int(acriss.get("acrissSuitcase"), 0)
        gearbox = acriss.get("gearboxType")

        # Resolve image and display name from imagesArray
        images = acriss.get("imagesArray") or []
        image_url = ""
        display_name = ""
        for img in images:
            if not isinstance(img, dict):
                continue
            if img.get("isDefault"):
                image_url = img.get("acrissImgUrl", "")
                display_name = img.get("acrissDisplayName", "")
                break
        if not image_url and images:
            first_img = images[0] if isinstance(images[0], dict) else {}
            image_url = first_img.get("acrissImgUrl", "")
            display_name = first_img.get("acrissDisplayName", "")

        vehicle_name = display_name or acriss_code
        name_parts = display_name.split(" ", 1) if display_name else []
        make = name_parts[0].title() if name_parts else ""
        model = name_parts[1].title() if len(name_parts) > 1 else ""

        products = acriss.get("products") or []
        if not isinstance(products, list) or not products:
            return []

        vehicles: list[Vehicle] = []

        for product_data in products:
            if not isinstance(product_data, dict):
                continue

            product = product_data.get("product") or {}
            product_id = product.get("productId")
            product_ver = product.get("productVer")
            product_name = product.get("productName", "")
            rate_prod_ver = product_data.get("rateProdVer")

            # Pricing
            total_price = _safe_float(
                product_data.get("priceTaxIncBookingDiscount")
                or product_data.get("priceTaxIncBooking")
            )
            daily_rate = _safe_float(
                product_data.get("priceTaxIncDayDiscount")
                or product_data.get("priceTaxIncDay")
            )
            if total_price <= 0 and daily_rate > 0:
                total_price = round(daily_rate * rental_days, 2)
            if daily_rate <= 0 and total_price > 0:
                daily_rate = round(total_price / rental_days, 2)

            # Mileage
            km_policy = product.get("kmPolicyComercial") or product.get("kmPolicyCommercial")
            mileage_policy, mileage_limit = _parse_mileage_policy(km_policy)

            # Age restrictions
            min_age = _safe_int(product.get("minAgeProduct")) or None
            max_age = _safe_int(product.get("maxAgeProduct")) or None

            # Insurance / preauth+excess from included complements
            included_complements = product.get("productComplementsIncluded") or []
            if not isinstance(included_complements, list):
                included_complements = []
            automatic_complements = product.get("productComplementsAutom") or []
            if not isinstance(automatic_complements, list):
                automatic_complements = []

            preauth_excess = _extract_preauth_excess(included_complements)
            deposit = preauth_excess.get("deposit")
            excess = preauth_excess.get("excess")

            # Build insurance options from included complements
            insurance_options: list[InsuranceOption] = []
            for comp in included_complements:
                if not isinstance(comp, dict):
                    continue
                cat = (comp.get("complementCategory") or "").upper()
                if cat != "COVERAGE":
                    continue
                comp_name = comp.get("complementName", "Basic Cover")
                insurance_options.append(
                    InsuranceOption(
                        id=f"ins_{self.supplier_id}_{comp.get('complementId', 'basic')}",
                        coverage_type=CoverageType.BASIC if "basic" in comp_name.lower() else CoverageType.STANDARD,
                        name=comp_name,
                        daily_rate=0,
                        total_price=_safe_float(comp.get("priceTaxIncComplement")),
                        excess_amount=excess,
                        included=True,
                    )
                )

            # Build extras from included non-coverage complements
            extras: list[Extra] = []
            for comp in included_complements:
                if not isinstance(comp, dict):
                    continue
                cat = (comp.get("complementCategory") or "").upper()
                if cat == "COVERAGE":
                    continue
                extras.append(
                    Extra(
                        id=f"ext_{self.supplier_id}_{comp.get('complementId', 'inc')}",
                        name=comp.get("complementName", "Included Extra"),
                        daily_rate=0,
                        total_price=_safe_float(comp.get("priceTaxIncComplement")),
                        type=_map_complement_type(comp.get("complementCategory")),
                        mandatory=True,
                        description=comp.get("complementDescription") or None,
                    )
                )

            pickup_loc = VehicleLocation(
                supplier_location_id=str(pickup_branch),
                name=pickup_entry.original_name,
                country_code=country_code,
                latitude=pickup_entry.latitude,
                longitude=pickup_entry.longitude,
            )

            full_name = f"{vehicle_name} - {product_name}" if product_name else vehicle_name

            vehicles.append(
                Vehicle(
                    id=f"gw_{uuid.uuid4().hex[:16]}",
                    supplier_id=self.supplier_id,
                    supplier_vehicle_id=f"{acriss_code}_{product_id}_{rate_prod_ver}",
                    name=full_name,
                    category=category_from_sipp(acriss_code),
                    make=make,
                    model=model,
                    image_url=image_url,
                    transmission=_parse_transmission(gearbox),
                    fuel_type=_parse_fuel_type(acriss_code),
                    seats=seats,
                    doors=doors,
                    bags_large=bags,
                    bags_small=0,
                    air_conditioning=True,
                    mileage_policy=mileage_policy,
                    mileage_limit_km=mileage_limit,
                    sipp_code=acriss_code or None,
                    pickup_location=pickup_loc,
                    pricing=Pricing(
                        currency="EUR",
                        total_price=total_price,
                        daily_rate=daily_rate,
                        deposit_amount=deposit,
                        deposit_currency="EUR" if deposit else None,
                        payment_options=[PaymentOption.PAY_AT_PICKUP],
                    ),
                    insurance_options=insurance_options,
                    extras=extras,
                    cancellation_policy=None,  # API does not return cancellation terms
                    supplier_data={
                        "acriss_id": acriss_id,
                        "acriss_code": acriss_code,
                        "product_id": product_id,
                        "product_ver": product_ver,
                        "product_name": product_name,
                        "rate_prod_ver": rate_prod_ver,
                        "sell_code": sell_code,
                        "sell_code_ver": sell_code_ver,
                        "country": country_code,
                        "pickup_branch": pickup_branch,
                        "dropoff_branch": dropoff_branch,
                        "partner_user": get_settings().recordgo_partner_user,
                        "booking_total": total_price,
                        "automatic_complements": automatic_complements,
                        "included_complements": included_complements,
                        # Full product data for frontend recordgo_products grouping
                        "product_data": {
                            "type": f"RG_{product_id}_{rate_prod_ver}",
                            "name": product_name,
                            "subtitle": product.get("productSubtitle"),
                            "description": product.get("productDescription"),
                            "total": total_price,
                            "price_per_day": daily_rate,
                            "product_id": product_id,
                            "product_ver": product_ver,
                            "rate_prod_ver": rate_prod_ver,
                            "deposit": deposit,
                            "excess": excess,
                            "excess_low": preauth_excess.get("excess_low"),
                            "complements_autom": automatic_complements,
                            "complements_included": included_complements,
                            "refuel_policy": product.get("refuelPolicyCommercial"),
                            "km_policy": product.get("kmPolicyCommercial") or product.get("kmPolicyComercial"),
                        },
                    },
                    min_driver_age=min_age,
                    max_driver_age=max_age,
                )
            )

        return vehicles

    # ─── Associated Complements (purchasable extras) ───

    async def _fetch_associated_complements(
        self,
        base_url: str,
        headers: dict,
        sell_code: int,
        sell_code_ver: str | None,
        pickup_branch: int,
        dropoff_branch: int,
        product_id: int,
        product_ver: int,
        rate_prod_ver: str,
        acriss_code: str,
        partner_user: str,
        country: str = "",
        pickup_datetime: str = "",
        dropoff_datetime: str = "",
        driver_age: int | None = None,
    ) -> tuple[list[Extra], dict]:
        """Fetch purchasable extras via booking_getAssociatedComplements.

        Payload matches PHP: partnerUser, country, sellCode, pickupBranch,
        dropoffBranch, pickupDateTime, dropoffDateTime, driverAge, productId,
        acrissCode, language.

        Returns (extras_list, raw_complements_list).
        """
        payload: dict = {
            "partnerUser": partner_user,
            "country": country,
            "sellCode": sell_code,
            "pickupBranch": pickup_branch,
            "dropoffBranch": dropoff_branch,
            "pickupDateTime": pickup_datetime,
            "dropoffDateTime": dropoff_datetime,
            "productId": product_id,
            "acrissCode": acriss_code,
            "language": "EN",
        }
        if driver_age is not None:
            payload["driverAge"] = driver_age
        # Remove empty/None values (matches PHP's array_filter)
        payload = {k: v for k, v in payload.items() if v is not None and v != ""}
        try:
            response = await self._request(
                "POST",
                f"{base_url}/booking_getAssociatedComplements/",
                json=payload,
                headers=headers,
            )
            data = response.json()
        except Exception as exc:
            logger.warning("[recordgo] getAssociatedComplements failed: %s", exc)
            return [], []

        if not isinstance(data, dict):
            return [], []

        # Check API status
        status = data.get("status")
        if isinstance(status, dict) and status.get("idStatus") not in (None, 200):
            logger.warning("[recordgo] getAssociatedComplements status: %s", status)
            return [], []

        complements = (
            data.get("productAssociatedComplements")
            or data.get("complements")
            or data.get("associatedComplements")
            or []
        )
        if not isinstance(complements, list):
            return [], []

        # Also extract automatic complements from response
        auto_complements = data.get("productAutomaticComplements") or []

        extras: list[Extra] = []
        for comp in complements:
            if not isinstance(comp, dict):
                continue
            comp_id = comp.get("complementId")
            comp_name = comp.get("complementName", "Extra")
            price = _safe_float(comp.get("priceTaxIncComplement") or comp.get("price"))
            max_qty = _safe_int(comp.get("maxUnits") or comp.get("maxQuantity"), 1)
            cat = (comp.get("complementCategory") or "").upper()

            extras.append(Extra(
                id=f"ext_{self.supplier_id}_{comp_id}",
                name=comp_name,
                daily_rate=0,
                total_price=price,
                type=_map_complement_type(cat) if cat != "COVERAGE" else ExtraType.EQUIPMENT,
                max_quantity=max_qty,
                mandatory=False,
                description=comp.get("complementDescription") or None,
            ))

        # Return raw data for storage in supplier_data
        raw_data = {
            "associated": complements,
            "automatic": auto_complements if isinstance(auto_complements, list) else [],
        }
        return extras, raw_data

    # ─── Booking ───

    async def create_booking(self, request: CreateBookingRequest, vehicle: Vehicle) -> BookingResponse:
        settings = get_settings()
        base_url = self._api_base()
        headers = self._auth_headers()
        sd = vehicle.supplier_data

        # Build complements from selected extras
        associated_complements = []
        for extra in request.extras:
            associated_complements.append({
                "complementId": extra.extra_id,
                "complementUnits": extra.quantity,
            })

        # Include automatic complements from search
        automatic_complements = sd.get("automatic_complements") or []

        # Build pickup/dropoff datetimes from request
        pickup_dt = ""
        dropoff_dt = ""
        if request.pickup_date and request.dropoff_date:
            pickup_time_str = request.pickup_time or "09:00"
            dropoff_time_str = request.dropoff_time or "09:00"
            pickup_dt = f"{request.pickup_date.isoformat()}T{pickup_time_str}:00"
            dropoff_dt = f"{request.dropoff_date.isoformat()}T{dropoff_time_str}:00"

        payload = {
            "partnerUser": settings.recordgo_partner_user,
            "country": sd.get("country", ""),
            "sellCode": sd.get("sell_code"),
            "sellCodeVer": sd.get("sell_code_ver"),
            "partnerBookingCode": request.laravel_booking_id or f"gw_{uuid.uuid4().hex[:12]}",
            "bookingDate": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pickupBranch": sd.get("pickup_branch"),
            "dropoffBranch": sd.get("dropoff_branch"),
            "pickupDateTime": pickup_dt,
            "dropoffDateTime": dropoff_dt,
            "driverAge": request.driver.age,
            "travelInfoCode": request.flight_number or None,
            "customer": {
                "name": f"{request.driver.first_name} {request.driver.last_name}",
                "email": request.driver.email,
                "phone": request.driver.phone or None,
            },
            "productId": sd.get("product_id"),
            "productVer": sd.get("product_ver"),
            "rateProdVer": sd.get("rate_prod_ver"),
            "acrissCode": sd.get("acriss_code"),
            "productAutomaticComplements": automatic_complements,
            "productAssociatedComplements": associated_complements,
            "bookingTotalAmount": sd.get("booking_total", vehicle.pricing.total_price),
            "finalCustomerTotalAmount": vehicle.pricing.total_price,
        }

        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            response = await self._request(
                "POST",
                f"{base_url}/booking_store/",
                json=payload,
                headers=headers,
            )
            data = response.json()
        except (httpx.HTTPError, Exception) as exc:
            logger.error("[recordgo] booking_store failed: %s", exc)
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"error": str(exc)},
            )

        if not isinstance(data, dict):
            return BookingResponse(
                id=f"bk_{uuid.uuid4().hex[:16]}",
                supplier_id=self.supplier_id,
                supplier_booking_id="",
                status=BookingStatus.FAILED,
                vehicle_name=vehicle.name,
                total_price=vehicle.pricing.total_price,
                currency=vehicle.pricing.currency,
                supplier_data={"raw_response": str(data)},
            )

        # Check API-level status
        status_obj = data.get("status")
        if isinstance(status_obj, dict):
            id_status = status_obj.get("idStatus")
            if id_status is not None and int(id_status) != 200:
                return BookingResponse(
                    id=f"bk_{uuid.uuid4().hex[:16]}",
                    supplier_id=self.supplier_id,
                    supplier_booking_id="",
                    status=BookingStatus.FAILED,
                    vehicle_name=vehicle.name,
                    total_price=vehicle.pricing.total_price,
                    currency=vehicle.pricing.currency,
                    supplier_data=data,
                )

        voucher = data.get("numVoucher") or data.get("voucherNumber") or ""

        return BookingResponse(
            id=f"bk_{uuid.uuid4().hex[:16]}",
            supplier_id=self.supplier_id,
            supplier_booking_id=str(voucher),
            status=BookingStatus.CONFIRMED if voucher else BookingStatus.PENDING,
            vehicle_name=vehicle.name,
            total_price=vehicle.pricing.total_price,
            currency=vehicle.pricing.currency,
            supplier_data=data,
        )

    # ─── Cancel ───

    async def cancel_booking(
        self, supplier_booking_id: str, request: CancelBookingRequest
    ) -> CancelBookingResponse:
        settings = get_settings()
        base_url = self._api_base()
        headers = self._auth_headers()

        payload = {
            "partnerUser": settings.recordgo_partner_user,
            "numVoucher": supplier_booking_id,
        }

        try:
            response = await self._request(
                "POST",
                f"{base_url}/booking_update/",
                json={**payload, "bookingStatus": "CANCELLED"},
                headers=headers,
            )
            data = response.json()
        except (httpx.HTTPError, Exception) as exc:
            logger.error("[recordgo] cancel_booking failed: %s", exc)
            return CancelBookingResponse(
                id=supplier_booking_id,
                status=BookingStatus.FAILED,
                supplier_cancellation_id="",
            )

        return CancelBookingResponse(
            id=supplier_booking_id,
            status=BookingStatus.CANCELLED,
            supplier_cancellation_id=supplier_booking_id,
        )

    # ─── Locations ───

    async def get_locations(self) -> list[dict]:
        """RecordGo locations are managed via unified_locations.json with numeric
        branch IDs (5-digit, country-prefixed). This adapter doesn't expose a
        dynamic location endpoint — locations are statically mapped.

        Return the known branch patterns for reference/syncing.
        """
        return [
            {"provider": self.supplier_id, "provider_location_id": "30001", "name": "Athens Airport", "country_code": "GR", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "30002", "name": "Thessaloniki Airport", "country_code": "GR", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "30003", "name": "Zakynthos Airport", "country_code": "GR", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "30004", "name": "Rhodes Airport", "country_code": "GR", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "34901", "name": "Tenerife South Airport", "country_code": "IC", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "34902", "name": "Las Palmas Airport", "country_code": "IC", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "34903", "name": "Lanzarote Airport", "country_code": "IC", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "34904", "name": "Chafiras", "country_code": "IC", "location_type": "other"},
            {"provider": self.supplier_id, "provider_location_id": "35001", "name": "Lisbon Airport", "country_code": "PT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "35002", "name": "Faro Airport", "country_code": "PT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "35003", "name": "Porto Airport", "country_code": "PT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "39001", "name": "Palermo Airport", "country_code": "IT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "39002", "name": "Catania Airport", "country_code": "IT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "39003", "name": "Olbia Airport", "country_code": "IT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "39004", "name": "Cagliari Airport", "country_code": "IT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "39005", "name": "Rome Fiumicino Airport", "country_code": "IT", "location_type": "airport"},
            {"provider": self.supplier_id, "provider_location_id": "39006", "name": "Milan Bergamo Airport", "country_code": "IT", "location_type": "airport"},
        ]
