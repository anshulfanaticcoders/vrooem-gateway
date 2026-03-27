from app.schemas.common import FuelType, MileagePolicy, TransmissionType
from app.schemas.pricing import Pricing
from app.schemas.vehicle import Vehicle, VehicleLocation
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def _make_vehicle(**overrides) -> Vehicle:
    base = {
        "id": "gw_123",
        "supplier_id": "green_motion",
        "supplier_vehicle_id": "gm_123",
        "name": "Toyota Aygo or similar",
        "pricing": Pricing(currency="EUR", total_price=120.0, daily_rate=40.0),
        "pickup_location": VehicleLocation(
            id="loc_1",
            supplier_location_id="gm_airport",
            name="Marrakech Airport",
            city="Marrakech",
            country_code="MA",
            latitude=31.6069,
            longitude=-8.0363,
        ),
        "supplier_data": {"booking_token": "opaque-booking-data"},
    }
    base.update(overrides)
    return Vehicle(**base)


def test_build_search_vehicle_payload_exposes_card_fields_and_booking_context() -> None:
    payload = build_search_vehicle_payload(_make_vehicle())

    assert payload.display_name == "Toyota Aygo or similar"
    assert payload.pricing is not None
    assert payload.location is not None
    assert payload.location.pickup.provider_location_id == "gm_airport"
    assert payload.booking_context is not None
    assert payload.booking_context.provider_payload == {"booking_token": "opaque-booking-data"}

    payload_dump = payload.model_dump()
    assert "booking_token" not in payload_dump
    assert "supplier_data" not in payload_dump


def test_build_search_vehicle_payload_nulls_unset_internal_specs() -> None:
    payload = build_search_vehicle_payload(_make_vehicle())

    assert payload.specs.transmission is None
    assert payload.specs.fuel is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.luggage_small is None
    assert payload.specs.luggage_medium is None
    assert payload.specs.luggage_large is None
    assert payload.specs.air_conditioning is None
    assert payload.specs.sipp_code is None
    assert payload.policies.mileage_policy is None
    assert payload.policies.mileage_limit_km is None


def test_build_search_vehicle_payload_preserves_explicit_specs() -> None:
    payload = build_search_vehicle_payload(
        _make_vehicle(
            transmission=TransmissionType.AUTOMATIC,
            fuel_type=FuelType.ELECTRIC,
            seats=2,
            doors=3,
            bags_small=1,
            bags_large=2,
            air_conditioning=False,
            mileage_policy=MileagePolicy.LIMITED,
            mileage_limit_km=250,
            sipp_code="ECAR",
        )
    )

    assert payload.specs.transmission == TransmissionType.AUTOMATIC.value
    assert payload.specs.fuel == FuelType.ELECTRIC.value
    assert payload.specs.seating_capacity == 2
    assert payload.specs.doors == 3
    assert payload.specs.luggage_small == 1
    assert payload.specs.luggage_large == 2
    assert payload.specs.air_conditioning is False
    assert payload.policies.mileage_policy == MileagePolicy.LIMITED.value
    assert payload.policies.mileage_limit_km == 250
    assert payload.specs.sipp_code == "ECAR"


def test_build_search_vehicle_payload_maps_greenmotion_products_from_supplier_payload() -> None:
    payload = build_search_vehicle_payload(
        _make_vehicle(
            supplier_data={
                "booking_token": "opaque-booking-data",
                "products": [
                    {
                        "type": "BAS",
                        "total": 120.0,
                        "currency": "EUR",
                        "deposit": 300.0,
                        "excess": 900.0,
                        "fuelpolicy": "Same to same",
                        "mileage": 0,
                    },
                    {
                        "type": "PRE",
                        "total": 150.0,
                        "currency": "EUR",
                        "deposit": 250.0,
                        "excess": 500.0,
                        "fuelpolicy": "Full to full",
                        "mileage": 0,
                    },
                ],
            }
        )
    )

    assert len(payload.products) == 2
    assert payload.products[0]["type"] == "BAS"
    assert payload.products[0]["total"] == 120.0
    assert payload.products[0]["currency"] == "EUR"
    assert payload.products[0]["deposit"] == 300.0
    assert payload.products[0]["excess"] == 900.0
    assert payload.products[0]["fuel_policy"] == "Same to same"
    assert payload.products[1]["type"] == "PRE"
    assert payload.products[1]["total"] == 150.0


def test_build_search_vehicle_payload_exposes_canonical_product_and_rate_ids() -> None:
    payload = build_search_vehicle_payload(
        _make_vehicle(
            supplier_vehicle_id="SBC-CAR-1",
            supplier_data={
                "booking_token": "opaque-booking-data",
                "product_id": "SBC-PRODUCT-1",
                "rate_id": "BASIC-PRE",
                "availability_status": "Immediate",
            },
        )
    )

    assert payload.provider_vehicle_id == "SBC-CAR-1"
    assert payload.provider_product_id == "SBC-PRODUCT-1"
    assert payload.provider_rate_id == "BASIC-PRE"
    assert payload.availability_status == "Immediate"
