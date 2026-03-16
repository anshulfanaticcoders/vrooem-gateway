import sys
import types
import unittest

sys.modules.setdefault("httpx", types.SimpleNamespace())

from app.adapters.recordgo import RecordGoAdapter
from app.schemas.pricing import Pricing
from app.schemas.vehicle import Vehicle, VehicleLocation


class RecordGoAdapterTest(unittest.TestCase):
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
