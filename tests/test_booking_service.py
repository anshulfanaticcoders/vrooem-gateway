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
    def __init__(self, vehicle_data: dict | None) -> None:
        self.redis = FakeRedis()
        self.vehicle_data = vehicle_data
        self.restored_vehicle = None

    async def get_vehicle(self, vehicle_id: str):
        if not self.vehicle_data:
            return None

        return self.vehicle_data if vehicle_id == self.vehicle_data["id"] else None

    async def set_vehicle(self, vehicle_id: str, data: dict) -> None:
        self.restored_vehicle = (vehicle_id, data)


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
        "search_id": "search_123",
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
            email="customer@example.com",
            phone="1000000000",
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


async def test_create_booking_applies_selected_supplier_package_before_adapter(monkeypatch):
    vehicle_data = {
        "id": "gw_vehicle_1",
        "search_id": "search_123",
        "supplier_id": "green_motion",
        "supplier_vehicle_id": "85512",
        "name": "Mitsubishi Attrage, Automatic or similar",
        "category": "economy",
        "pricing": {"currency": "EUR", "total_price": 57.88, "daily_rate": 19.29},
        "supplier_data": {
            "quote_id": "10939053957",
            "vehicle_id": "85512",
            "location_id": "59610",
            "products": [
                {
                    "type": "BAS",
                    "total": 57.88,
                    "price_per_day": 19.29,
                    "currency": "EUR",
                    "deposit": 473.37,
                    "fuelpolicy": "SL",
                    "minage": 21,
                },
                {
                    "type": "PMP",
                    "total": 109.96,
                    "price_per_day": 36.65,
                    "currency": "EUR",
                    "deposit": 473.37,
                    "fuelpolicy": "FF",
                    "minage": 21,
                },
            ],
        },
    }
    adapter = FakeAdapter()
    cache = FakeCache(vehicle_data)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        package="PMP",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="customer@example.com",
            phone="1000000000",
            age=35,
        ),
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    response = await booking_service.create_booking(request, cache)

    assert response.supplier_booking_id == "LC123"
    assert len(adapter.calls) == 1
    vehicle = adapter.calls[0][1]
    assert vehicle.pricing.total_price == 109.96
    assert vehicle.pricing.daily_rate == 36.65
    assert vehicle.pricing.deposit_amount == 473.37
    assert vehicle.supplier_data["fuel_policy"] == "FF"
    assert vehicle.supplier_data["selected_package"] == "PMP"
    assert vehicle.supplier_data["selected_product"]["type"] == "PMP"


async def test_create_booking_applies_selected_recordgo_product_ids_before_adapter(monkeypatch):
    vehicle_data = {
        "id": "gw_vehicle_1",
        "search_id": "search_123",
        "supplier_id": "recordgo",
        "supplier_vehicle_id": "EDMR",
        "name": "Fiat 500 or similar",
        "category": "mini",
        "pricing": {"currency": "EUR", "total_price": 100.0, "daily_rate": 25.0},
        "supplier_data": {
            "product_id": 10,
            "product_ver": 1,
            "rate_prod_ver": "A",
            "booking_total": 100.0,
            "automatic_complements": [{"complementId": 1}],
            "products": [
                {
                    "type": "RG_10_A",
                    "name": "Basic",
                    "total": 100.0,
                    "price_per_day": 25.0,
                    "currency": "EUR",
                    "product_id": 10,
                    "product_ver": 1,
                    "rate_prod_ver": "A",
                    "complements_autom": [{"complementId": 1}],
                },
                {
                    "type": "RG_20_B",
                    "name": "Premium",
                    "total": 160.0,
                    "price_per_day": 40.0,
                    "currency": "EUR",
                    "product_id": 20,
                    "product_ver": 2,
                    "rate_prod_ver": "B",
                    "complements_autom": [{"complementId": 2}],
                    "complements_included": [{"complementId": 3}],
                },
            ],
        },
    }
    adapter = FakeAdapter()
    cache = FakeCache(vehicle_data)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        package="RG_20_B",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="customer@example.com",
            phone="1000000000",
            age=35,
        ),
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    response = await booking_service.create_booking(request, cache)

    assert response.supplier_booking_id == "LC123"
    vehicle = adapter.calls[0][1]
    assert vehicle.pricing.total_price == 160.0
    assert vehicle.pricing.daily_rate == 40.0
    assert vehicle.supplier_data["product_id"] == 20
    assert vehicle.supplier_data["product_ver"] == 2
    assert vehicle.supplier_data["rate_prod_ver"] == "B"
    assert vehicle.supplier_data["booking_total"] == 160.0
    assert vehicle.supplier_data["automatic_complements"] == [{"complementId": 2}]
    assert vehicle.supplier_data["included_complements"] == [{"complementId": 3}]
    assert vehicle.supplier_data["product_data"]["type"] == "RG_20_B"


