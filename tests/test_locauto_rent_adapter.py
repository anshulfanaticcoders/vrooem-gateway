import xml.etree.ElementTree as ET
from datetime import date, time

from app.adapters.locauto_rent import LocautoRentAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_locauto_parse_vehicle_keeps_missing_specs_missing() -> None:
    adapter = LocautoRentAdapter()
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
        provider="locauto_rent",
        pickup_id="FCO",
        original_name="Rome Fiumicino Airport",
        latitude=41.8,
        longitude=12.25,
    )

    veh_avail = ET.fromstring(
        "<VehAvail>"
        "<VehAvailCore Status=\"Available\">"
        "<Vehicle AirConditionInd=\"\" PassengerQuantity=\"\" BaggageQuantity=\"\" TransmissionType=\"\">"
        "<VehMakeModel ModelYear=\"Ford Focus\"/>"
        "<PictureURL>https://example.com/focus.jpg</PictureURL>"
        "<VehType VehicleCategory=\"1\" DoorCount=\"\"/>"
        "<VehClass Size=\"2\"/>"
        "</Vehicle>"
        "<RentalRate RateDistanceUnlimited=\"false\">"
        "<VehicleCharges><VehicleCharge Amount=\"100.00\" CurrencyCode=\"EUR\" TaxInclusive=\"true\"/></VehicleCharges>"
        "</RentalRate>"
        "<TotalCharge RateTotalAmount=\"100.00\" EstimatedTotalAmount=\"100.00\" CurrencyCode=\"EUR\"/>"
        "</VehAvailCore>"
        "</VehAvail>"
    )

    vehicle = adapter._parse_single_vehicle(veh_avail, 3, request, pickup, None)

    assert vehicle is not None
    assert "transmission" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "bags_large" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "fuel_type" not in vehicle.model_fields_set
    assert "bags_small" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.transmission is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.luggage_large is None
    assert payload.specs.air_conditioning is None
    assert payload.specs.fuel is None
    assert payload.specs.luggage_small is None
    assert payload.policies.mileage_policy is None
