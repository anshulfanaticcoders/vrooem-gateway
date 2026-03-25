import xml.etree.ElementTree as ET
from datetime import date, time

from app.adapters.ok_mobility import OkMobilityAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_okmobility_parse_single_vehicle_keeps_missing_specs_missing() -> None:
    adapter = OkMobilityAdapter()
    vehicle_xml = ET.fromstring(
        """
        <getMultiplePrice>
            <GroupID>GROUP-1</GroupID>
            <token>tok123</token>
            <VehicleModel>Peugeot 108 Automatic</VehicleModel>
            <previewValue>212.55</previewValue>
            <PrepayValue>300.00</PrepayValue>
            <rateCode>OK-BASE</rateCode>
            <stationNamePick>Palma de Mallorca Airport</stationNamePick>
            <stationNameDrop>Palma de Mallorca Airport</stationNameDrop>
        </getMultiplePrice>
        """
    )

    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2026, 5, 21),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 5, 24),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(provider="okmobility", pickup_id="PMI", original_name="Palma de Mallorca Airport")

    vehicle = adapter._parse_single_vehicle(vehicle_xml, 3, request, pickup, None)

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
    assert payload.specs.luggage_small is None
    assert payload.specs.luggage_large is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None
