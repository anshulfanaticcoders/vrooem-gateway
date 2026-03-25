from datetime import date, time

from app.adapters.favrica import FavricaAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_favrica_keeps_missing_specs_missing() -> None:
    adapter = FavricaAdapter()
    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2026, 5, 21),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 5, 24),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="favrica",
        pickup_id="AYT",
        original_name="Antalya Havaliman",
        latitude=36.8987,
        longitude=30.8005,
    )
    raw = {
        "rez_id": "rz-1",
        "brand": "renault",
        "type": "clio",
        "total_rental": "180,00",
        "daily_rental": "60,00",
        "currency": "EURO",
        "sipp": "",
        "transmission": "",
        "fuel": "",
        "image_path": "car.png",
        "km_limit": "",
        "provision": "",
        "chairs": "",
        "big_bags": "",
        "small_bags": "",
        "Services": [],
    }

    vehicle = adapter._parse_vehicle(raw, request, 3, pickup)

    assert vehicle is not None
    assert "transmission" not in vehicle.model_fields_set
    assert "fuel_type" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "bags_large" not in vehicle.model_fields_set
    assert "bags_small" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.transmission is None
    assert payload.specs.fuel is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.luggage_large is None
    assert payload.specs.luggage_small is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None
