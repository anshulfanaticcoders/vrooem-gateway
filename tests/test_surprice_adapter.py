from datetime import date, time

from app.adapters.surprice import SurpriceAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_surprice_parse_vehicle_keeps_missing_specs_missing() -> None:
    adapter = SurpriceAdapter()
    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2026, 5, 21),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 5, 24),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(provider="surprice", pickup_id="CMN", original_name="Casablanca Airport")

    offering = {
        "vehicle": {
            "description": "Kia Picanto Automatic or similar",
            "pictureURL": "https://example.com/surprice/picanto.jpg",
        },
        "rentalDetails": [
            {
                "rentalRate": {
                    "rateQualifier": {
                        "vendorRateID": "vr-1",
                        "rateCode": "VROOEM",
                    },
                },
                "totalCharge": {
                    "estimatedTotalAmount": 51.38,
                    "currencyCode": "USD",
                },
            }
        ],
    }

    vehicle = adapter._parse_vehicle(
        offering=offering,
        rental_days=3,
        request=request,
        pickup_entry=pickup,
        dropoff_entry=None,
        pickup_station={"name": "Casablanca Airport", "address": {}},
        return_station={"name": "Casablanca Airport", "address": {}},
        pickup_code="CMN",
        pickup_ext_code="CMNA01",
        dropoff_code="CMN",
        dropoff_ext_code="CMNA01",
        fdw_offering=None,
    )

    assert vehicle is not None
    assert "transmission" not in vehicle.model_fields_set
    assert "fuel_type" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "bags_large" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.transmission is None
    assert payload.specs.fuel is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.luggage_large is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None
