"""Builders for API-facing SearchVehicle payloads."""

from app.schemas.common import FuelType, VehicleCategory
from app.schemas.search import SearchResponse, SupplierResult
from app.schemas.search_vehicle_payload import (
    SearchVehicleBookingContextPayload,
    SearchVehicleLocationPayload,
    SearchVehicleLocationPointPayload,
    SearchVehiclePayload,
    SearchVehiclePoliciesPayload,
    SearchVehiclePricingPayload,
    SearchVehicleProviderStatusPayload,
    SearchVehicleResponsePayload,
    SearchVehicleSpecsPayload,
    SearchVehicleSupplierResultPayload,
    SearchVehicleUiPlaceholdersPayload,
)
from app.schemas.vehicle import Extra, Vehicle, VehicleLocation


_SOURCE_MAP = {
    "green_motion": "greenmotion",
    "ok_mobility": "okmobility",
}


def _normalize_source(source: str) -> str:
    return _SOURCE_MAP.get(source, source)


def _is_explicit(vehicle: Vehicle, field: str) -> bool:
    return field in vehicle.model_fields_set


def _explicit_string(vehicle: Vehicle, field: str) -> str | None:
    if not _is_explicit(vehicle, field):
        return None

    value = getattr(vehicle, field)
    if not value:
        return None

    return str(value)


def _explicit_int(vehicle: Vehicle, field: str) -> int | None:
    if not _is_explicit(vehicle, field):
        return None

    value = getattr(vehicle, field)
    return int(value) if value is not None else None


def _explicit_bool(vehicle: Vehicle, field: str) -> bool | None:
    if not _is_explicit(vehicle, field):
        return None

    value = getattr(vehicle, field)
    return bool(value) if value is not None else None


def _build_specs(vehicle: Vehicle) -> SearchVehicleSpecsPayload:
    transmission = None
    if _is_explicit(vehicle, "transmission") and vehicle.transmission is not None:
        transmission = vehicle.transmission.value

    fuel = None
    if _is_explicit(vehicle, "fuel_type") and vehicle.fuel_type is not None and vehicle.fuel_type != FuelType.UNKNOWN:
        fuel = vehicle.fuel_type.value

    return SearchVehicleSpecsPayload(
        transmission=transmission,
        fuel=fuel,
        seating_capacity=_explicit_int(vehicle, "seats"),
        doors=_explicit_int(vehicle, "doors"),
        luggage_small=_explicit_int(vehicle, "bags_small"),
        luggage_medium=None,
        luggage_large=_explicit_int(vehicle, "bags_large"),
        air_conditioning=_explicit_bool(vehicle, "air_conditioning"),
        sipp_code=_explicit_string(vehicle, "sipp_code"),
    )


def _build_pricing(vehicle: Vehicle) -> SearchVehiclePricingPayload:
    supplier_data = vehicle.supplier_data or {}

    return SearchVehiclePricingPayload(
        currency=vehicle.pricing.currency,
        price_per_day=vehicle.pricing.daily_rate,
        total_price=vehicle.pricing.total_price,
        deposit_amount=vehicle.pricing.deposit_amount,
        deposit_currency=vehicle.pricing.deposit_currency,
        excess_amount=supplier_data.get("excess_amount"),
        excess_theft_amount=supplier_data.get("excess_theft_amount"),
    )


def _build_location_point(location: VehicleLocation | None) -> SearchVehicleLocationPointPayload:
    if location is None:
        return SearchVehicleLocationPointPayload()

    return SearchVehicleLocationPointPayload(
        provider_location_id=location.supplier_location_id or None,
        name=location.name or None,
        latitude=location.latitude,
        longitude=location.longitude,
    )


def _build_policies(vehicle: Vehicle) -> SearchVehiclePoliciesPayload:
    mileage_policy = None
    if _is_explicit(vehicle, "mileage_policy") and vehicle.mileage_policy is not None:
        mileage_policy = vehicle.mileage_policy.value

    mileage_limit_km = None
    if _is_explicit(vehicle, "mileage_limit_km"):
        mileage_limit_km = vehicle.mileage_limit_km

    cancellation = None
    if vehicle.cancellation_policy is not None:
        cancellation = vehicle.cancellation_policy.model_dump(mode="json")

    fuel_policy = None
    supplier_data = vehicle.supplier_data or {}
    if supplier_data.get("fuel_policy"):
        fuel_policy = supplier_data["fuel_policy"]

    return SearchVehiclePoliciesPayload(
        mileage_policy=mileage_policy,
        mileage_limit_km=mileage_limit_km,
        fuel_policy=fuel_policy,
        cancellation=cancellation,
    )


def _build_extras_preview(extras: list[Extra]) -> list[dict]:
    preview: list[dict] = []
    for extra in extras:
        preview.append(
            {
                "id": extra.id,
                "name": extra.name,
                "type": extra.type.value,
                "currency": extra.currency,
                "daily_rate": extra.daily_rate,
                "total_price": extra.total_price,
                "mandatory": extra.mandatory,
            }
        )
    return preview





def _build_data_quality_flags(vehicle: Vehicle) -> list[str]:
    flags: list[str] = []

    if not _is_explicit(vehicle, "transmission"):
        flags.append("missing_transmission")
    if not _is_explicit(vehicle, "fuel_type") or vehicle.fuel_type == FuelType.UNKNOWN:
        flags.append("missing_fuel")
    if not _is_explicit(vehicle, "seats"):
        flags.append("missing_seating_capacity")
    if not _is_explicit(vehicle, "doors"):
        flags.append("missing_doors")
    if not _is_explicit(vehicle, "air_conditioning"):
        flags.append("missing_air_conditioning")
    if not _is_explicit(vehicle, "mileage_policy"):
        flags.append("missing_mileage_policy")

    return flags


