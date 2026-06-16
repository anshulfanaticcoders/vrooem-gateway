"""Refresh unified_locations.json directly from provider adapters."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.adapters.registry import get_all_adapters, get_public_supplier_id, get_supplier_config
from app.core.config import get_settings
from app.services.location_normalization import canonicalize_country_code
from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)


class LocationJsonRefreshService:
    def __init__(self, adapters: list | None = None, output_path: str | Path | None = None) -> None:
        self.adapters = adapters
        self.output_path = Path(output_path) if output_path else None
        self.unification_service = LocationUnificationService()

    async def refresh(self) -> dict[str, object]:
        adapters = self.adapters if self.adapters is not None else get_all_adapters()
        summary = {
            "providers_succeeded": 0,
            "providers_failed": 0,
            "providers_succeeded_ids": [],
            "providers_failed_ids": [],
            "locations_received": 0,
            "unified_locations": 0,
            "internal_provider_succeeded": False,
            "internal_provider_failed": False,
            "internal_locations_received": 0,
            "locations_filtered_by_country_scope": 0,
            "providers_country_filtered_ids": [],
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
                filtered_locations = self._filter_locations_for_supplier_country_scope(provider, locations)
                filtered_out = len(locations) - len(filtered_locations)
                if filtered_out:
                    summary["locations_filtered_by_country_scope"] += filtered_out
                    if public_provider not in summary["providers_country_filtered_ids"]:
                        summary["providers_country_filtered_ids"].append(public_provider)
                    logger.warning(
                        "[%s] Filtered %d location(s) outside configured country scope",
                        provider,
                        filtered_out,
                    )
                summary["providers_succeeded"] += 1
                summary["providers_succeeded_ids"].append(public_provider)
                summary["locations_received"] += len(locations)
                if public_provider == "internal":
                    summary["internal_provider_succeeded"] = True
                    summary["internal_locations_received"] = len(filtered_locations)
                raw_locations.extend({**location, "provider": public_provider} for location in filtered_locations)
            except Exception:
                logger.exception("[%s] location JSON refresh failed", provider)
                summary["providers_failed"] += 1
                summary["providers_failed_ids"].append(public_provider)
                if public_provider == "internal":
                    summary["internal_provider_failed"] = True

        unified_locations = self.unification_service.build_unified_locations(raw_locations)
        self._export_unified_json(unified_locations)
        summary["unified_locations"] = len(unified_locations)
        summary["status"] = "completed_with_failures" if summary["providers_failed"] else "completed"
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

    def _filter_locations_for_supplier_country_scope(self, provider: str, locations: list[dict]) -> list[dict]:
        config = get_supplier_config(provider)
        allowed_countries = {
            canonicalize_country_code(country, country).upper()
            for country in (config.get("countries") or [])
            if str(country or "").strip()
        }

        if not allowed_countries:
            return locations

        filtered: list[dict] = []
        for location in locations:
            country_code = canonicalize_country_code(
                location.get("country_code"),
                location.get("country"),
            ).upper()
            if country_code in allowed_countries:
                filtered.append(location)

        return filtered
