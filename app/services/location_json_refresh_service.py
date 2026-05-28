"""Refresh unified_locations.json directly from provider adapters."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.adapters.registry import get_all_adapters, get_public_supplier_id
from app.core.config import get_settings
from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)


class LocationJsonRefreshService:
    def __init__(self, adapters: list | None = None, output_path: str | Path | None = None) -> None:
        self.adapters = adapters
        self.output_path = Path(output_path) if output_path else None
        self.unification_service = LocationUnificationService()

    async def refresh(self) -> dict[str, int]:
        adapters = self.adapters if self.adapters is not None else get_all_adapters()
        summary = {
            "providers_succeeded": 0,
            "providers_failed": 0,
            "locations_received": 0,
            "unified_locations": 0,
        }
        raw_locations: list[dict] = []

        for adapter in adapters:
            provider = getattr(adapter, "supplier_id", None) or "unknown"
            public_provider = get_public_supplier_id(provider)
            try:
                logger.info("[%s] Fetching locations...", provider)
                locations = await asyncio.wait_for(
                    adapter.get_locations(),
                    timeout=self._provider_timeout_seconds(adapter),
                ) or []
                logger.info("[%s] Got %d locations", provider, len(locations))
                summary["providers_succeeded"] += 1
                summary["locations_received"] += len(locations)
                raw_locations.extend({**location, "provider": public_provider} for location in locations)
            except Exception:
                logger.exception("[%s] location JSON refresh failed", provider)
                summary["providers_failed"] += 1

        unified_locations = self.unification_service.build_unified_locations(raw_locations)
        self._export_unified_json(unified_locations)
        summary["unified_locations"] = len(unified_locations)
        return summary

    def _export_unified_json(self, unified_locations: list[dict]) -> Path:
        json_path = self.output_path or (
            Path(__file__).resolve().parent.parent.parent / "data" / "unified_locations.json"
        )
        json_path.parent.mkdir(parents=True, exist_ok=True)
        exportable = []
        for loc in unified_locations:
            exportable.append({
                "unified_location_id": loc["unified_location_id"],
                "name": loc["name"],
                "aliases": loc["aliases"],
                "city": loc["city"],
                "country": loc["country"],
                "country_code": loc.get("country_code", ""),
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "location_type": loc["location_type"],
                "iata": loc.get("iata"),
                "provider_count": loc.get("provider_count", len(loc.get("providers") or [])),
                "providers": [
                    {
                        "provider": get_public_supplier_id(p["provider"]),
                        "pickup_id": p["pickup_id"],
                        "original_name": p.get("original_name", ""),
                        "dropoffs": p.get("dropoffs", []),
                        "latitude": p.get("latitude"),
                        "longitude": p.get("longitude"),
                        "supports_one_way": bool(p.get("supports_one_way")),
                        "extended_location_code": p.get("extended_location_code"),
                        "extended_dropoff_code": p.get("extended_dropoff_code"),
                        "country_code": p.get("country_code"),
                        "iata": p.get("iata"),
                        "provider_code": p.get("provider_code"),
                    }
                    for p in loc["providers"]
                ],
                "our_location_id": loc.get("our_location_id"),
            })

        tmp_path = json_path.with_suffix(json_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(exportable, indent=4, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(json_path)
        logger.info("Exported %d unified locations to %s", len(exportable), json_path)
        return json_path

    def _provider_timeout_seconds(self, adapter) -> float:
        timeout = getattr(adapter, "location_refresh_timeout_seconds", None)
        if timeout is None:
            timeout = get_settings().location_refresh_provider_timeout_seconds

        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 180.0

        return max(15.0, timeout)
