import asyncio

from app.adapters.usave import USaveAdapter
from app.schemas.booking import CreateBookingRequest, DriverInfo
from app.schemas.pricing import Pricing
from app.schemas.vehicle import Vehicle


def test_create_booking_allows_missing_quote_id() -> None:
    adapter = USaveAdapter()
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured["content"] = kwargs.get("content", "")
        body = (
            "<gm_webservice><response><booking_ref>US123</booking_ref></response>"
            "</gm_webservice>"
        )
        return type("Resp", (), {"text": body})()

    adapter._request = fake_request  # type: ignore[method-assign]
    request = CreateBookingRequest(
        vehicle_id="gw_usave_1",
        search_id="search_123",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Testing",
            email="customer@example.com",
            phone="1000000000",
            age=35,
        ),
    )
    vehicle = Vehicle(
        id="gw_usave_1",
        supplier_id="usave",
        supplier_vehicle_id="85512",
        name="Mitsubishi Attrage, Automatic or similar",
        pricing=Pricing(currency="EUR", total_price=103.49, daily_rate=34.5),
        supplier_data={
            "vehicle_id": "85512",
            "location_id": "59610",
            "dropoff_location_id": "59610",
            "start_date": "2026-08-18",
            "start_time": "09:00",
            "end_date": "2026-08-21",
            "end_time": "09:00",
        },
    )

    response = asyncio.run(adapter.create_booking(request, vehicle))

    assert response.supplier_id == "usave"
    assert response.supplier_booking_id == "US123"
    assert "<quoteid></quoteid>" in captured["content"]
