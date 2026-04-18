import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.json_location_repository import JsonLocationRepository


def _location(uid: int, name: str) -> dict:
    return {
        "unified_location_id": uid,
        "name": name,
        "aliases": [name],
        "city": "Casablanca",
        "country": "Morocco",
        "latitude": 33.57,
        "longitude": -7.58,
        "location_type": "airport",
        "providers": [
            {
                "provider": "surprice",
                "pickup_id": f"pickup-{uid}",
                "original_name": name,
                "dropoffs": [],
            }
        ],
    }


class JsonLocationRepositoryTest(unittest.TestCase):
    def test_repository_refreshes_when_json_file_changes_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "unified_locations.json"
            json_path.write_text(json.dumps([_location(1, "Old Airport")]), encoding="utf-8")

            with patch("app.services.json_location_repository._DATA_PATH", json_path):
                repository = JsonLocationRepository()

                first = repository.get_location_by_unified_id(1)
                self.assertEqual(first["name"], "Old Airport")

                json_path.write_text(json.dumps([_location(2, "New Airport")]), encoding="utf-8")

                second = repository.get_location_by_unified_id(2)
                self.assertIsNotNone(second)
                self.assertEqual(second["name"], "New Airport")
                self.assertIsNone(repository.get_location_by_unified_id(1))

                metadata = repository.metadata()
                self.assertEqual(metadata["location_count"], 1)
                self.assertIsNotNone(metadata["location_data_version"])
                self.assertIsNotNone(metadata["location_data_mtime"])
                self.assertIsNotNone(metadata["location_data_size"])
                self.assertEqual(metadata["location_data_path"], str(json_path))
                self.assertIsNotNone(metadata["location_data_loaded_at"])


class DropoffCandidatesTest(unittest.TestCase):
    def _entry(self, uid: int, name: str, country_code: str, providers: list[dict]) -> dict:
        return {
            "unified_location_id": uid,
            "name": name,
            "aliases": [name],
            "city": name,
            "country": "Test Country",
            "country_code": country_code,
            "latitude": 0.0,
            "longitude": 0.0,
            "location_type": "airport",
            "providers": providers,
        }

    def _with_dataset(self, dataset: list[dict]):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "unified_locations.json"
        path.write_text(json.dumps(dataset), encoding="utf-8")
        patcher = patch("app.services.json_location_repository._DATA_PATH", path)
        patcher.start()
        repo = JsonLocationRepository()
        return tmp, patcher, repo

    def test_filters_by_provider_and_country_and_excludes_pickup(self) -> None:
        dataset = [
            self._entry(1, "Pickup AE", "AE", [{"provider": "greenmotion", "pickup_id": "GM-AE-1", "dropoffs": []}]),
            self._entry(2, "Dubai Mall",   "AE", [{"provider": "greenmotion", "pickup_id": "GM-AE-2", "dropoffs": []}]),
            self._entry(3, "Abu Dhabi",    "AE", [{"provider": "greenmotion", "pickup_id": "GM-AE-3", "dropoffs": []}]),
            self._entry(4, "London",       "GB", [{"provider": "greenmotion", "pickup_id": "GM-GB-1", "dropoffs": []}]),
            self._entry(5, "Madrid Surprice Only", "AE", [{"provider": "surprice", "pickup_id": "SP-1", "dropoffs": []}]),
        ]

        tmp, patcher, repo = self._with_dataset(dataset)
        try:
            results = repo.find_dropoff_candidates(provider="greenmotion", pickup_unified_id=1)
            uids = [location["unified_location_id"] for location in results]
            self.assertEqual(sorted(uids), [2, 3])  # same country, excludes pickup, excludes other providers
        finally:
            patcher.stop()
            tmp.cleanup()

    def test_country_override_overrides_pickup_country(self) -> None:
        dataset = [
            self._entry(1, "Pickup AE", "AE", [{"provider": "greenmotion", "pickup_id": "GM-AE-1", "dropoffs": []}]),
            self._entry(2, "Dubai",     "AE", [{"provider": "greenmotion", "pickup_id": "GM-AE-2", "dropoffs": []}]),
            self._entry(3, "London",    "GB", [{"provider": "greenmotion", "pickup_id": "GM-GB-1", "dropoffs": []}]),
        ]

        tmp, patcher, repo = self._with_dataset(dataset)
        try:
            results = repo.find_dropoff_candidates(
                provider="greenmotion",
                pickup_unified_id=1,
                country_code="GB",
            )
            uids = [location["unified_location_id"] for location in results]
            self.assertEqual(uids, [3])
        finally:
            patcher.stop()
            tmp.cleanup()

    def test_respects_limit(self) -> None:
        dataset = [
            self._entry(uid, f"Loc {uid}", "AE", [{"provider": "greenmotion", "pickup_id": f"GM-{uid}", "dropoffs": []}])
            for uid in range(1, 11)
        ]

        tmp, patcher, repo = self._with_dataset(dataset)
        try:
            results = repo.find_dropoff_candidates(provider="greenmotion", pickup_unified_id=1, limit=3)
            self.assertEqual(len(results), 3)
        finally:
            patcher.stop()
            tmp.cleanup()

    def test_returns_empty_when_provider_absent(self) -> None:
        dataset = [
            self._entry(1, "Pickup AE", "AE", [{"provider": "okmobility", "pickup_id": "OK-1", "dropoffs": []}]),
            self._entry(2, "Dubai",     "AE", [{"provider": "okmobility", "pickup_id": "OK-2", "dropoffs": []}]),
        ]

        tmp, patcher, repo = self._with_dataset(dataset)
        try:
            results = repo.find_dropoff_candidates(provider="greenmotion", pickup_unified_id=1)
            self.assertEqual(results, [])
        finally:
            patcher.stop()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
