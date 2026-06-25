import unittest
from datetime import date, time

from app.adapters.surprice import SurpriceAdapter, SurpriceOneWayNotAllowedError
from app.schemas.booking import BookingExtra, CreateBookingRequest, DriverInfo
from app.schemas.common import ExtraType, FuelType
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import Extra, Vehicle, VehicleLocation
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def _parse_surprice_vehicle(description: str, sipp_code: str):
    adapter = SurpriceAdapter()
    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2026, 6, 10),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 6, 13),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="surprice",
        pickup_id="BCN",
        original_name="Barcelona Airport",
    )
    offering = {
        "vehicle": {
            "code": sipp_code,
            "description": description,
            "pictureURL": "https://example.com/surprice/car.jpg",
        },
        "rentalDetails": [
            {
                "rentalRate": {"rateQualifier": {"vendorRateID": "vr-1", "rateCode": "VROOEM"}},
                "totalCharge": {"estimatedTotalAmount": 300.00, "currencyCode": "EUR"},
            }
        ],
    }

    return adapter._parse_vehicle(
        offering=offering,
        rental_days=3,
        request=request,
        pickup_entry=pickup,
        dropoff_entry=None,
        pickup_station={"name": "Barcelona Airport", "address": {}},
        return_station={"name": "Barcelona Airport", "address": {}},
        pickup_code="BCN",
        pickup_ext_code="BCNA01",
        dropoff_code="BCN",
        dropoff_ext_code="BCNA01",
        fdw_offering=None,
    )


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


def test_surprice_does_not_guess_petrol_for_ambiguous_sipp_fuel_code() -> None:
    vehicle = _parse_surprice_vehicle("Generic City Car or similar", "MTES")

    assert vehicle is not None
    assert vehicle.fuel_type is None

    payload = build_search_vehicle_payload(vehicle)
    assert payload.specs.fuel is None
    assert payload.specs.sipp_code == "MTES"


def test_surprice_uses_clear_model_name_for_ev_and_phev_fuel() -> None:
    examples = [
        ("Fiat 500e", "MSES", FuelType.ELECTRIC),
        ("Nissan Leaf or similar", "CMES", FuelType.ELECTRIC),
        ("Peugeot 3008 Plug-in Hybrid or similar", "SMPS", FuelType.HYBRID),
    ]

    for model, sipp, expected_fuel in examples:
        vehicle = _parse_surprice_vehicle(model, sipp)
        assert vehicle is not None
        assert vehicle.fuel_type == expected_fuel

        payload = build_search_vehicle_payload(vehicle)
        assert payload.specs.fuel == expected_fuel.value
        assert payload.specs.sipp_code == sipp


def test_surprice_still_maps_deterministic_petrol_sipp_codes() -> None:
    vehicle = _parse_surprice_vehicle("Volkswagen Polo or similar", "EDMR")

    assert vehicle is not None
    assert vehicle.fuel_type == FuelType.PETROL

    payload = build_search_vehicle_payload(vehicle)
    assert payload.specs.fuel == FuelType.PETROL.value


def test_surprice_availability_extras_are_exposed_as_prebookable_addons() -> None:
    adapter = SurpriceAdapter()

    extras = adapter._parse_extras(
        [
            {
                "description": "NAV",
                "detailedDescription": "NAVIGATION SYSTEM",
                "amount": 27.60,
                "currencyCode": "EUR",
                "allowQuantity": 1,
                "purpose": 46,
                "calculationInfo": {"unitName": "Day", "unitCharge": 9.20},
            }
        ],
        "EUR",
    )

    assert len(extras) == 1
    assert extras[0].id == "ext_surprice_NAV"
    assert extras[0].name == "NAVIGATION SYSTEM"
    assert extras[0].daily_rate == 9.20
    assert extras[0].total_price == 27.60
    assert extras[0].currency == "EUR"
    assert extras[0].max_quantity == 1
    assert extras[0].supplier_data["code"] == "NAV"
    assert extras[0].supplier_data["allow_quantity"] is True
    assert extras[0].supplier_data["purpose"] == 46


