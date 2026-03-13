"""In-memory location repository backed by unified_locations.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "unified_locations.json"


class JsonLocationRepository:
    def __init__(self) -> None:
        self._locations: list[dict] = []
        self._by_unified_id: dict[int, dict] = {}
        self._by_provider: dict[str, dict] = {}
        self._loaded = False
        self._search_service = LocationUnificationService()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        path = _DATA_PATH
        if not path.exists():
            logger.warning("unified_locations.json not found at %s", path)
            self._loaded = True
            return

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.error("Failed to read unified_locations.json", exc_info=True)
            self._loaded = True
            return

        for entry in raw:
            uid = entry.get("unified_location_id")
            if uid is None:
                continue

            # Add id and provider_count if missing (match Location schema)
            if "id" not in entry:
                entry["id"] = f"loc_{uid}"
            if "provider_count" not in entry:
                entry["provider_count"] = len(entry.get("providers") or [])
            if "country_code" not in entry:
                entry["country_code"] = ""

            self._locations.append(entry)
            self._by_unified_id[uid] = entry

            for provider in entry.get("providers") or []:
                key = f"{provider['provider']}:{provider['pickup_id']}"
                self._by_provider[key] = entry

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

    def reload(self) -> int:
        """Force reload from disk. Returns new location count."""
        self._locations = []
        self._by_unified_id = {}
        self._by_provider = {}
        self._loaded = False
        self._ensure_loaded()
        return len(self._locations)
