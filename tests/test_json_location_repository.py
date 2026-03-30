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


if __name__ == "__main__":
    unittest.main()
