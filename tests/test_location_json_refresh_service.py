import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.location_json_refresh_service import LocationJsonRefreshService


class FakeAdapter:
    def __init__(self, supplier_id: str, locations: list[dict]):
        self.supplier_id = supplier_id
        self._locations = locations

    async def get_locations(self) -> list[dict]:
        return self._locations


class FailingAdapter:
    def __init__(self, supplier_id: str, error: Exception):
        self.supplier_id = supplier_id
        self._error = error

    async def get_locations(self) -> list[dict]:
        raise self._error


class LocationJsonRefreshServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_provider_timeout_uses_configured_location_refresh_window(self) -> None:
        service = LocationJsonRefreshService(adapters=[])

        with patch("app.services.location_json_refresh_service.get_settings") as get_settings:
            get_settings.return_value.location_refresh_provider_timeout_seconds = 180.0

            self.assertEqual(service._provider_timeout_seconds(SimpleNamespace()), 180.0)

    def test_provider_timeout_allows_adapter_specific_override(self) -> None:
        service = LocationJsonRefreshService(adapters=[])
        adapter = SimpleNamespace(location_refresh_timeout_seconds=240.0)

        self.assertEqual(service._provider_timeout_seconds(adapter), 240.0)

    async def test_refresh_filters_locations_outside_supplier_country_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "wheelsys",
                        [
                            {
                                "provider": "wheelsys",
                                "provider_location_id": "ATH",
                                "name": "Athens Airport",
                                "city": "Athens",
                                "country": "Greece",
                                "country_code": "GR",
                                "location_type": "airport",
                                "latitude": 37.9364,
                                "longitude": 23.9445,
                                "iata": "ATH",
                            },
                            {
                                "provider": "wheelsys",
                                "provider_location_id": "MAIN",
                                "name": "Airport-MCO- Orlando",
                                "city": "Orlando",
                                "country": "United States",
                                "country_code": "US",
                                "location_type": "airport",
                                "latitude": 28.429394,
                                "longitude": -81.308993,
                                "iata": "MCO",
                            },
                        ],
                    ),
                ],
                output_path=output_path,
            )

            with patch(
                "app.services.location_json_refresh_service.get_supplier_config",
                return_value={"countries": ["GR"]},
            ):
                summary = await service.refresh()

            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["locations_received"], 2)
        self.assertEqual(summary["locations_filtered_by_country_scope"], 1)
        self.assertEqual(summary["providers_country_filtered_ids"], ["wheelsys"])
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["iata"], "ATH")
        providers = {
            provider["provider"]: provider["pickup_id"] for provider in exported[0]["providers"]
        }
        self.assertEqual(providers, {"wheelsys": "ATH"})

    async def test_refresh_does_not_filter_global_supplier_country_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "internal",
                        [
                            {
                                "provider": "internal",
                                "provider_location_id": "1",
                                "name": "Dubai Airport",
                                "city": "Dubai",
                                "country": "United Arab Emirates",
                                "country_code": "AE",
                                "location_type": "airport",
                                "latitude": 25.250291,
                                "longitude": 55.345171,
                                "iata": "DXB",
                            },
                        ],
                    ),
                ],
                output_path=output_path,
            )

            with patch(
                "app.services.location_json_refresh_service.get_supplier_config",
                return_value={"countries": ["global"]},
            ):
                summary = await service.refresh()

            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["locations_received"], 1)
        self.assertEqual(summary["internal_locations_received"], 1)
        self.assertEqual(summary["locations_filtered_by_country_scope"], 0)
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["iata"], "DXB")

    async def test_refresh_exports_current_cmn_codes_with_public_provider_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "green_motion",
                        [
                            {
                                "provider": "green_motion",
                                "provider_location_id": "354",
                                "name": "Casablanca Airport",
                                "city": "Casablanca",
                                "country": "Morocco",
                                "country_code": "MA",
                                "location_type": "airport",
                                "latitude": 33.37028036000844,
                                "longitude": -7.583008423934328,
                                "iata": "CMN",
                            }
                        ],
                    ),
                    FakeAdapter(
                        "usave",
                        [
                            {
                                "provider": "usave",
                                "provider_location_id": "354",
                                "name": "Casablanca Airport",
                                "city": "Casablanca",
                                "country": "Morocco",
                                "country_code": "MA",
                                "location_type": "airport",
                                "latitude": 33.37028036000844,
                                "longitude": -7.583008423934328,
                                "iata": "CMN",
                            }
                        ],
                    ),
                    FakeAdapter(
                        "surprice",
                        [
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
                            }
                        ],
                    ),
                    FakeAdapter(
                        "renteon",
                        [
                            {
                                "provider": "renteon",
                                "provider_location_id": "MA-CAS-CMN",
                                "name": "Casablanca airport",
                                "country": "MA",
                                "country_code": "MA",
                                "location_type": "airport",
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            summary = await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["providers_succeeded"], 4)
        self.assertEqual(summary["providers_failed"], 0)
        self.assertEqual(summary["providers_failed_ids"], [])
        self.assertEqual(summary["locations_received"], 4)
        self.assertFalse(summary["internal_provider_failed"])

        cmn = next(item for item in exported if item.get("iata") == "CMN")
        providers = {item["provider"]: item["pickup_id"] for item in cmn["providers"]}

        self.assertEqual(cmn["name"], "Casablanca Airport (CMN)")
        self.assertEqual(providers["greenmotion"], "354")
        self.assertEqual(providers["usave"], "354")
        self.assertEqual(providers["surprice"], "CMN:CMNA01")
        self.assertEqual(providers["renteon"], "MA-CAS-CMN")
        self.assertNotIn("green_motion", providers)

    async def test_refresh_exports_provider_metadata_needed_for_runtime_searches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "surprice",
                        [
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
                        ],
                    ),
                ],
                output_path=output_path,
            )

            await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exported[0]["country_code"], "MA")
        self.assertEqual(exported[0]["provider_count"], 1)
        provider = exported[0]["providers"][0]
        self.assertEqual(provider["provider"], "surprice")
        self.assertEqual(provider["pickup_id"], "CMN:CMNA01")
        self.assertEqual(provider["dropoffs"], ["CMNC01:CMNC01"])
        self.assertTrue(provider["supports_one_way"])
        self.assertEqual(provider["extended_location_code"], "CMNA01")
        self.assertEqual(provider["extended_dropoff_code"], "CMNC01")
        self.assertEqual(provider["country_code"], "MA")
        self.assertEqual(provider["iata"], "CMN")
        self.assertEqual(provider["provider_code"], "surprice")

    async def test_refresh_rebuilds_only_from_fresh_provider_rows_when_a_provider_fails(
        self,
    ) -> None:
        existing_export = [
            {
                "unified_location_id": 416203036,
                "name": "Casablanca Airport (CMN)",
                "aliases": ["Casablanca Airport", "CMN"],
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "latitude": 33.374138,
                "longitude": -7.570431,
                "location_type": "airport",
                "iata": "CMN",
                "providers": [
                    {
                        "provider": "greenmotion",
                        "pickup_id": "354",
                        "original_name": "Casablanca Airport",
                        "dropoffs": [],
                        "latitude": 33.37028036000844,
                        "longitude": -7.583008423934328,
                    }
                ],
                "our_location_id": None,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            output_path.write_text(json.dumps(existing_export), encoding="utf-8")

            service = LocationJsonRefreshService(
                adapters=[
                    FailingAdapter("green_motion", RuntimeError("timeout")),
                    FakeAdapter(
                        "surprice",
                        [
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
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            summary = await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["providers_failed"], 1)
        self.assertEqual(summary["providers_failed_ids"], ["greenmotion"])
        self.assertFalse(summary["internal_provider_failed"])
        cmn = next(item for item in exported if item.get("iata") == "CMN")
        providers = {item["provider"]: item["pickup_id"] for item in cmn["providers"]}
        self.assertEqual(providers, {"surprice": "CMN:CMNA01"})

    async def test_refresh_reports_internal_adapter_failure_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FailingAdapter("internal", RuntimeError("laravel unavailable")),
                    FakeAdapter(
                        "surprice",
                        [
                            {
                                "provider": "surprice",
                                "provider_location_id": "DXB:DXBA01",
                                "name": "Dubai Airport",
                                "city": "Dubai",
                                "country": "United Arab Emirates",
                                "country_code": "AE",
                                "location_type": "airport",
                                "latitude": 25.248081,
                                "longitude": 55.345093,
                                "iata": "DXB",
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            summary = await service.refresh()

        self.assertEqual(summary["status"], "completed_with_failures")
        self.assertEqual(summary["providers_failed_ids"], ["internal"])
        self.assertTrue(summary["internal_provider_failed"])
        self.assertFalse(summary["internal_provider_succeeded"])
        self.assertEqual(summary["internal_locations_received"], 0)

    async def test_refresh_discards_stale_existing_rows_when_provider_returns_new_rows(
        self,
    ) -> None:
        existing_export = [
            {
                "unified_location_id": 416203036,
                "name": "Casablanca Airport (CMN)",
                "aliases": ["Casablanca Airport", "CMN"],
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "latitude": 33.374138,
                "longitude": -7.570431,
                "location_type": "airport",
                "iata": "CMN",
                "providers": [
                    {
                        "provider": "usave",
                        "pickup_id": "354",
                        "original_name": "Casablanca Airport",
                        "dropoffs": [],
                        "latitude": 33.37028036000844,
                        "longitude": -7.583008423934328,
                    }
                ],
                "our_location_id": None,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            output_path.write_text(json.dumps(existing_export), encoding="utf-8")

            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "usave",
                        [
                            {
                                "provider": "usave",
                                "provider_location_id": "355",
                                "name": "Casablanca Downtown",
                                "city": "Casablanca",
                                "country": "Morocco",
                                "country_code": "MA",
                                "location_type": "downtown",
                                "latitude": 33.58445900869179,
                                "longitude": -7.619644280221447,
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        all_usave_pickups = {
            provider["pickup_id"]
            for location in exported
            for provider in location.get("providers", [])
            if provider.get("provider") == "usave"
        }
        self.assertEqual(all_usave_pickups, {"355"})

    async def test_refresh_does_not_inherit_stale_iata_from_existing_internal_rows(self) -> None:
        existing_export = [
            {
                "unified_location_id": 416203036,
                "name": "Casablanca Airport (CMN)",
                "aliases": ["Casablanca Airport", "CMN"],
                "city": "Casablanca",
                "country": "Morocco",
                "country_code": "MA",
                "latitude": 33.374138,
                "longitude": -7.570431,
                "location_type": "airport",
                "iata": "CMN",
                "providers": [
                    {
                        "provider": "internal",
                        "pickup_id": "155",
                        "original_name": "Mohamed V airport, Casablanca, Morocco",
                        "dropoffs": [],
                        "latitude": 33.372954,
                        "longitude": -7.577391,
                    }
                ],
                "our_location_id": "internal_old",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            output_path.write_text(json.dumps(existing_export), encoding="utf-8")

            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "internal",
                        [
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
                                "our_location_id": "internal_new",
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(len(exported), 1)
        self.assertIsNone(exported[0]["iata"])
        self.assertEqual(exported[0]["our_location_id"], "internal_new")

    async def test_refresh_preserves_internal_location_hashes_from_adapter_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "internal",
                        [
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
                                "our_location_id": "internal_abc123",
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["our_location_id"], "internal_abc123")

    async def test_refresh_normalizes_alias_provider_ids_for_okmobility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "unified_locations.json"
            service = LocationJsonRefreshService(
                adapters=[
                    FakeAdapter(
                        "ok_mobility",
                        [
                            {
                                "provider": "ok_mobility",
                                "provider_location_id": "01",
                                "name": "OK PMI",
                                "city": "Mallorca",
                                "country": "Spain",
                                "country_code": "ES",
                                "location_type": "airport",
                                "latitude": 39.54,
                                "longitude": 2.74,
                            }
                        ],
                    ),
                ],
                output_path=output_path,
            )

            await service.refresh()
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        providers = [
            provider["provider"]
            for location in exported
            for provider in location.get("providers", [])
        ]
        self.assertIn("okmobility", providers)
        self.assertNotIn("ok_mobility", providers)


if __name__ == "__main__":
    unittest.main()
