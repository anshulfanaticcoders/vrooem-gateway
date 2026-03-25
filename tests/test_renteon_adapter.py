from datetime import date, time

from app.adapters.renteon import RenteonAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_renteon_keeps_missing_specs_missing() -> None:
    adapter = RenteonAdapter()
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
        provider="renteon",
        pickup_id="MA-CAS-CMN",
        original_name="Casablanca Airport",
        latitude=33.3675,
        longitude=-7.58997,
    )
    raw = {
        "ModelName": "Fiat 500",
        "CarCategory": "",
        "Amount": 180,
        "Currency": "EUR",
        "PickupOffice": {"Name": "Casablanca Airport", "Town": "Casablanca"},
        "DropOffOffice": {},
        "PickupOfficeId": 101,
        "DropOffOfficeId": 101,
        "ConnectorId": 51,
        "PassengerCapacity": None,
        "NumberOfDoors": None,
        "BigBagsCapacity": None,
        "SmallBagsCapacity": None,
        "CarModelImageURL": "https://example.com/fiat.png",
        "AvailableServices": [],
    }

    vehicle = adapter._parse_vehicle(raw, request, 3, pickup, provider_code="Alquicoche")

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
