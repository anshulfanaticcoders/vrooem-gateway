import unittest
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, LocationSyncRun, ProviderLocation, UnifiedLocation, UnifiedLocationMapping
from app.services.location_sync_service import LocationSyncService


class FakeAdapter:
    def __init__(self, supplier_id: str, locations: list[dict]):
        self.supplier_id = supplier_id
        self._locations = locations

    async def get_locations(self) -> list[dict]:
        return self._locations


class FailingAdapter:
    def __init__(self, supplier_id: str, error: Exception):
        self.supplier_id = supplier_id
        self._error = error

    async def get_locations(self) -> list[dict]:
        raise self._error


class DatabaseFailingLocationSyncService(LocationSyncService):
    async def sync_provider_locations(self, db, provider: str, raw_locations: list[dict]):
        if provider == 'sicily_by_car':
            await db.execute(text('SELECT * FROM definitely_missing_table'))
        return await super().sync_provider_locations(db, provider, raw_locations)


class LocationSyncServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.schema = f"test_location_sync_{uuid.uuid4().hex[:8]}"
        self.engine = create_async_engine('postgresql+asyncpg://postgres:postgres@db:5432/vrooem_gateway')

        async with self.engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'))
            await conn.execute(text(f'SET search_path TO "{self.schema}"'))
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.engine.dispose()

    async def _open_session(self) -> AsyncSession:
        session = self.session_factory()
        await session.execute(text(f'SET search_path TO "{self.schema}"'))
        return session

    async def test_sync_locations_upserts_provider_rows_records_runs_and_builds_unified_rows(self) -> None:
        service = LocationSyncService(
            adapters=[
                FakeAdapter(
                    'green_motion',
                    [
                        {
                            'provider': 'green_motion',
                            'provider_location_id': '359',
                            'name': 'Marrakech Airport',
                            'city': 'Marrakech',
                            'country': 'Morocco',
                            'country_code': 'MA',
                            'location_type': 'airport',
                            'latitude': 31.600026,
                            'longitude': -8.024344,
                            'iata': 'RAK',
                        }
                    ],
                ),
                FakeAdapter(
                    'xdrive',
                    [
                        {
                            'provider': 'xdrive',
                            'provider_location_id': '21',
                            'name': 'Marrakech-Menara Airport (RAK)',
                            'city': 'Marrakesh',
                            'country': 'MA',
                            'country_code': 'MA',
                            'location_type': 'airport',
                            'latitude': 31.6069,
                            'longitude': -8.0363,
                        }
                    ],
                ),
            ]
        )

        async with await self._open_session() as session:
            summary = await service.sync_locations(session)

            provider_count = await session.scalar(select(func.count()).select_from(ProviderLocation))
            unified_count = await session.scalar(select(func.count()).select_from(UnifiedLocation))
            mapping_count = await session.scalar(select(func.count()).select_from(UnifiedLocationMapping))
            run_count = await session.scalar(select(func.count()).select_from(LocationSyncRun))
            provider_rows = (await session.execute(select(ProviderLocation).order_by(ProviderLocation.provider))).scalars().all()

        self.assertEqual(summary['providers_succeeded'], 2)
        self.assertEqual(summary['providers_failed'], 0)
        self.assertEqual(provider_count, 2)
        self.assertEqual(unified_count, 1)
        self.assertEqual(mapping_count, 2)
        self.assertEqual(run_count, 2)
        self.assertTrue(all(row.last_synced_at is not None for row in provider_rows))
        self.assertTrue(all(row.last_seen_at is not None for row in provider_rows))
        self.assertTrue(all(row.sync_status == 'active' for row in provider_rows))

    async def test_sync_locations_marks_missing_rows_inactive_after_successful_provider_refresh(self) -> None:
        service = LocationSyncService(
            adapters=[
                FakeAdapter(
                    'surprice',
                    [
                        {
                            'provider': 'surprice',
                            'provider_location_id': 'DXB:DXB',
                            'name': 'Dubai Airport',
                            'city': 'Dubai',
                            'country': 'United Arab Emirates',
                            'country_code': 'AE',
                            'location_type': 'airport',
                            'latitude': 25.2532,
                            'longitude': 55.3657,
                            'iata': 'DXB',
                        }
                    ],
                )
            ]
        )

        async with await self._open_session() as session:
            await service.sync_locations(session)

        service = LocationSyncService(adapters=[FakeAdapter('surprice', [])])

        async with await self._open_session() as session:
            await service.sync_locations(session)
            row = (
                await session.execute(
                    select(ProviderLocation).where(
                        ProviderLocation.provider == 'surprice',
                        ProviderLocation.provider_location_id == 'DXB:DXB',
                    )
                )
            ).scalar_one()
            unified_count = await session.scalar(select(func.count()).select_from(UnifiedLocation))
            latest_run = (
                await session.execute(
                    select(LocationSyncRun)
                    .where(LocationSyncRun.provider == 'surprice')
                    .order_by(LocationSyncRun.started_at.desc())
                )
            ).scalars().first()

        self.assertFalse(row.is_active)
        self.assertEqual(row.sync_status, 'inactive')
        self.assertEqual(unified_count, 0)
        self.assertEqual(latest_run.status, 'success')
        self.assertEqual(latest_run.locations_received, 0)



    async def test_sync_locations_keeps_successful_provider_rows_when_a_later_provider_fails(self) -> None:
        service = LocationSyncService(
            adapters=[
                FakeAdapter(
                    'green_motion',
                    [
                        {
                            'provider': 'green_motion',
                            'provider_location_id': '359',
                            'name': 'Marrakech Airport',
                            'city': 'Marrakech',
                            'country': 'Morocco',
                            'country_code': 'MA',
                            'location_type': 'airport',
                            'latitude': 31.600026,
                            'longitude': -8.024344,
                            'iata': 'RAK',
                        }
                    ],
                ),
                FailingAdapter('sicily_by_car', RuntimeError('provider timeout')),
            ]
        )

        async with await self._open_session() as session:
            summary = await service.sync_locations(session)
            provider_count = await session.scalar(select(func.count()).select_from(ProviderLocation))
            unified_count = await session.scalar(select(func.count()).select_from(UnifiedLocation))
            run_rows = (
                await session.execute(
                    select(LocationSyncRun).order_by(LocationSyncRun.provider.asc(), LocationSyncRun.started_at.asc())
                )
            ).scalars().all()

        self.assertEqual(summary['providers_succeeded'], 1)
        self.assertEqual(summary['providers_failed'], 1)
        self.assertEqual(provider_count, 1)
        self.assertEqual(unified_count, 1)
        self.assertEqual([row.provider for row in run_rows], ['green_motion', 'sicily_by_car'])
        self.assertEqual([row.status for row in run_rows], ['success', 'failed'])



    async def test_sync_locations_keeps_successful_rows_when_a_later_provider_hits_a_db_error(self) -> None:
        service = DatabaseFailingLocationSyncService(
            adapters=[
                FakeAdapter(
                    'green_motion',
                    [
                        {
                            'provider': 'green_motion',
                            'provider_location_id': '359',
                            'name': 'Marrakech Airport',
                            'city': 'Marrakech',
                            'country': 'Morocco',
                            'country_code': 'MA',
                            'location_type': 'airport',
                            'latitude': 31.600026,
                            'longitude': -8.024344,
                            'iata': 'RAK',
                        }
                    ],
                ),
                FakeAdapter(
                    'sicily_by_car',
                    [
                        {
                            'provider': 'sicily_by_car',
                            'provider_location_id': 'PSA',
                            'name': 'Pisa Airport',
                            'city': 'Pisa',
                            'country': 'Italy',
                            'country_code': 'IT',
                            'location_type': 'airport',
                            'latitude': 43.6987,
                            'longitude': 10.4004,
                            'iata': 'PSA',
                        }
                    ],
                ),
            ]
        )

        async with await self._open_session() as session:
            summary = await service.sync_locations(session)
            provider_count = await session.scalar(select(func.count()).select_from(ProviderLocation))
            unified_count = await session.scalar(select(func.count()).select_from(UnifiedLocation))
            run_rows = (
                await session.execute(
                    select(LocationSyncRun).order_by(LocationSyncRun.provider.asc(), LocationSyncRun.started_at.asc())
                )
            ).scalars().all()

        self.assertEqual(summary['providers_succeeded'], 1)
        self.assertEqual(summary['providers_failed'], 1)
        self.assertEqual(provider_count, 1)
        self.assertEqual(unified_count, 1)
        self.assertEqual([row.provider for row in run_rows], ['green_motion', 'sicily_by_car'])
        self.assertEqual([row.status for row in run_rows], ['success', 'failed'])


if __name__ == '__main__':
    unittest.main()
