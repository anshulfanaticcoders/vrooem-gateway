"""Refresh unified_locations.json directly from provider adapters."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from app.adapters.registry import get_all_adapters, get_public_supplier_id
from app.services.location_sync_service import LocationSyncService
from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)

_STICKY_PROVIDERS = {"greenmotion", "usave", "internal"}


class LocationJsonRefreshService:
    def __init__(self, adapters: list | None = None, output_path: str | Path | None = None) -> None:
        self.adapters = adapters
        self.output_path = Path(output_path) if output_path else None
        self.unification_service = LocationUnificationService()
        self.sync_service = LocationSyncService(adapters=[])

    async def refresh(self) -> dict[str, int]:
        adapters = self.adapters if self.adapters is not None else get_all_adapters()
        summary = {
            "providers_succeeded": 0,
            "providers_failed": 0,
            "locations_received": 0,
            "unified_locations": 0,
        }
        raw_locations: list[dict] = []
        existing_locations = self._load_existing_locations_by_provider()

        for adapter in adapters:
            provider = getattr(adapter, "supplier_id", None) or "unknown"
            public_provider = get_public_supplier_id(provider)
            try:
                logger.info("[%s] Fetching locations...", provider)
                locations = await adapter.get_locations() or []
                logger.info("[%s] Got %d locations", provider, len(locations))
                summary["providers_succeeded"] += 1
                summary["locations_received"] += len(locations)
                fresh_locations = []
                for location in locations:
                    fresh_locations.append({**location, "provider": public_provider})

                if public_provider in _STICKY_PROVIDERS:
                    raw_locations.extend(self._merge_provider_locations(existing_locations.get(public_provider, []), fresh_locations))
                else:
                    raw_locations.extend(fresh_locations)
            except Exception:
                logger.exception("[%s] location JSON refresh failed", provider)
                summary["providers_failed"] += 1
                raw_locations.extend(existing_locations.get(public_provider, []))

        unified_locations = self.unification_service.build_unified_locations(raw_locations)
        self.sync_service.export_unified_json(unified_locations, output_path=self.output_path)
        summary["unified_locations"] = len(unified_locations)
        return summary

    def _load_existing_locations_by_provider(self) -> dict[str, list[dict]]:
        path = self.output_path or (Path(__file__).resolve().parent.parent.parent / "data" / "unified_locations.json")
        if not path.exists():
            return {}

        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read existing unified locations JSON from %s", path)
            return {}

        rows: dict[str, list[dict]] = defaultdict(list)
        for location in existing:
            for provider in location.get("providers") or []:
                rows[provider.get("provider", "")].append({
                    "provider": provider.get("provider"),
                    "provider_location_id": provider.get("pickup_id"),
                    "name": provider.get("original_name") or location.get("name"),
                    "city": location.get("city"),
                    "country": location.get("country"),
                    "country_code": location.get("country_code"),
                    "latitude": provider.get("latitude") if provider.get("latitude") is not None else location.get("latitude"),
                    "longitude": provider.get("longitude") if provider.get("longitude") is not None else location.get("longitude"),
                    "location_type": location.get("location_type"),
                    "iata": location.get("iata"),
                    "dropoffs": provider.get("dropoffs") or [],
                    "supports_one_way": bool(provider.get("dropoffs")),
                })

        return rows

    def _merge_provider_locations(self, existing_locations: list[dict], fresh_locations: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for location in existing_locations:
            provider_location_id = str(location.get("provider_location_id") or "").strip()
            if provider_location_id:
                merged[provider_location_id] = location

        for location in fresh_locations:
            provider_location_id = str(location.get("provider_location_id") or "").strip()
            if provider_location_id:
                merged[provider_location_id] = location

        return list(merged.values())
