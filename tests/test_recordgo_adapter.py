import sys
import types
import unittest
import asyncio

class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass


sys.modules.setdefault(
    "httpx",
    types.SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Response=object,
        TimeoutException=Exception,
    ),
)

from app.adapters.recordgo import RecordGoAdapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.pricing import Pricing
from app.schemas.search import SearchRequest
from app.schemas.vehicle import Vehicle, VehicleLocation


class RecordGoAdapterTest(unittest.TestCase):
    def test_static_locations_include_iata_and_coordinates_for_lanzarote(self) -> None:
        adapter = RecordGoAdapter()

        locations = asyncio.run(adapter.get_locations())

        lanz = next(location for location in locations if location["provider_location_id"] == "34903")
        self.assertEqual(lanz["name"], "Lanzarote Airport")
        self.assertEqual(lanz["iata"], "ACE")
        self.assertAlmostEqual(lanz["latitude"], 28.9462, places=4)
        self.assertAlmostEqual(lanz["longitude"], -13.6052, places=4)

    def test_groups_product_variants_into_one_vehicle_with_booking_products(self) -> None:
        adapter = RecordGoAdapter()

        vehicles = [
            Vehicle(
                id="gw_rg_1",
                supplier_id="recordgo",
                supplier_vehicle_id="MDMR_11_A",
                name="Fiat 500 - Basic",
                category="mini",
                make="Fiat",
                model="500",
                image_url="https://example.com/fiat.png",
                pickup_location=VehicleLocation(
                    supplier_location_id="35001",
                    name="Loures Airport",
                    country_code="PT",
                ),
                pricing=Pricing(currency="EUR", total_price=180.0, daily_rate=22.5),
                supplier_data={
                    "acriss_code": "MDMR",
                    "product_data": {
                        "type": "BAS",
                        "product_id": 11,
                        "product_ver": 1,
                        "rate_prod_ver": "A",
                        "total": 180.0,
                        "price_per_day": 22.5,
                    },
                },
            ),
            Vehicle(
                id="gw_rg_2",
                supplier_id="recordgo",
                supplier_vehicle_id="MDMR_12_B",
                name="Fiat 500 - Premium",
                category="mini",
                make="Fiat",
                model="500",
                image_url="https://example.com/fiat.png",
                pickup_location=VehicleLocation(
                    supplier_location_id="35001",
                    name="Loures Airport",
                    country_code="PT",
                ),
                pricing=Pricing(currency="EUR", total_price=220.0, daily_rate=27.5),
                supplier_data={
                    "acriss_code": "MDMR",
                    "product_data": {
                        "type": "PRE",
                        "product_id": 12,
                        "product_ver": 1,
                        "rate_prod_ver": "B",
                        "total": 220.0,
                        "price_per_day": 27.5,
                    },
                },
            ),
        ]

        grouped = adapter._group_product_variants(vehicles)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].pricing.total_price, 180.0)
        self.assertEqual(grouped[0].supplier_vehicle_id, "MDMR")
        self.assertEqual(len(grouped[0].supplier_data["products"]), 2)
        self.assertEqual(
            [product["type"] for product in grouped[0].supplier_data["products"]],
            ["BAS", "PRE"],
        )


    def test_parse_acriss_keeps_missing_specs_missing(self) -> None:
        adapter = RecordGoAdapter()
        request = SearchRequest(
            unified_location_id=1,
            pickup_date=__import__("datetime").date(2026, 5, 21),
            pickup_time=__import__("datetime").time(9, 0),
            dropoff_date=__import__("datetime").date(2026, 5, 24),
            dropoff_time=__import__("datetime").time(9, 0),
            currency="EUR",
            country_code="PT",
            driver_age=35,
        )
        pickup = ProviderLocationEntry(
            provider="recordgo",
            pickup_id="35001",
            original_name="Loures Airport",
            latitude=38.8,
            longitude=-9.1,
        )
        acriss = {
            "acrissCode": "MDMR",
            "acrissId": 10,
            "acrissSeats": None,
            "acrissDoors": None,
            "acrissSuitcase": None,
            "gearboxType": None,
            "imagesArray": [{"isDefault": True, "acrissImgUrl": "https://example.com/fiat.png", "acrissDisplayName": "Fiat 500"}],
            "products": [{
                "rateProdVer": "A",
                "priceTaxIncBookingDiscount": 180.0,
                "priceTaxIncDayDiscount": 22.5,
                "product": {
                    "productId": 11,
                    "productVer": 1,
                    "productName": "Basic",
                    "kmPolicyComercial": None,
                    "productComplementsIncluded": [],
                    "productComplementsAutom": [],
                },
            }],
        }

        vehicles = adapter._parse_acriss_vehicles(
            acriss=acriss,
            request=request,
            rental_days=3,
            pickup_entry=pickup,
            sell_code_ver="1",
            sell_code=110,
            country_code="PT",
            pickup_branch=35001,
            dropoff_branch=35001,
        )

        self.assertEqual(len(vehicles), 1)
        vehicle = vehicles[0]

        from app.services.search_vehicle_payload_builder import build_search_vehicle_payload

        payload = build_search_vehicle_payload(vehicle)
        self.assertEqual(payload.specs.transmission, "manual")
        self.assertEqual(payload.specs.fuel, "petrol")
        self.assertIsNone(payload.specs.seating_capacity)
        self.assertIsNone(payload.specs.doors)
        self.assertIsNone(payload.specs.luggage_large)
        self.assertIsNone(payload.specs.luggage_small)
        self.assertTrue(payload.specs.air_conditioning)
        self.assertIsNone(payload.policies.mileage_policy)
