"""Refresh unified_locations.json directly from provider adapters."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.adapters.registry import get_all_adapters, get_public_supplier_id
from app.services.location_sync_service import LocationSyncService
from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)


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
        self.sync_service.export_unified_json(unified_locations, output_path=self.output_path)
        summary["unified_locations"] = len(unified_locations)
        return summary

    def _provider_timeout_seconds(self, adapter) -> float:
        default_timeout = getattr(adapter, "default_timeout", 30.0)

        try:
            default_timeout = float(default_timeout)
        except (TypeError, ValueError):
            default_timeout = 30.0

        # Give location refresh a small cushion over the adapter timeout,
        # but keep individual providers bounded so one slow supplier cannot
        # stall or kill the entire unified JSON refresh.
        return max(15.0, min(default_timeout + 10.0, 60.0))
