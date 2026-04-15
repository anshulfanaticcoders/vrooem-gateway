from datetime import date, time
from types import SimpleNamespace

from app.adapters.renteon import RenteonAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload
import app.adapters.renteon as renteon_module


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


async def test_renteon_blank_allowed_providers_fans_out_to_default_provider_set(monkeypatch) -> None:
    adapter = RenteonAdapter()
    request = SearchRequest(
        unified_location_id=3385755165,
        pickup_date=date(2026, 5, 10),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 5, 13),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="renteon",
        pickup_id="AE-DUB-DXB",
        original_name="Dubai Airport",
        country_code="AE",
        iata="DXB",
    )

    monkeypatch.setattr(
        renteon_module,
        "get_settings",
        lambda: SimpleNamespace(
            renteon_api_url="https://aggregator.renteon.com",
            renteon_username="demo",
            renteon_password="demo",
            renteon_allowed_providers="",
            renteon_pricelist_codes="",
        ),
    )

    requested_provider_codes = []

    class DummyResponse:
        def json(self):
            return []

    async def fake_request(method, url, json=None, headers=None):
        requested_provider_codes.append(json["Providers"][0]["Code"])
        return DummyResponse()

    monkeypatch.setattr(adapter, "_request", fake_request)

    vehicles = await adapter.search_vehicles(request, pickup, None)

    assert vehicles == []
    assert requested_provider_codes == [
        "LetsDrive",
        "CapitalCarRental",
        "LuxGoo",
        "Alquicoche",
    ]
