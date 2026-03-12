"""Synchronize provider and internal locations into the gateway database."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_all_adapters
from app.db.models import LocationSyncRun, ProviderLocation, UnifiedLocation, UnifiedLocationMapping
from app.services.location_normalization import (
    canonicalize_city,
    canonicalize_country_code,
    canonicalize_location_type,
    coordinate_bucket,
    extract_iata_code,
    normalize_string,
)
from app.services.location_unification_service import LocationUnificationService

logger = logging.getLogger(__name__)


class LocationSyncService:
    def __init__(self, adapters: list | None = None) -> None:
        self.adapters = adapters
        self.unification_service = LocationUnificationService()

    async def sync_locations(self, db: AsyncSession) -> dict[str, int]:
        adapters = self.adapters if self.adapters is not None else get_all_adapters()
        summary = {
            "providers_succeeded": 0,
            "providers_failed": 0,
            "locations_received": 0,
        }

        for adapter in adapters:
            provider = getattr(adapter, "supplier_id", None) or "unknown"
            started_at = datetime.utcnow()

            try:
                raw_locations = await adapter.get_locations()
                received = len(raw_locations or [])
                summary["locations_received"] += received

                upserted, deactivated = await self.sync_provider_locations(db, provider, raw_locations or [])
                db.add(
                    LocationSyncRun(
                        provider=provider,
                        status="success",
                        locations_received=received,
                        locations_upserted=upserted,
                        locations_deactivated=deactivated,
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                    )
                )
                await db.commit()
                summary["providers_succeeded"] += 1
            except Exception as exc:
                logger.exception("[%s] location sync failed", provider)
                await db.rollback()
                if await self._record_failed_sync_run(db, provider, exc, started_at):
                    summary["providers_failed"] += 1
                else:
                    logger.exception("[%s] failed to persist location sync failure", provider)

        if summary["providers_succeeded"] > 0:
            await db.rollback()
            await self.rebuild_unified_locations_from_provider_rows(db)
            await db.commit()
        else:
            await db.rollback()

        return summary

    async def _record_failed_sync_run(
        self,
        db: AsyncSession,
        provider: str,
        exc: Exception,
        started_at: datetime,
    ) -> bool:
        try:
            db.add(
                LocationSyncRun(
                    provider=provider,
                    status="failed",
                    locations_received=0,
                    locations_upserted=0,
                    locations_deactivated=0,
                    error_message=str(exc),
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            return False

    async def sync_provider_locations(
        self,
        db: AsyncSession,
        provider: str,
        raw_locations: list[dict],
    ) -> tuple[int, int]:
        now = datetime.utcnow()
        normalized_locations = self._dedupe_provider_locations(provider, raw_locations)
        location_ids = list(normalized_locations.keys())

        existing_rows = (
            await db.execute(
                select(ProviderLocation).where(ProviderLocation.provider == provider)
            )
        ).scalars().all()
        existing_by_id = {row.provider_location_id: row for row in existing_rows}

        for provider_location_id, location in normalized_locations.items():
            row = existing_by_id.get(provider_location_id)
            payload_hash = self._payload_hash(location)

            if row is None:
                row = ProviderLocation(
                    provider=provider,
                    provider_location_id=provider_location_id,
                )
                db.add(row)

            row.name = location["name"]
            row.name_norm = normalize_string(location["name"])
            row.city = location["city"]
            row.city_norm = canonicalize_city(location["city"])
            row.country = location["country"]
            row.country_code = location["country_code"]
            row.country_norm = normalize_string(location["country"] or location["country_code"])
            row.latitude = location["latitude"]
            row.longitude = location["longitude"]
            row.location_type = location["location_type"]
            row.type_norm = canonicalize_location_type(location["location_type"], location["name"])
            row.iata = location.get("iata")
            row.iata_norm = (location.get("iata") or "").upper() or None
            row.geohash = coordinate_bucket(location["latitude"], location["longitude"])
            row.raw_data = location
            row.is_active = True
            row.last_synced_at = now
            row.last_seen_at = now
            row.sync_status = "active"
            row.provider_payload_hash = payload_hash

        deactivated = 0
        for row in existing_rows:
            if row.provider_location_id in location_ids:
                continue
            if row.is_active:
                deactivated += 1
            row.is_active = False
            row.last_synced_at = now
            row.sync_status = "inactive"

        await db.flush()
        return len(normalized_locations), deactivated

    async def rebuild_unified_locations_from_provider_rows(self, db: AsyncSession) -> None:
        rows = (
            await db.execute(
                select(ProviderLocation).where(ProviderLocation.is_active.is_(True))
            )
        ).scalars().all()

        raw_locations = []
        for row in rows:
            raw_data = row.raw_data or {}
            raw_locations.append(
                {
                    "provider": row.provider,
                    "provider_location_id": row.provider_location_id,
                    "name": row.name,
                    "city": row.city,
                    "country": row.country,
                    "country_code": row.country_code,
                    "latitude": row.latitude,
                    "longitude": row.longitude,
                    "location_type": row.location_type,
                    "iata": row.iata,
                    "dropoffs": raw_data.get("dropoffs") or [],
                    "supports_one_way": bool(raw_data.get("dropoffs") or raw_data.get("supports_one_way")),
                    "our_location_id": raw_data.get("our_location_id"),
                }
            )

        unified_locations = self.unification_service.build_unified_locations(raw_locations)

        await db.execute(delete(UnifiedLocationMapping))
        await db.execute(delete(UnifiedLocation))

        for unified in unified_locations:
            db.add(
                UnifiedLocation(
                    unified_location_id=unified["unified_location_id"],
                    match_key=unified["match_key"],
                    name=unified["name"],
                    aliases=unified["aliases"],
                    city=unified["city"],
                    country=unified["country"],
                    country_code=unified["country_code"],
                    latitude=unified["latitude"],
                    longitude=unified["longitude"],
                    location_type=unified["location_type"],
                    iata=unified.get("iata"),
                    confidence=unified.get("confidence", 1.0),
                    our_location_id=unified.get("our_location_id"),
                    is_active=True,
                )
            )

            for provider in unified["providers"]:
                db.add(
                    UnifiedLocationMapping(
                        unified_location_id=unified["unified_location_id"],
                        provider=provider["provider"],
                        provider_location_id=provider["pickup_id"],
                        original_name=provider.get("original_name", ""),
                        latitude=provider.get("latitude"),
                        longitude=provider.get("longitude"),
                        dropoffs=provider.get("dropoffs", []),
                        status="auto",
                    )
                )

        await db.flush()

    def _dedupe_provider_locations(self, provider: str, raw_locations: list[dict]) -> dict[str, dict]:
        normalized_locations: dict[str, dict] = {}

        for raw_location in raw_locations:
            normalized = self._normalize_location(provider, raw_location)
            if not normalized:
                continue
            normalized_locations[normalized["provider_location_id"]] = normalized

        return normalized_locations

    def _normalize_location(self, provider: str, raw_location: dict) -> dict | None:
        provider_location_id = str(raw_location.get("provider_location_id") or "").strip()
        if not provider_location_id:
            return None

        country = str(raw_location.get("country") or "").strip()
        country_code = canonicalize_country_code(raw_location.get("country_code"), country)
        name = str(raw_location.get("name") or raw_location.get("city") or provider_location_id).strip()
        city = str(raw_location.get("city") or name).strip()
        location_type = canonicalize_location_type(raw_location.get("location_type"), name)
        iata = (raw_location.get("iata") or extract_iata_code(raw_location) or "").strip().upper() or None
        latitude = _safe_float(raw_location.get("latitude"))
        longitude = _safe_float(raw_location.get("longitude"))
        our_location_id = raw_location.get("our_location_id")
        if provider == "internal" and not our_location_id:
            our_location_id = provider_location_id

        normalized = {
            "provider": provider,
            "provider_location_id": provider_location_id,
            "name": name,
            "city": city,
            "country": country or country_code,
            "country_code": country_code,
            "latitude": latitude,
            "longitude": longitude,
            "location_type": location_type,
            "iata": iata,
            "dropoffs": list(raw_location.get("dropoffs") or []),
            "supports_one_way": bool(raw_location.get("dropoffs") or raw_location.get("supports_one_way")),
            "our_location_id": our_location_id,
        }

        return normalized

    def _payload_hash(self, payload: dict) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
