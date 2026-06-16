from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.booking import BookingResponse, CreateBookingRequest, DriverInfo
from app.schemas.common import BookingStatus
from app.services import booking_service


class FakeRedis:
    def __init__(self) -> None:
        self.set_calls = []
        self.deleted_keys = []

    async def set(self, key, value, ex=None, nx=False):
        self.set_calls.append((key, value, ex, nx))
        return True

    async def delete(self, key):
        self.deleted_keys.append(key)


class FakeCache:
    def __init__(self, vehicle_data: dict) -> None:
        self.redis = FakeRedis()
        self.vehicle_data = vehicle_data

    async def get_vehicle(self, vehicle_id: str):
        return self.vehicle_data if vehicle_id == self.vehicle_data["id"] else None


class FakeAdapter:
    def __init__(self) -> None:
        self.calls = []

    async def create_booking(self, request, vehicle):
        self.calls.append((request, vehicle))
        return BookingResponse(
            id="bk_test_123",
            supplier_id="locauto_rent",
            supplier_booking_id="LC123",
            status=BookingStatus.CONFIRMED,
            vehicle_name=vehicle.name,
            pickup_datetime=datetime(2027, 5, 13, 9, 0),
            dropoff_datetime=datetime(2027, 5, 21, 9, 0),
            pickup_location="Milan Airport (MXP)",
            dropoff_location="Brindisi Airport (BDS)",
            total_price=1067,
            currency="EUR",
        )


class FailingAdapter:
    async def create_booking(self, request, vehicle):
        raise RuntimeError("Supplier rejected reservation: rate no longer available")


async def test_create_booking_uses_cached_vehicle_and_laravel_lock(monkeypatch):
    vehicle_data = {
        "id": "gw_vehicle_1",
        "supplier_id": "locauto_rent",
        "supplier_vehicle_id": "MDMR",
        "name": "Fiat Panda",
        "pricing": {"currency": "EUR", "total_price": 1067, "daily_rate": 133.38},
    }
    adapter = FakeAdapter()
    cache = FakeCache(vehicle_data)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="anshulmankotia1997@gmail.com",
            phone="8278825392",
            age=35,
        ),
        pickup_date="2027-05-13",
        pickup_time="09:00",
        dropoff_date="2027-05-21",
        dropoff_time="09:00",
        laravel_booking_id=104,
        laravel_booking_number="BK2026052581",
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    response = await booking_service.create_booking(request, cache)

    assert response.supplier_booking_id == "LC123"
    assert len(adapter.calls) == 1
    assert cache.redis.set_calls == [("gateway_booking_lock:104", "1", 90, True)]
    assert cache.redis.deleted_keys == ["gateway_booking_lock:104"]


async def test_create_booking_returns_structured_provider_failure(monkeypatch):
    vehicle_data = {
        "id": "gw_vehicle_1",
        "supplier_id": "surprice",
        "supplier_vehicle_id": "EDMR",
        "name": "Fiat 500 or similar",
        "pricing": {"currency": "EUR", "total_price": 202.88, "daily_rate": 28.98},
    }
    cache = FakeCache(vehicle_data)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="anshulmankotia1997@gmail.com",
            phone="8278825392",
            age=35,
        ),
        pickup_date="2027-05-13",
        pickup_time="09:00",
        dropoff_date="2027-05-21",
        dropoff_time="09:00",
        laravel_booking_id=104,
        laravel_booking_number="BK2026052581",
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: FailingAdapter())

    response = await booking_service.create_booking(request, cache)

    assert response.status == BookingStatus.FAILED
    assert response.supplier_booking_id == ""
    assert response.provider_status == "failed"
    assert response.failure_reason == "Supplier rejected reservation: rate no longer available"
    assert (
        response.supplier_data["error"] == "Supplier rejected reservation: rate no longer available"
    )
    assert response.supplier_data["exception_type"] == "RuntimeError"
    assert cache.redis.deleted_keys == ["gateway_booking_lock:104"]


def test_create_booking_request_rejects_blank_gateway_context():
    with pytest.raises(ValidationError):
        CreateBookingRequest(
            vehicle_id=" ",
            search_id="search_123",
            driver=DriverInfo(
                first_name="Vrooem",
                last_name="Testing",
                email="anshulmankotia1997@gmail.com",
                phone="8278825392",
                age=35,
            ),
        )

    with pytest.raises(ValidationError):
        CreateBookingRequest(
            vehicle_id="gw_vehicle_1",
            search_id="",
            driver=DriverInfo(
                first_name="Vrooem",
                last_name="Testing",
                email="anshulmankotia1997@gmail.com",
                phone="8278825392",
                age=35,
            ),
        )


def test_booking_response_normalizer_fails_confirmed_without_supplier_ref():
    response = BookingResponse(
        id="bk_test_123",
        supplier_id="locauto_rent",
        supplier_booking_id="",
        status=BookingStatus.CONFIRMED,
        vehicle_name="Fiat Panda",
        total_price=1067,
        currency="EUR",
    )

    normalized = booking_service.normalize_booking_response(response)

    assert normalized.status == BookingStatus.FAILED
    assert normalized.failure_reason == "Confirmed status without supplier booking id"