_PRODUCT_NAME_MAP = {
    "BAS": "Basic",
    "MED": "Medium",
    "PRE": "Premium",
    "PMP": "Premium Plus",
    "PLU": "Plus",
}


def _build_products(vehicle: Vehicle) -> list[dict]:
    supplier_data = vehicle.supplier_data or {}
    raw_products = supplier_data.get("products") or []
    if not isinstance(raw_products, list):
        return []

    rental_days = 0
    if vehicle.pricing.daily_rate:
        rental_days = max(1, round(vehicle.pricing.total_price / vehicle.pricing.daily_rate))

    products: list[dict] = []
    for raw_product in raw_products:
        if not isinstance(raw_product, dict):
            continue

        product_type = str(raw_product.get("type") or "BAS").upper()
        total = float(raw_product.get("total") or 0)
        price_per_day = raw_product.get("price_per_day")
        if price_per_day is None:
            price_per_day = round(total / rental_days, 2) if rental_days > 0 else total

        products.append(
            {
                "type": product_type,
                "name": raw_product.get("name") or _PRODUCT_NAME_MAP.get(product_type, product_type.title()),
                "total": total,
                "price_per_day": float(price_per_day),
                "currency": raw_product.get("currency") or vehicle.pricing.currency,
                "deposit": raw_product.get("deposit"),
                "excess": raw_product.get("excess"),
                "fuel_policy": raw_product.get("fuelpolicy") or raw_product.get("fuel_policy"),
                "mileage_limit_km": raw_product.get("mileage") if raw_product.get("mileage") not in (None, "") else None,
                "cost_per_extra_km": raw_product.get("costperextradistance") or raw_product.get("cost_per_extra_km"),
                "minimum_driver_age": raw_product.get("minage") if raw_product.get("minage") not in (None, "") else None,
                "debit_card_required": raw_product.get("debitcard") or None,
            }
        )

    return products

def build_search_vehicle_payload(vehicle: Vehicle) -> SearchVehiclePayload:
    source = _normalize_source(vehicle.supplier_id)
    image = _explicit_string(vehicle, "image_url")
    supplier_data = vehicle.supplier_data or {}

    category = None
    if _is_explicit(vehicle, "category") or vehicle.category != VehicleCategory.OTHER:
        category = vehicle.category.value

    provider_product_id = vehicle.provider_product_id or supplier_data.get("product_id")
    provider_rate_id = vehicle.provider_rate_id or supplier_data.get("rate_id") or supplier_data.get("vendor_rate_id")
    availability_status = vehicle.availability_status or supplier_data.get("availability_status") or supplier_data.get("availability")

    return SearchVehiclePayload(
        id=vehicle.id,
        gateway_vehicle_id=vehicle.id,
        provider_vehicle_id=vehicle.supplier_vehicle_id or None,
        provider_product_id=provider_product_id,
        provider_rate_id=provider_rate_id,
        source=source,
        provider_code=supplier_data.get("provider_code") or source,
        display_name=vehicle.name,
        availability_status=availability_status,
        brand=_explicit_string(vehicle, "make"),
        model=_explicit_string(vehicle, "model"),
        category=category,
        image=image,
        specs=_build_specs(vehicle),
        pricing=_build_pricing(vehicle),
        policies=_build_policies(vehicle),
        products=_build_products(vehicle),
        extras_preview=_build_extras_preview(vehicle.extras),
        location=SearchVehicleLocationPayload(
            pickup=_build_location_point(vehicle.pickup_location),
            dropoff=_build_location_point(vehicle.dropoff_location),
        ),
        data_quality_flags=_build_data_quality_flags(vehicle),
        pricing_transparency_flags=[],
        ui_placeholders=SearchVehicleUiPlaceholdersPayload(image=image is None),
        booking_context=SearchVehicleBookingContextPayload(
            version=1,
            provider_payload=dict(supplier_data),
        ),
    )


def _build_supplier_result_payload(result: SupplierResult) -> SearchVehicleSupplierResultPayload:
    return SearchVehicleSupplierResultPayload(
        supplier_id=_normalize_source(result.supplier_id),
        vehicle_count=result.vehicle_count,
        response_time_ms=result.response_time_ms,
        error=result.error,
        from_cache=result.from_cache,
    )


def _build_provider_status_payload(failure) -> SearchVehicleProviderStatusPayload:
    return SearchVehicleProviderStatusPayload(
        provider=_normalize_source(failure.provider),
        stage=failure.stage,
        failure_type=failure.failure_type,
        http_status=failure.http_status,
        provider_code=failure.provider_code,
        message=failure.message,
        retryable=failure.retryable,
        raw_excerpt=failure.raw_excerpt,
    )


def build_search_vehicle_response(response: SearchResponse) -> SearchVehicleResponsePayload:
    return SearchVehicleResponsePayload(
        search_id=response.search_id,
        vehicles=[build_search_vehicle_payload(vehicle) for vehicle in response.vehicles],
        total_vehicles=response.total_vehicles,
        suppliers_queried=response.suppliers_queried,
        suppliers_responded=response.suppliers_responded,
        supplier_results=[_build_supplier_result_payload(result) for result in response.supplier_results],
        provider_status=[_build_provider_status_payload(failure) for failure in response.provider_status],
        from_cache=response.from_cache,
        response_time_ms=response.response_time_ms,
    )