def test_surprice_parse_vehicle_uses_dropoff_entry_when_supplier_repeats_pickup_station() -> None:
    adapter = SurpriceAdapter()
    request = SearchRequest(
        unified_location_id=1,
        dropoff_unified_location_id=2,
        pickup_date=date(2026, 6, 24),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 6, 28),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="surprice",
        pickup_id="DXB:DXBA01",
        original_name="Dubai Airport",
        latitude=25.2815459,
        longitude=55.3519485,
    )
    dropoff = ProviderLocationEntry(
        provider="surprice",
        pickup_id="60160:60160",
        original_name="Dubai Downtown",
        latitude=25.25406,
        longitude=55.30957,
    )
    station = {
        "name": "Dubai Airport",
        "stationType": "airport",
        "address": {
            "addressLine": ["34 24 St - Hor Al Anz East - Dubai - United Arab Emirates"],
            "city": "Dubai",
            "postalCode": "99070",
            "country": {"code": "AE"},
        },
    }
    offering = {
        "vehicle": {
            "description": "Hyundai Creta Automatic or similar",
            "pictureURL": "https://example.com/surprice/creta.jpg",
        },
        "rentalDetails": [
            {
                "rentalRate": {"rateQualifier": {"vendorRateID": "vr-1", "rateCode": "VROOEM"}},
                "totalCharge": {"estimatedTotalAmount": 113.43, "currencyCode": "EUR"},
            }
        ],
    }

    vehicle = adapter._parse_vehicle(
        offering=offering,
        rental_days=4,
        request=request,
        pickup_entry=pickup,
        dropoff_entry=dropoff,
        pickup_station=station,
        return_station=station,
        pickup_code="DXB",
        pickup_ext_code="DXBA01",
        dropoff_code="60160",
        dropoff_ext_code="60160",
        fdw_offering=None,
    )

    assert vehicle is not None
    assert vehicle.dropoff_location is not None
    assert vehicle.dropoff_location.supplier_location_id == "60160:60160"
    assert vehicle.dropoff_location.name == "Dubai Downtown"
    assert vehicle.dropoff_location.latitude == 25.25406
    assert vehicle.supplier_data["return_station_name"] == "Dubai Downtown"
    assert vehicle.supplier_data["dropoff_office"] is None


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


class SurpriceBookingPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_booking_sends_fdw_rate_and_selected_extras(self) -> None:
        adapter = SurpriceAdapter()
        captured_payload = {}

        class _Resp:
            status_code = 201

            def json(self):
                return {"orderInfo": {"corporateOrderId": "SURP-123"}}

        async def fake_request(*args, **kwargs):
            captured_payload.update(kwargs.get("json") or {})
            return _Resp()

        adapter._request = fake_request  # type: ignore[method-assign]

        vehicle = Vehicle(
            id="gw_surprice_1",
            supplier_id="surprice",
            supplier_vehicle_id="MBMR",
            provider_rate_id="bas-rate-1",
            name="Toyota Aygo or similar",
            pickup_location=VehicleLocation(name="Cagliari Airport"),
            pricing=Pricing(total_price=390.91, daily_rate=39.09, currency="EUR"),
            extras=[
                Extra(
                    id="ext_surprice_NAV",
                    name="NAVIGATION SYSTEM",
                    daily_rate=8,
                    total_price=80,
                    currency="EUR",
                    max_quantity=1,
                    type=ExtraType.EQUIPMENT,
                    supplier_data={"code": "NAV", "purpose": 46},
                ),
                Extra(
                    id="ext_surprice_ADS",
                    name="ADDITIONAL DRIVER",
                    daily_rate=6,
                    total_price=60,
                    currency="EUR",
                    max_quantity=1,
                    type=ExtraType.EQUIPMENT,
                    supplier_data={"code": "ADS", "purpose": 12},
                ),
            ],
            supplier_data={
                "vendor_rate_id": "bas-rate-1",
                "rate_code": "Vrooem",
                "fdw_vendor_rate_id": "fdw-rate-1",
                "fdw_rate_code": "Vrooem FDW",
                "pickup_code": "CAG",
                "pickup_ext_code": "CAGA01",
                "dropoff_code": "CAG",
                "dropoff_ext_code": "CAGA01",
            },
        )

        request = CreateBookingRequest(
            vehicle_id="gw_surprice_1",
            search_id="search_1",
            driver=DriverInfo(
                first_name="Test",
                last_name="Customer",
                email="test@example.com",
                phone="+390000000",
            ),
            insurance_id="ins_surprice_fdw",
            extras=[
                BookingExtra(extra_id="ext_surprice_NAV", quantity=1),
                BookingExtra(extra_id="ext_surprice_ADS", quantity=1),
            ],
            special_requests="Supplier-requested live test",
            pickup_date=date(2026, 8, 18),
            pickup_time="09:00",
            dropoff_date=date(2026, 8, 28),
            dropoff_time="09:00",
        )

        response = await adapter.create_booking(request, vehicle)

        self.assertEqual(response.supplier_booking_id, "SURP-123")
        self.assertEqual(captured_payload["vendorRateID"], "fdw-rate-1")
        self.assertEqual(captured_payload["rateCode"], "Vrooem FDW")
        self.assertNotIn("extras", captured_payload)
        self.assertEqual(
            captured_payload["specialEquipmentPreferences"],
            [{"equipType": 46, "quantity": 1}],
        )
        self.assertEqual(captured_payload["customerInfo"]["additionalUnknownDriversNum"], 1)
        self.assertEqual(captured_payload["notes"], "Supplier-requested live test")
        self.assertNotIn("specialRequests", captured_payload["customerInfo"]["customer"])


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
