import xml.etree.ElementTree as ET
from datetime import date, time

from app.adapters.green_motion import GreenMotionAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_greenmotion_parse_single_vehicle_keeps_missing_specs_missing() -> None:
    adapter = GreenMotionAdapter()
    vehicle_xml = ET.fromstring(
        """
        <vehicle id="veh-1" name="Volkswagen Up or similar">
            <acriss>MBMR</acriss>
            <product type="BAS">
                <total currency="EUR">120.00</total>
                <deposit>300.00</deposit>
                <excess>900.00</excess>
                <fuelpolicy>Same to same</fuelpolicy>
            </product>
        </vehicle>
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
    pickup = ProviderLocationEntry(provider="greenmotion", pickup_id="359", original_name="Marrakech Airport")

    vehicle = adapter._parse_single_vehicle(vehicle_xml, "quote-1", 3, [], request, pickup, None)

    assert vehicle is not None
    assert "transmission" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.transmission is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None