async def test_create_booking_returns_structured_provider_failure(monkeypatch):
    vehicle_data = {
        "id": "gw_vehicle_1",
        "search_id": "search_123",
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


async def test_create_booking_uses_request_vehicle_context_when_cache_missing(monkeypatch):
    adapter = FakeAdapter()
    cache = FakeCache(None)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        vehicle_context={
            "id": "gw_vehicle_1",
            "gateway_vehicle_id": "gw_vehicle_1",
            "search_id": "search_123",
            "gateway_search_id": "search_123",
            "supplier_id": "locauto_rent",
            "supplier_vehicle_id": "MDMR",
            "name": "Fiat Panda",
            "category": "mini",
            "pricing": {
                "currency": "EUR",
                "total_price": 1067,
                "daily_rate": 133.38,
            },
            "supplier_data": {"rate_id": "RATE1"},
            "context_valid_until": "2099-01-01T00:00:00+00:00",
        },
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
    assert cache.restored_vehicle is not None
    assert cache.restored_vehicle[0] == "gw_vehicle_1"
    assert cache.restored_vehicle[1]["search_id"] == "search_123"


async def test_create_booking_uses_context_when_cached_vehicle_missing_search_id(monkeypatch):
    adapter = FakeAdapter()
    cache = FakeCache(
        {
            "id": "gw_vehicle_1",
            "supplier_id": "locauto_rent",
            "supplier_vehicle_id": "OLD",
            "name": "Old cached vehicle",
            "pricing": {"currency": "EUR", "total_price": 999, "daily_rate": 111},
        }
    )
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        vehicle_context={
            "id": "gw_vehicle_1",
            "search_id": "search_123",
            "supplier_id": "locauto_rent",
            "supplier_vehicle_id": "MDMR",
            "name": "Fiat Panda",
            "category": "mini",
            "pricing": {
                "currency": "EUR",
                "total_price": 1067,
                "daily_rate": 133.38,
            },
            "context_valid_until": "2099-01-01T00:00:00+00:00",
        },
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="anshulmankotia1997@gmail.com",
            phone="8278825392",
            age=35,
        ),
        laravel_booking_id=104,
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    response = await booking_service.create_booking(request, cache)

    assert response.supplier_booking_id == "LC123"
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1].supplier_vehicle_id == "MDMR"
    assert cache.restored_vehicle is not None
    assert cache.restored_vehicle[1]["search_id"] == "search_123"


async def test_create_booking_rejects_cached_vehicle_missing_search_id_without_context(
    monkeypatch,
):
    adapter = FakeAdapter()
    cache = FakeCache(
        {
            "id": "gw_vehicle_1",
            "supplier_id": "locauto_rent",
            "supplier_vehicle_id": "MDMR",
            "name": "Fiat Panda",
            "pricing": {"currency": "EUR", "total_price": 1067, "daily_rate": 133.38},
        }
    )
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
        laravel_booking_id=104,
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    with pytest.raises(ValueError, match="missing search_id"):
        await booking_service.create_booking(request, cache)

    assert adapter.calls == []
    assert cache.restored_vehicle is None
    assert cache.redis.deleted_keys == ["gateway_booking_lock:104"]


async def test_create_booking_rejects_context_search_id_mismatch(monkeypatch):
    adapter = FakeAdapter()
    cache = FakeCache(None)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        vehicle_context={
            "id": "gw_vehicle_1",
            "search_id": "search_other",
            "supplier_id": "locauto_rent",
            "supplier_vehicle_id": "MDMR",
            "name": "Fiat Panda",
            "pricing": {"currency": "EUR", "total_price": 1067, "daily_rate": 133.38},
            "context_valid_until": "2099-01-01T00:00:00+00:00",
        },
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="anshulmankotia1997@gmail.com",
            phone="8278825392",
            age=35,
        ),
        laravel_booking_id=104,
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    with pytest.raises(ValueError, match="does not belong to search"):
        await booking_service.create_booking(request, cache)

    assert adapter.calls == []
    assert cache.restored_vehicle is None
    assert cache.redis.deleted_keys == ["gateway_booking_lock:104"]


async def test_create_booking_rejects_expired_request_vehicle_context(monkeypatch):
    adapter = FakeAdapter()
    cache = FakeCache(None)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_123",
        vehicle_context={
            "id": "gw_vehicle_1",
            "search_id": "search_123",
            "supplier_id": "locauto_rent",
            "supplier_vehicle_id": "MDMR",
            "name": "Fiat Panda",
            "pricing": {"currency": "EUR", "total_price": 1067, "daily_rate": 133.38},
            "context_valid_until": "2000-01-01T00:00:00+00:00",
        },
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="anshulmankotia1997@gmail.com",
            phone="8278825392",
            age=35,
        ),
        laravel_booking_id=104,
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    with pytest.raises(ValueError, match="has expired"):
        await booking_service.create_booking(request, cache)

    assert adapter.calls == []
    assert cache.restored_vehicle is None
    assert cache.redis.deleted_keys == ["gateway_booking_lock:104"]


async def test_create_booking_rejects_search_id_mismatch(monkeypatch):
    vehicle_data = {
        "id": "gw_vehicle_1",
        "search_id": "search_original",
        "supplier_id": "locauto_rent",
        "supplier_vehicle_id": "MDMR",
        "name": "Fiat Panda",
        "pricing": {"currency": "EUR", "total_price": 1067, "daily_rate": 133.38},
    }
    adapter = FakeAdapter()
    cache = FakeCache(vehicle_data)
    request = CreateBookingRequest(
        vehicle_id="gw_vehicle_1",
        search_id="search_other",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="anshulmankotia1997@gmail.com",
            phone="8278825392",
            age=35,
        ),
        laravel_booking_id=104,
    )
    monkeypatch.setattr(booking_service, "get_adapter", lambda supplier_id: adapter)

    with pytest.raises(ValueError, match="does not belong to search"):
        await booking_service.create_booking(request, cache)

    assert adapter.calls == []
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
