from datetime import date, time

from app.adapters.adobe_car import AdobeCarAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_adobe_keeps_missing_specs_missing() -> None:
    adapter = AdobeCarAdapter()
    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2026, 5, 21),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 5, 24),
        dropoff_time=time(9, 0),
        currency="USD",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="adobe_car",
        pickup_id="SJO",
        original_name="San Jose Airport",
        latitude=9.9939,
        longitude=-84.2088,
    )
    raw = {
        "category": "b",
        "model": "Kia Picanto AT or similar",
        "tdr": 34.35,
        "pli": 5,
        "ldw": 6,
        "spp": 0,
        "dro": 0,
        "passengers": None,
        "doors": None,
        "manual": None,
        "photo": "https://example.com/car.jpg",
        "traction": "",
        "type": "sedan",
        "order": None,
    }

    vehicle = adapter._parse_vehicle(raw, request, 3, pickup, None, {})

    assert vehicle is not None
    assert "transmission" not in vehicle.model_fields_set
    assert "fuel_type" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.transmission is None
    assert payload.specs.fuel is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None
