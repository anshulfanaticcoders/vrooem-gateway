from datetime import date, time

from app.adapters.sicily_by_car import SicilyByCarAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_sicily_by_car_keeps_missing_specs_missing() -> None:
    adapter = SicilyByCarAdapter()
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
        provider="sicily_by_car",
        pickup_id="CTA",
        original_name="Catania Airport",
        latitude=37.4667,
        longitude=15.0664,
    )
    offer = {
        "currency": "EUR",
        "deposit": 900,
        "vehicle": {
            "id": "cat-1",
            "description": "Fiat Panda or similar",
            "sipp": "MDMRS",
            "imageUrl": "https://example.com/panda.jpg",
            "transmissionType": None,
            "fuelType": None,
            "numberOfPassengers": None,
            "numberOfDoors": None,
            "luggageBig": None,
            "luggageSmall": None,
            "airConditioning": None,
        },
        "rate": {
            "id": "BASIC-POA",
            "description": "Basic",
            "distance": {},
        },
        "totalPrices": {"total": 180},
        "pickupLocation": {"id": "CTA", "name": "Catania Airport", "airportCode": "CTA"},
        "services": [],
    }

    vehicle = adapter._parse_offer(
        offer,
        request=request,
        rental_days=3,
        availability_id="av-1",
        request_id="req-1",
        pickup_entry=pickup,
    )

    assert vehicle is not None
    assert "fuel_type" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "bags_large" not in vehicle.model_fields_set
    assert "bags_small" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.fuel is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.luggage_large is None
    assert payload.specs.luggage_small is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None
