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

    def test_it_preserves_provider_metadata_needed_for_runtime_searches(self) -> None:
        locations = [
            {
                "provider": "surprice",
                "provider_location_id": "CMN:CMNA01",
                "name": "Casablanca Airport",
                "city": "Nouaceur",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 33.381853,
                "longitude": -7.545275,
                "iata": "CMN",
                "dropoffs": ["CMNC01:CMNC01"],
                "supports_one_way": True,
                "extended_location_code": "CMNA01",
                "extended_dropoff_code": "CMNC01",
                "provider_code": "surprice",
            }
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        provider = unified[0]["providers"][0]
        self.assertEqual(provider["provider"], "surprice")
        self.assertEqual(provider["pickup_id"], "CMN:CMNA01")
        self.assertEqual(provider["dropoffs"], ["CMNC01:CMNC01"])
        self.assertTrue(provider["supports_one_way"])
        self.assertEqual(provider["extended_location_code"], "CMNA01")
        self.assertEqual(provider["extended_dropoff_code"], "CMNC01")
        self.assertEqual(provider["country_code"], "MA")
        self.assertEqual(provider["iata"], "CMN")
        self.assertEqual(provider["provider_code"], "surprice")

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

    def test_it_merges_nearby_airports_even_when_provider_cities_differ(self) -> None:
        locations = [
            {
                "provider": "internal",
                "provider_location_id": "66",
                "name": "Tit Mellil Airport, Casablanca, Morocco",
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 33.594703,
                "longitude": -7.462264,
            },
            {
                "provider": "internal",
                "provider_location_id": "155",
                "name": "Mohamed V airport, Casablanca, Morocco",
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 33.372954,
                "longitude": -7.577391,
            },
            {
                "provider": "surprice",
                "provider_location_id": "CMN:CMNA01",
                "name": "Casablanca Airport",
                "city": "Nouaceur",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 33.381853,
                "longitude": -7.545275,
                "iata": "CMN",
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 2)
        cmn = next(item for item in unified if item.get("iata") == "CMN")
        self.assertEqual(cmn["provider_count"], 2)
        cmn_pickups = {provider["pickup_id"] for provider in cmn["providers"]}
        self.assertEqual(cmn_pickups, {"155", "CMN:CMNA01"})
        names = {item["name"] for item in unified}
        self.assertIn("Tit Mellil Airport", names)
        self.assertNotIn("Mohamed V airport", names)

    def test_it_drops_weaker_duplicate_rows_for_the_same_our_location_id(self) -> None:
        locations = [
            {
                "provider": "internal",
                "provider_location_id": "154",
                "name": "Mohamed V airport, Casablanca, Morocco",
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 20.838278,
                "longitude": 76.59668,
                "our_location_id": "internal_641e4a7fa6e3c4841a7839f231a161ed",
            },
            {
                "provider": "internal",
                "provider_location_id": "155",
                "name": "Mohamed V airport, Casablanca, Morocco",
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 33.372954,
                "longitude": -7.577391,
                "our_location_id": "internal_641e4a7fa6e3c4841a7839f231a161ed",
            },
            {
                "provider": "surprice",
                "provider_location_id": "CMN:CMNA01",
                "name": "Casablanca Airport",
                "city": "Nouaceur",
                "country": "Morocco",
                "country_code": "MA",
                "location_type": "airport",
                "latitude": 33.381853,
                "longitude": -7.545275,
                "iata": "CMN",
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        cmn = unified[0]
        self.assertEqual(cmn["iata"], "CMN")
        pickup_ids = {provider["pickup_id"] for provider in cmn["providers"]}
        self.assertEqual(pickup_ids, {"155", "CMN:CMNA01"})

    def test_it_keeps_known_static_airport_coordinates_when_other_rows_have_zero_coords(self) -> None:
        locations = [
            {
                "provider": "recordgo",
                "provider_location_id": "34903",
                "name": "Lanzarote Airport",
                "city": "Arrecife",
                "country": "Spain",
                "country_code": "IC",
                "location_type": "airport",
                "iata": "ACE",
                "latitude": 28.9462,
                "longitude": -13.6052,
            },
            {
                "provider": "other_provider",
                "provider_location_id": "ACE-legacy",
                "name": "Lanzarote Airport",
                "city": "Arrecife",
                "country": "Spain",
                "country_code": "IC",
                "location_type": "airport",
                "iata": "ACE",
                "latitude": 0.0,
                "longitude": 0.0,
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["iata"], "ACE")
        self.assertAlmostEqual(unified[0]["latitude"], 28.9462, places=4)
        self.assertAlmostEqual(unified[0]["longitude"], -13.6052, places=4)

    def test_it_maps_generic_no_coord_city_airports_to_the_primary_iata(self) -> None:
        locations = [
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
            {
                "provider": "renteon",
                "provider_location_id": "AE-DXB",
                "name": "Dubai Airport",
                "city": "Dubai",
                "country": "United Arab Emirates",
                "country_code": "AE",
                "location_type": "airport",
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["iata"], "DXB")
        self.assertEqual(unified[0]["provider_count"], 2)

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
        self.assertEqual([item["name"] for item in results], ["Marrakech Airport (RAK)", "Marrakech Downtown"])

    def test_search_prefers_downtown_rows_for_downtown_queries(self) -> None:
        unified_locations = self.service.build_unified_locations(
            [
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
                    "iata": "DXB",
                },
                {
                    "provider": "green_motion",
                    "provider_location_id": "60160",
                    "name": "Dubai Downtown",
                    "city": "Dubai",
                    "country": "United Arab Emirates",
                    "country_code": "AE",
                    "location_type": "office",
                    "latitude": 25.25812,
                    "longitude": 55.297569,
                },
                {
                    "provider": "ok_mobility",
                    "provider_location_id": "650",
                    "name": "Dubai",
                    "city": "Dubai",
                    "country": "United Arab Emirates",
                    "country_code": "AE",
                    "location_type": "unknown",
                    "latitude": 25.085,
                    "longitude": 55.215,
                },
            ]
        )

        results = self.service.search_locations(unified_locations, "dubai downtown", limit=10)

        self.assertEqual([item["name"] for item in results], ["Dubai Downtown"])

    def test_search_prefers_airport_rows_for_airport_queries(self) -> None:
        unified_locations = self.service.build_unified_locations(
            [
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
                    "iata": "DXB",
                },
                {
                    "provider": "green_motion",
                    "provider_location_id": "60160",
                    "name": "Dubai Downtown",
                    "city": "Dubai",
                    "country": "United Arab Emirates",
                    "country_code": "AE",
                    "location_type": "office",
                    "latitude": 25.25812,
                    "longitude": 55.297569,
                },
            ]
        )

        results = self.service.search_locations(unified_locations, "dubai airport", limit=10)

        self.assertEqual([item["name"] for item in results], ["Dubai Airport (DXB)"])

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

    def test_it_merges_same_iata_airports_even_when_country_formats_differ(self) -> None:
        locations = [
            {
                "provider": "locauto_rent",
                "provider_location_id": "VE",
                "name": "Venice Airport",
                "city": "Venice",
                "country": "Italy",
                "country_code": "",
                "location_type": "airport",
                "iata": "VCE",
            },
            {
                "provider": "renteon",
                "provider_location_id": "IT-VEN-VCE",
                "name": "Venezia airport",
                "city": "Venezia",
                "country": "IT",
                "country_code": "IT",
                "location_type": "airport",
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["iata"], "VCE")
        self.assertEqual(unified[0]["provider_count"], 2)

    def test_it_keeps_bus_and_train_stations_separate(self) -> None:
        locations = [
            {
                "provider": "renteon",
                "provider_location_id": "ES-VAL-BS",
                "name": "Valencia bus station",
                "city": "Valencia",
                "country": "Spain",
                "country_code": "ES",
                "location_type": "station",
            },
            {
                "provider": "renteon",
                "provider_location_id": "ES-VAL-RS",
                "name": "Valencia railway station",
                "city": "Valencia",
                "country": "Spain",
                "country_code": "ES",
                "location_type": "station",
            },
        ]

        unified = self.service.build_unified_locations(locations)

        self.assertEqual(len(unified), 2)
        self.assertCountEqual([item["name"] for item in unified], ["Valencia Bus Station", "Valencia Train Station"])



if __name__ == "__main__":
    unittest.main()
