from datetime import date, time

from app.adapters.sicily_by_car import SicilyByCarAdapter
from app.schemas.booking import BookingExtra, CreateBookingRequest, DriverInfo
from app.schemas.common import BookingStatus
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest
from app.services.search_vehicle_payload_builder import build_search_vehicle_payload


def test_sicily_by_car_keeps_missing_specs_missing() -> None:
    adapter = SicilyByCarAdapter()
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
        provider="sicily_by_car",
        pickup_id="CTA",
        original_name="Catania Airport",
        latitude=37.4667,
        longitude=15.0664,
    )
    offer = {
        "currency": "EUR",
        "deposit": 900,
        "vehicle": {
            "id": "cat-1",
            "description": "Fiat Panda or similar",
            "sipp": "",
            "imageUrl": "https://example.com/panda.jpg",
            "transmissionType": None,
            "fuelType": None,
            "numberOfPassengers": None,
            "numberOfDoors": None,
            "luggageBig": None,
            "luggageSmall": None,
            "airConditioning": None,
        },
        "rate": {
            "id": "BASIC-POA",
            "description": "Basic",
            "distance": {},
        },
        "totalPrices": {"total": 180},
        "pickupLocation": {"id": "CTA", "name": "Catania Airport", "airportCode": "CTA"},
        "services": [],
    }

    vehicle = adapter._parse_offer(
        offer,
        request=request,
        rental_days=3,
        availability_id="av-1",
        request_id="req-1",
        pickup_entry=pickup,
    )

    assert vehicle is not None
    assert "fuel_type" not in vehicle.model_fields_set
    assert "seats" not in vehicle.model_fields_set
    assert "doors" not in vehicle.model_fields_set
    assert "bags_large" not in vehicle.model_fields_set
    assert "bags_small" not in vehicle.model_fields_set
    assert "air_conditioning" not in vehicle.model_fields_set
    assert "mileage_policy" not in vehicle.model_fields_set

    payload = build_search_vehicle_payload(vehicle)

    assert payload.specs.fuel is None
    assert payload.specs.seating_capacity is None
    assert payload.specs.doors is None
    assert payload.specs.luggage_large is None
    assert payload.specs.luggage_small is None
    assert payload.specs.air_conditioning is None
    assert payload.policies.mileage_policy is None


async def test_sicily_by_car_create_booking_sends_vehicle_id() -> None:
    class CapturingSicilyByCarAdapter(SicilyByCarAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls = []

        async def _post(self, endpoint: str, payload: dict) -> dict:
            self.calls.append((endpoint, payload))
            if endpoint == "reservations/create":
                return {"ok": True, "data": {"reservation": {"id": "SBC-RES-1"}}}
            if endpoint == "reservations/commit":
                return {"ok": True, "data": {"reservationId": "SBC-RES-1"}}
            raise AssertionError(f"Unexpected endpoint {endpoint}")

    adapter = CapturingSicilyByCarAdapter()
    request = SearchRequest(
        unified_location_id=1,
        pickup_date=date(2026, 6, 24),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 6, 26),
        dropoff_time=time(9, 0),
        currency="EUR",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="sicily_by_car",
        pickup_id="IT014",
        original_name="Milan Linate Airport",
        latitude=45.4451,
        longitude=9.2767,
    )
    vehicle = adapter._parse_offer(
        {
            "currency": "EUR",
            "deposit": 900,
            "vehicle": {
                "id": "B",
                "description": "Fiat Panda 1.2 or similar",
                "sipp": "MDMRS",
                "imageUrl": "https://example.com/panda.jpg",
            },
            "rate": {
                "id": "BASIC-POA",
                "description": "Basic",
                "payment": "PayOnArrival",
                "distance": {"unlimited": True},
            },
            "totalPrices": {"total": 180},
            "pickupLocation": {"id": "IT014", "name": "Milan Linate Airport", "airportCode": "LIN"},
            "services": [{"id": "GPS", "description": "GPS", "total": 20}],
        },
        request=request,
        rental_days=2,
        availability_id="availability-1",
        request_id="request-1",
        pickup_entry=pickup,
    )

    assert vehicle is not None
    response = await adapter.create_booking(
        CreateBookingRequest(
            vehicle_id=vehicle.id,
            search_id="search_sbc_1",
            driver=DriverInfo(
                first_name="Vrooem",
                last_name="Test",
                email="provider-smoke-test@example.com",
                phone="+32000000000",
                age=35,
                address="Test address",
                city="Milan",
                country="IT",
                postal_code="20100",
            ),
            extras=[BookingExtra(extra_id="ext_sicily_by_car_GPS", quantity=1)],
            pickup_date=date(2026, 6, 24),
            pickup_time="09:00",
            dropoff_date=date(2026, 6, 26),
            dropoff_time="09:00",
            laravel_booking_number="VROOEM-sbc_1",
        ),
        vehicle,
    )

    assert response.status == BookingStatus.CONFIRMED
    endpoint, create_payload = adapter.calls[0]
    assert endpoint == "reservations/create"
    assert create_payload["availabilityId"] == "availability-1"
    assert create_payload["vehicleId"] == "B"
    assert "vehicleCategoryId" not in create_payload
    assert create_payload["rateId"] == "BASIC-POA"
    assert create_payload["pickupLocationId"] == "IT014"
    assert create_payload["dropoffLocationId"] == "IT014"
    assert create_payload["pickupDateTime"] == "2026-06-24T09:00:00"
    assert create_payload["dropoffDateTime"] == "2026-06-26T09:00:00"
    assert "pickupDatetime" not in create_payload
    assert "dropoffDatetime" not in create_payload
    assert create_payload["driver"] == {
        "firstName": "Vrooem",
        "lastName": "Test",
        "age": 35,
        "email": "provider-smoke-test@example.com",
        "phone": "+32000000000",
    }
    assert "customer" not in create_payload
    assert create_payload["voucher"]["number"] == "VROOEM-sbc_1"
    assert create_payload["voucher"]["amount"] == 180
    assert create_payload["include"] == ["GPS"]
    assert "services" not in create_payload
