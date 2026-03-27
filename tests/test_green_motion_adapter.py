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


def test_greenmotion_parse_single_vehicle_sets_canonical_dropoff_location_for_one_way() -> None:
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
        dropoff_unified_location_id=2,
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="greenmotion",
        pickup_id="354",
        original_name="Casablanca Airport",
        latitude=33.37028,
        longitude=-7.583008,
        country_code="MA",
        iata="CMN",
    )
    dropoff = ProviderLocationEntry(
        provider="greenmotion",
        pickup_id="355",
        original_name="Casablanca Downtown",
        latitude=33.584459,
        longitude=-7.619644,
        country_code="MA",
    )

    vehicle = adapter._parse_single_vehicle(vehicle_xml, "quote-1", 3, [], request, pickup, dropoff)

    assert vehicle is not None
    assert vehicle.dropoff_location is not None
    assert vehicle.dropoff_location.supplier_location_id == "355"
    assert vehicle.dropoff_location.name == "Casablanca Downtown"
    assert vehicle.supplier_data["dropoff_location_id"] == "355"

    payload = build_search_vehicle_payload(vehicle)

    assert payload.location.dropoff is not None
    assert payload.location.dropoff.provider_location_id == "355"


import asyncio
import json


def test_greenmotion_get_locations_collects_service_area_and_info() -> None:
    adapter = GreenMotionAdapter()

    async def fake_request(method, url, **kwargs):
        content = kwargs.get("content", "")
        if "GetCountryList" in content:
            body = """<gm_webservice><response><country><countryID>1</countryID><countryName>Morocco</countryName><iso_alpha2>MA</iso_alpha2></country></response></gm_webservice>"""
        elif "GetServiceAreas" in content:
            body = """<gm_webservice><response><servicearea><locationID>354</locationID><name>Casablanca Airport</name></servicearea></response></gm_webservice>"""
        elif "GetLocationInfo" in content:
            body = """<gm_webservice><response><location_info><latitude>33.37028</latitude><longitude>-7.583008</longitude><iata>CMN</iata></location_info></response></gm_webservice>"""
        else:
            raise AssertionError(content)

        return type("Resp", (), {"text": body})()

    adapter._request = fake_request  # type: ignore[method-assign]
    locations = asyncio.run(adapter.get_locations())

    assert locations == [
        {
            "provider": "green_motion",
            "provider_location_id": "354",
            "name": "Casablanca Airport",
            "country": "Morocco",
            "country_code": "MA",
            "latitude": 33.37028,
            "longitude": -7.583008,
            "iata": "CMN",
        }
    ]
