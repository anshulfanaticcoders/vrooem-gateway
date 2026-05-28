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
    assert _EQUIP_TYPE_NAMES["77"] == "No Stress Return"
    assert _EQUIP_TYPE_NAMES["89"] == "Pet Transport (Bau the Way)"
    assert _EQUIP_TYPE_NAMES["138"] == "Pool Driving (3+ Drivers)"
    assert _EQUIP_TYPE_NAMES["166"] == "Tollpass Device"


def test_locauto_priced_equips_skip_one_way_fee_and_respect_charge_rules() -> None:
    adapter = LocautoRentAdapter()
    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2027, 1, 1),
        pickup_time=time(9, 0),
        dropoff_date=date(2027, 1, 8),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="locauto_rent",
        pickup_id="MXP",
        original_name="Milano Malpensa Airport",
        latitude=45.62762,
        longitude=8.73,
    )
    dropoff = ProviderLocationEntry(
        provider="locauto_rent",
        pickup_id="BDS",
        original_name="Brindisi Airport",
        latitude=40.710612,
        longitude=17.929884,
    )

    veh_avail = ET.fromstring(
        "<VehAvail>"
        "<VehAvailCore Status=\"Available\">"
        "<Vehicle AirConditionInd=\"true\" PassengerQuantity=\"5\" BaggageQuantity=\"2\" TransmissionType=\"Manual\" Code=\"EDMR\">"
        "<VehMakeModel ModelYear=\"Fiat Panda\"/>"
        "<PictureURL>https://example.com/panda.jpg</PictureURL>"
        "<VehType DoorCount=\"5\"/>"
        "<VehClass Size=\"1\"/>"
        "</Vehicle>"
        "<RentalRate><VehicleCharges><VehicleCharge Amount=\"120.00\" CurrencyCode=\"EUR\" TaxInclusive=\"true\"/></VehicleCharges></RentalRate>"
        "<TotalCharge RateTotalAmount=\"120.00\" EstimatedTotalAmount=\"120.00\" CurrencyCode=\"EUR\"/>"
        "<PricedEquips>"
        "<PricedEquip>"
        "<Equipment EquipType=\"35\"><Description>Sardinia one way fee</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"719.80\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Rent\" Quantity=\"1\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"19\"><Description>Navigatore satellitare / GPS</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"10.00\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<MinMax MaxChargeDays=\"3\"/>"
        "<Calculation UnitName=\"Day\" Quantity=\"7\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"55\"><Description>Catene da neve / Snow chains</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"0.00\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Day\" Quantity=\"7\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"7\"><Description>Infant child seat</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"25.00\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Rent\" Quantity=\"1\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"89\"><Description>Bau the way</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"12.47\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Day\" Quantity=\"7\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"166\"><Description>Tollpass device</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"25.19\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Day\" Quantity=\"7\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"77\"><Description>No stress return</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"7.20\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Day\" Quantity=\"7\"/>"
        "</Charge>"
        "</PricedEquip>"
        "<PricedEquip>"
        "<Equipment EquipType=\"138\"><Description>Car pooling from 3 to more drivers</Description></Equipment>"
        "<Charge CurrencyCode=\"EUR\" Amount=\"12.96\" TaxInclusive=\"true\" IncludedInRate=\"false\">"
        "<Calculation UnitName=\"Day\" Quantity=\"7\"/>"
        "</Charge>"
        "</PricedEquip>"
        "</PricedEquips>"
        "</VehAvailCore>"
        "</VehAvail>"
    )

    vehicle = adapter._parse_single_vehicle(veh_avail, 7, request, pickup, dropoff)

    assert vehicle is not None
    assert vehicle.pricing.deposit_amount == 0.0
    assert vehicle.pricing.deposit_currency == "EUR"
    assert vehicle.supplier_data["deposit_amount"] == 0.0
    assert vehicle.supplier_data["deposit_policy"]["display_text"] == "No car deposit required"

    codes = {extra.supplier_data["code"] for extra in vehicle.extras}
    assert "35" not in codes
    assert "23" not in codes
    assert "55" not in codes

    gps = next(extra for extra in vehicle.extras if extra.supplier_data["code"] == "19")
    assert gps.daily_rate == 10.0
    assert gps.total_price == 30.0
    assert gps.supplier_data["pricing_type"] == "per_day"
    assert gps.supplier_data["chargeable_days"] == 3

    infant_seat = next(extra for extra in vehicle.extras if extra.supplier_data["code"] == "7")
    assert infant_seat.daily_rate == 0.0
    assert infant_seat.total_price == 25.0
    assert infant_seat.supplier_data["pricing_type"] == "per_rental"

    pet_transport = next(extra for extra in vehicle.extras if extra.supplier_data["code"] == "89")
    assert pet_transport.daily_rate == 12.47
    assert pet_transport.total_price == 87.29

    tollpass = next(extra for extra in vehicle.extras if extra.supplier_data["code"] == "166")
    assert tollpass.daily_rate == 25.19
    assert tollpass.total_price == 176.33

    no_stress_return = next(extra for extra in vehicle.extras if extra.supplier_data["code"] == "77")
    assert no_stress_return.daily_rate == 7.2
    assert no_stress_return.total_price == 50.4

    car_pooling = next(extra for extra in vehicle.extras if extra.supplier_data["code"] == "138")
    assert car_pooling.daily_rate == 12.96
    assert car_pooling.total_price == 90.72

    payload = build_search_vehicle_payload(vehicle)
    assert payload.pricing.deposit_amount == 0.0
    assert payload.pricing.deposit_currency == "EUR"
    payload_codes = {extra["code"] for extra in payload.extras_preview}
    assert "35" not in payload_codes
    assert "55" not in payload_codes
    assert payload.extras_preview[0]["pricing_type"] in {"per_day", "per_rental"}
