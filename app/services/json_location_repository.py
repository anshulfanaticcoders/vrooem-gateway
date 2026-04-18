"""In-memory location repository backed by unified_locations.json."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "unified_locations.json"

# Map non-standard location_type values to valid LocationType enum values
_TYPE_NORMALIZE = {
    "airport": "airport",
    "downtown": "downtown",
    "port": "port",
    "train_station": "train_station",
    "train": "train_station",
    "railway station": "train_station",
    "hotel": "hotel",
    "bus station": "bus_station",
    "bus stop": "bus_station",
    "bus_station": "bus_station",
    "city": "downtown",
    "industrial": "other",
    "office": "other",
    "resort": "hotel",
    "unknown": "other",
}


class JsonLocationRepository:
    def __init__(self) -> None:
        self._locations: list[dict] = []
        self._by_unified_id: dict[int, dict] = {}
        self._by_provider: dict[str, dict] = {}
        self._loaded = False
        self._file_signature: dict[str, str | int | float] | None = None
        self._loaded_at: str | None = None
        self._search_service = LocationUnificationService()

    def _read_file(self) -> tuple[dict[str, str | int | float] | None, str | None]:
        path = _DATA_PATH
        if not path.exists():
            return None, None

        stat = path.stat()
        raw_text = path.read_text(encoding="utf-8")
        signature = {
            "path": str(path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "sha1": hashlib.sha1(raw_text.encode("utf-8")).hexdigest(),
        }
        return signature, raw_text

    def _ensure_loaded(self) -> None:
        current_signature, raw_text = self._read_file()
        if self._loaded and self._file_signature == current_signature:
            return

        self._locations = []
        self._by_unified_id = {}
        self._by_provider = {}

        path = _DATA_PATH
        if current_signature is None:
            logger.warning("unified_locations.json not found at %s", path)
            self._file_signature = None
            self._loaded_at = datetime.now(timezone.utc).isoformat()
            self._loaded = True
            return

        try:
            raw = json.loads(raw_text)
        except Exception:
            logger.error("Failed to read unified_locations.json", exc_info=True)
            self._file_signature = current_signature
            self._loaded_at = datetime.now(timezone.utc).isoformat()
            self._loaded = True
            return

        for entry in raw:
            uid = entry.get("unified_location_id")
            if uid is None:
                continue

            # Normalize to match Location schema
            if "id" not in entry:
                entry["id"] = f"loc_{uid}"
            if "provider_count" not in entry:
                entry["provider_count"] = len(entry.get("providers") or [])
            if "country_code" not in entry:
                entry["country_code"] = ""
            raw_type = (entry.get("location_type") or "other").lower().strip()
            entry["location_type"] = _TYPE_NORMALIZE.get(raw_type, "other")

            self._locations.append(entry)
            self._by_unified_id[uid] = entry

            for provider in entry.get("providers") or []:
                key = f"{provider['provider']}:{provider['pickup_id']}"
                self._by_provider[key] = entry

        self._file_signature = current_signature
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._loaded = True
        logger.info("Loaded %d locations from JSON file", len(self._locations))

    def list_locations(self) -> list[dict]:
        self._ensure_loaded()
        return self._locations

    def search_locations(self, query: str, limit: int = 20) -> list[dict]:
        self._ensure_loaded()
        return self._search_service.search_locations(self._locations, query, limit)

    def get_location_by_unified_id(self, unified_location_id: int) -> dict | None:
        self._ensure_loaded()
        return self._by_unified_id.get(unified_location_id)

    def get_location_by_provider_id(self, provider: str, provider_location_id: str) -> dict | None:
        self._ensure_loaded()
        key = f"{provider}:{provider_location_id}"
        return self._by_provider.get(key)

    def find_dropoff_candidates(
        self,
        provider: str,
        pickup_unified_id: int | None = None,
        country_code: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return locations where the given provider has presence, usable as one-way dropoffs.

        Filtering rules:
        - Provider must appear in the location's `providers[]`.
        - If `country_code` is given, location.country_code must match (case-insensitive).
          If omitted but `pickup_unified_id` is given, the pickup's country is used.
        - The pickup location itself is excluded.
        - Results capped by `limit`.
        """
        self._ensure_loaded()

        provider_key = provider.strip().lower()
        if not provider_key:
            return []

        resolved_country = (country_code or "").strip().upper()
        if not resolved_country and pickup_unified_id is not None:
            pickup = self._by_unified_id.get(pickup_unified_id)
            if pickup:
                resolved_country = str(pickup.get("country_code") or "").strip().upper()

        results: list[dict] = []
        for location in self._locations:
            if pickup_unified_id is not None and location.get("unified_location_id") == pickup_unified_id:
                continue

            loc_country = str(location.get("country_code") or "").strip().upper()
            if resolved_country and loc_country != resolved_country:
                continue

            has_provider = any(
                str(entry.get("provider", "")).strip().lower() == provider_key
                for entry in (location.get("providers") or [])
            )
            if not has_provider:
                continue

            results.append(location)
            if len(results) >= limit:
                break

        return results

    def reload(self) -> int:
        """Force reload from disk. Returns new location count."""
        self._locations = []
        self._by_unified_id = {}
        self._by_provider = {}
        self._file_signature = None
        self._loaded = False
        self._ensure_loaded()
        return len(self._locations)

    def metadata(self) -> dict[str, str | int | float | None]:
        self._ensure_loaded()
        return {
            "location_count": len(self._locations),
            "location_data_loaded_at": self._loaded_at,
            "location_data_version": (
                (self._file_signature or {}).get("sha1")
                if self._file_signature
                else None
            ),
            "location_data_mtime": (
                (self._file_signature or {}).get("mtime")
                if self._file_signature
                else None
            ),
            "location_data_size": (
                (self._file_signature or {}).get("size")
                if self._file_signature
                else None
            ),
            "location_data_path": (
                (self._file_signature or {}).get("path")
                if self._file_signature
                else str(_DATA_PATH)
            ),
        }
