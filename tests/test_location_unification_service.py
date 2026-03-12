import unittest

from app.services.location_unification_service import LocationUnificationService


class LocationUnificationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = LocationUnificationService()

    def test_it_merges_airport_aliases_with_shared_iata(self) -> None:
        locations = [
            {
                "provider": "green_motion",
                "provider_location_id": "359",
                "name": "Marrakech Airport",
                "city": "Marrakech",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 31.600026,
                "longitude": -8.024344,
                "iata": "RAK",
            },
            {
                "provider": "xdrive",
                "provider_location_id": "21",
                "name": "Marrakech-Menara Airport (RAK)",
                "city": "Marrakesh",
                "country": "MA",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 31.6069,
                "longitude": -8.0363,
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["iata"], "RAK")
        self.assertEqual(unified[0]["city"], "Marrakech")
        self.assertEqual(len(unified[0]["providers"]), 2)

    def test_it_keeps_distinct_airports_separate_when_identifiers_differ(self) -> None:
        locations = [
            {
                "provider": "surprice",
                "provider_location_id": "DXB:DXB",
                "name": "Dubai Airport",
                "city": "Dubai",
                "country": "United Arab Emirates",
                "country_code": "AE",
                "location_type": "airport",
                "latitude": 25.2532,
                "longitude": 55.3657,
            },
            {
                "provider": "surprice",
                "provider_location_id": "DWC:DWC",
                "name": "Dubai Al Maktoum Airport",
                "city": "Dubai",
                "country": "United Arab Emirates",
                "country_code": "AE",
                "location_type": "airport",
                "latitude": 24.8964,
                "longitude": 55.1614,
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 2)
        self.assertCountEqual([item["iata"] for item in unified], ["DXB", "DWC"])

    def test_it_finds_canonical_results_by_alias(self) -> None:
        unified_locations = self.service.build_unified_locations(
            [
                {
                    "provider": "green_motion",
                    "provider_location_id": "359",
                    "name": "Marrakech Airport",
                    "city": "Marrakech",
                    "country": "Morocco",
                    "country_code": "MA",
                    "location_type": "airport",
                    "latitude": 31.600026,
                    "longitude": -8.024344,
                    "iata": "RAK",
                },
                {
                    "provider": "surprice",
                    "provider_location_id": "RAKC01:RAKC01",
                    "name": "Marrakesh Downtown",
                    "city": "Marrakesh",
                    "country": "Morocco",
                    "country_code": "MA",
                    "location_type": "office",
                    "latitude": 31.635595,
                    "longitude": -8.01005,
                },
            ]
        )

        results = self.service.search_locations(unified_locations, "marrakesh", limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["city"], "Marrakech")
        self.assertIn("Marrakesh", results[0]["aliases"])

    def test_it_collapses_generic_same_city_other_rows_without_identifiers(self) -> None:
        locations = [
            {
                "provider": "ok_mobility",
                "provider_location_id": "350",
                "name": "Marrakech",
                "city": "Marrakech",
                "country": "159",
                "country_code": "",
                "location_type": "unknown",
                "latitude": 31.50,
                "longitude": 0.005,
            },
            {
                "provider": "ok_mobility",
                "provider_location_id": "352",
                "name": "Marrakech",
                "city": "Marrakech",
                "country": "159",
                "country_code": "",
                "location_type": "unknown",
                "latitude": 31.37,
                "longitude": 8.00,
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["provider_count"], 2)

    def test_search_hides_generic_city_rows_when_specific_city_variants_exist(self) -> None:
        unified_locations = self.service.build_unified_locations(
            [
                {
                    "provider": "green_motion",
                    "provider_location_id": "359",
                    "name": "Marrakech Airport",
                    "city": "Marrakech",
                    "country": "Morocco",
                    "country_code": "MA",
                    "location_type": "airport",
                    "latitude": 31.600026,
                    "longitude": -8.024344,
                    "iata": "RAK",
                },
                {
                    "provider": "surprice",
                    "provider_location_id": "RAKC01:RAKC01",
                    "name": "Marrakesh Downtown",
                    "city": "Marrakesh",
                    "country": "Morocco",
                    "country_code": "MA",
                    "location_type": "office",
                    "latitude": 31.635595,
                    "longitude": -8.01005,
                },
                {
                    "provider": "ok_mobility",
                    "provider_location_id": "350",
                    "name": "Marrakech",
                    "city": "Marrakech",
                    "country": "159",
                    "country_code": "",
                    "location_type": "unknown",
                    "latitude": 31.50,
                    "longitude": 0.005,
                },
            ]
        )

        results = self.service.search_locations(unified_locations, "marrakesh", limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual([item["name"] for item in results], ["Marrakech Airport", "Marrakech Downtown"])

    def test_it_merges_terminal_airport_rows_into_nearby_iata_airport(self) -> None:
        locations = [
            {
                "provider": "green_motion",
                "provider_location_id": "59610",
                "name": "Dubai Airport Terminal 1",
                "city": "Dubai",
                "country": "United Arab Emirates",
                "country_code": "AE",
                "location_type": "airport",
                "latitude": 25.248081,
                "longitude": 55.345093,
            },
            {
                "provider": "green_motion",
                "provider_location_id": "60847",
                "name": "Dubai Airport Terminal 2",
                "city": "Dubai",
                "country": "United Arab Emirates",
                "country_code": "AE",
                "location_type": "airport",
                "latitude": 25.248081,
                "longitude": 55.345093,
            },
            {
                "provider": "xdrive",
                "provider_location_id": "83",
                "name": "Dubai International Airport (DXB)",
                "city": "Dubai",
                "country": "United Arab Emirates",
                "country_code": "AE",
                "location_type": "airport",
                "latitude": 25.254444,
                "longitude": 55.356389,
                "iata": "DXB",
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["iata"], "DXB")
        self.assertEqual(unified[0]["provider_count"], 3)


if __name__ == "__main__":
    unittest.main()
