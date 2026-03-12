"""Database-backed location repository."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderLocation, UnifiedLocation, UnifiedLocationMapping
from app.services.location_sync_service import LocationSyncService
from app.services.location_unification_service import LocationUnificationService


class LocationRepository:
    def __init__(self) -> None:
        self.unification_service = LocationUnificationService()
        self.sync_service = LocationSyncService()

    async def ensure_initialized(self, db: AsyncSession) -> None:
        count = await db.scalar(select(func.count()).select_from(UnifiedLocation))
        if count:
            return

        provider_count = await db.scalar(
            select(func.count()).select_from(ProviderLocation).where(ProviderLocation.is_active.is_(True))
        )
        if provider_count:
            await self.sync_service.rebuild_unified_locations_from_provider_rows(db)
            await db.commit()

    async def search_locations(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 20,
        include_internal_provider: bool = False,
    ) -> list[dict]:
        locations = await self.list_locations(db, include_internal_provider=include_internal_provider)
        return self.unification_service.search_locations(locations, query, limit)

    async def list_locations(self, db: AsyncSession, include_internal_provider: bool = False) -> list[dict]:
        await self.ensure_initialized(db)

        unified_rows = (
            await db.execute(
                select(UnifiedLocation).where(UnifiedLocation.is_active.is_(True)).order_by(UnifiedLocation.name.asc())
            )
        ).scalars().all()

        return await self._attach_mappings(db, unified_rows, include_internal_provider=include_internal_provider)

    async def get_location_by_unified_id(
        self,
        db: AsyncSession,
        unified_location_id: int,
        include_internal_provider: bool = False,
    ) -> dict | None:
        await self.ensure_initialized(db)

        row = (
            await db.execute(
                select(UnifiedLocation).where(
                    UnifiedLocation.unified_location_id == unified_location_id,
                    UnifiedLocation.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

        if row is None:
            return None

        locations = await self._attach_mappings(
            db,
            [row],
            include_internal_provider=include_internal_provider,
        )
        return locations[0] if locations else None

    async def get_location_by_provider_id(
        self,
        db: AsyncSession,
        provider: str,
        provider_location_id: str,
        include_internal_provider: bool = False,
    ) -> dict | None:
        await self.ensure_initialized(db)

        mapping = (
            await db.execute(
                select(UnifiedLocationMapping).where(
                    UnifiedLocationMapping.provider == provider,
                    UnifiedLocationMapping.provider_location_id == provider_location_id,
                )
            )
        ).scalar_one_or_none()

        if mapping is None:
            return None

        return await self.get_location_by_unified_id(
            db,
            mapping.unified_location_id,
            include_internal_provider=include_internal_provider,
        )

    async def _attach_mappings(
        self,
        db: AsyncSession,
        unified_rows: list[UnifiedLocation],
        include_internal_provider: bool = False,
    ) -> list[dict]:
        if not unified_rows:
            return []

        unified_ids = [row.unified_location_id for row in unified_rows]
        mapping_rows = (
            await db.execute(
                select(UnifiedLocationMapping).where(
                    UnifiedLocationMapping.unified_location_id.in_(unified_ids)
                )
            )
        ).scalars().all()

        mappings_by_unified: dict[int, list[UnifiedLocationMapping]] = {}
        for mapping in mapping_rows:
            mappings_by_unified.setdefault(mapping.unified_location_id, []).append(mapping)

        results = []
        for row in unified_rows:
            providers = []
            for mapping in mappings_by_unified.get(row.unified_location_id, []):
                if mapping.provider == "internal" and not include_internal_provider:
                    continue

                providers.append(
                    {
                        "provider": mapping.provider,
                        "pickup_id": mapping.provider_location_id,
                        "original_name": mapping.original_name or "",
                        "dropoffs": mapping.dropoffs or [],
                        "latitude": mapping.latitude,
                        "longitude": mapping.longitude,
                        "supports_one_way": bool(mapping.dropoffs),
                    }
                )

            results.append(
                {
                    "id": f"loc_{row.unified_location_id}",
                    "unified_location_id": row.unified_location_id,
                    "name": row.name,
                    "aliases": row.aliases or [],
                    "city": row.city,
                    "country": row.country,
                    "country_code": row.country_code or "",
                    "latitude": row.latitude,
                    "longitude": row.longitude,
                    "location_type": row.location_type,
                    "iata": row.iata,
                    "providers": providers,
                    "provider_count": len(providers),
                    "our_location_id": row.our_location_id,
                }
            )

        return results
