import asyncio
import xml.etree.ElementTree as ET
from datetime import date, time

from app.adapters.locauto_rent import LocautoRentAdapter, _EQUIP_TYPE_NAMES
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


def test_locauto_get_locations_returns_enriched_location_metadata() -> None:
    adapter = LocautoRentAdapter()

    locations = asyncio.run(adapter.get_locations())

    assert len(locations) == 107

    fco = next(location for location in locations if location["provider_location_id"] == "FCO")

    assert fco["name"] == "Roma Fiumicino Airport"
    assert fco["city"] == "Fiumicino"
    assert fco["country_code"] == "IT"
    assert fco["location_type"] == "airport"
    assert fco["iata"] == "FCO"
    assert fco["is_airport"] is True
    assert fco["address"] == "AEROPORTO L.DA VINCI - Via dell'aeroporto di Fiumicino 320, 00054 Fiumicino (RM)"
    assert fco["phone"] == "+39 06 65953615"
    assert fco["email"] == "fiumicinoapt@locautorent.it"
    assert fco["operating_hours"] == {
        "weekday": "07:00 - 24:00",
        "saturday": "07:00 - 24:00",
        "sunday": "07:00 - 24:00",
    }
    assert fco["pickup_instructions"].startswith("The office is located inside the Epua 2 Tower")
    assert fco["dropoff_instructions"].startswith("Follow the 'Car Rental'")
    assert fco["out_of_hours"]["key_box"].startswith("Key box available")
    assert fco["out_of_hours"]["pickup"] == "ON REQUEST"


def test_locauto_parse_vehicle_enriches_pickup_and_dropoff_locations() -> None:
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
        original_name="Roma Fiumicino Airport",
        latitude=41.8,
        longitude=12.25,
    )
    dropoff = ProviderLocationEntry(
        provider="locauto_rent",
        pickup_id="AHO",
        original_name="Alghero Airport",
        latitude=40.63,
        longitude=8.29,
    )

    veh_avail = ET.fromstring(
        "<VehAvail>"
        "<VehAvailCore Status=\"Available\">"
        "<Vehicle AirConditionInd=\"true\" PassengerQuantity=\"5\" BaggageQuantity=\"2\" TransmissionType=\"Manual\">"
        "<VehMakeModel ModelYear=\"Ford Focus\"/>"
        "<PictureURL>https://example.com/focus.jpg</PictureURL>"
        "<VehType VehicleCategory=\"1\" DoorCount=\"4\"/>"
        "<VehClass Size=\"2\"/>"
        "</Vehicle>"
        "<RentalRate RateDistanceUnlimited=\"false\">"
        "<VehicleCharges><VehicleCharge Amount=\"150.00\" CurrencyCode=\"EUR\" TaxInclusive=\"true\"/></VehicleCharges>"
        "</RentalRate>"
        "<TotalCharge RateTotalAmount=\"150.00\" EstimatedTotalAmount=\"150.00\" CurrencyCode=\"EUR\"/>"
        "</VehAvailCore>"
        "</VehAvail>"
    )

    vehicle = adapter._parse_single_vehicle(veh_avail, 3, request, pickup, dropoff)

    assert vehicle is not None
    assert vehicle.pickup_location.address == "AEROPORTO L.DA VINCI - Via dell'aeroporto di Fiumicino 320, 00054 Fiumicino (RM)"
    assert vehicle.pickup_location.phone == "+39 06 65953615"
    assert vehicle.pickup_location.email == "fiumicinoapt@locautorent.it"
    assert vehicle.pickup_location.is_airport is True
    assert vehicle.pickup_location.airport_code == "FCO"
    assert vehicle.pickup_location.operating_hours == {
        "weekday": "07:00 - 24:00",
        "saturday": "07:00 - 24:00",
        "sunday": "07:00 - 24:00",
    }
    assert vehicle.pickup_location.pickup_instructions.startswith("The office is located inside the Epua 2 Tower")
    assert vehicle.dropoff_location is not None
    assert vehicle.dropoff_location.phone == "+39 079 999241"
    assert vehicle.dropoff_location.email == "algheroapt@locautorent.it"
    assert vehicle.dropoff_location.is_airport is True
    assert vehicle.dropoff_location.airport_code == "AHO"
    assert vehicle.dropoff_location.dropoff_instructions.startswith("The parking is located just outside the terminal")


def test_locauto_equipment_mapping_includes_vendor_document_codes() -> None:
    assert _EQUIP_TYPE_NAMES["136"] == "Don't Worry Protection"
    assert _EQUIP_TYPE_NAMES["140"] == "Glass & Wheels Protection"
    assert _EQUIP_TYPE_NAMES["147"] == "Smart Cover"
