import unittest
from datetime import date, time

from app.adapters.surprice import SurpriceAdapter, SurpriceOneWayNotAllowedError
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


class SurpriceAdapterFetchAvailabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_availability_raises_for_known_one_way_restriction(self) -> None:
        adapter = SurpriceAdapter()

        class _Resp:
            status_code = 422
            text = '{"type":3,"code":225,"message":"One way rentals not allowed to this location"}'

            def json(self):
                return {
                    "type": 3,
                    "code": 225,
                    "message": "One way rentals not allowed to this location",
                }

        async def fake_request(*args, **kwargs):
            return _Resp()

        adapter._request = fake_request  # type: ignore[method-assign]

        with self.assertRaises(SurpriceOneWayNotAllowedError):
            await adapter._fetch_availability(
                adapter._base_url(),
                {
                    "pickUpLocationCode": "CMN",
                    "pickUpExtendedLocationCode": "CMNA01",
                    "returnLocationCode": "CMNC01",
                    "returnExtendedLocationCode": "CMNC01",
                    "rateCode": "Vrooem",
                },
            )


class SurpriceOneWayPayloadTest(unittest.IsolatedAsyncioTestCase):
    """Locks in that a different dropoff is actually sent to the provider API.

    Prevents silent regressions where the adapter might accidentally send pickup == dropoff
    while the user asked for one-way — which would return round-trip inventory and mislead the user.
    """

    def _make_request(self) -> SearchRequest:
        return SearchRequest(
            unified_location_id=1,
            dropoff_unified_location_id=2,
            pickup_date=date(2026, 5, 21),
            pickup_time=time(9, 0),
            dropoff_date=date(2026, 5, 24),
            dropoff_time=time(9, 0),
            currency="EUR",
            driver_age=35,
        )

    async def _run_and_capture(self, pickup_entry, dropoff_entry):
        adapter = SurpriceAdapter()
        captured_payloads: list[dict] = []

        async def fake_fetch(base_url: str, payload: dict):
            captured_payloads.append(payload)
            return {"productOfferings": []}

        adapter._fetch_availability = fake_fetch  # type: ignore[method-assign]

        await adapter.search_vehicles(self._make_request(), pickup_entry, dropoff_entry)
        return captured_payloads

    async def test_different_dropoff_sends_different_return_location_code(self) -> None:
        pickup = ProviderLocationEntry(
            provider="surprice",
            pickup_id="DXB",
            original_name="Dubai Airport",
            extended_location_code="DXBA01",
        )
        dropoff = ProviderLocationEntry(
            provider="surprice",
            pickup_id="AUH",
            original_name="Abu Dhabi Airport",
            extended_location_code="AUHA01",
            extended_dropoff_code="AUHA01",
        )

        payloads = await self._run_and_capture(pickup, dropoff)

        self.assertGreater(len(payloads), 0)
        for payload in payloads:
            self.assertEqual(payload["pickUpLocationCode"], "DXB")
            self.assertEqual(payload["returnLocationCode"], "AUH")
            self.assertNotEqual(payload["pickUpLocationCode"], payload["returnLocationCode"])
            self.assertEqual(payload["pickUpExtendedLocationCode"], "DXBA01")
            self.assertEqual(payload["returnExtendedLocationCode"], "AUHA01")

    async def test_same_pickup_and_dropoff_sends_matching_codes(self) -> None:
        pickup = ProviderLocationEntry(
            provider="surprice",
            pickup_id="DXB",
            original_name="Dubai Airport",
            extended_location_code="DXBA01",
        )
        dropoff = ProviderLocationEntry(
            provider="surprice",
            pickup_id="DXB",
            original_name="Dubai Airport",
            extended_location_code="DXBA01",
        )

        payloads = await self._run_and_capture(pickup, dropoff)

        self.assertGreater(len(payloads), 0)
        for payload in payloads:
            self.assertEqual(payload["pickUpLocationCode"], payload["returnLocationCode"])

    async def test_missing_dropoff_entry_defaults_to_pickup(self) -> None:
        # Defensive: if the gateway forgets to resolve the dropoff entry,
        # the adapter must NOT invent an arbitrary one — it falls back to pickup
        # so the provider performs a round-trip quote, and Laravel should not have
        # called this code path at all on a one-way request.
        pickup = ProviderLocationEntry(
            provider="surprice",
            pickup_id="DXB",
            original_name="Dubai Airport",
            extended_location_code="DXBA01",
        )

        payloads = await self._run_and_capture(pickup, None)

        self.assertGreater(len(payloads), 0)
        for payload in payloads:
            self.assertEqual(payload["pickUpLocationCode"], payload["returnLocationCode"])
            self.assertEqual(payload["pickUpLocationCode"], "DXB")
