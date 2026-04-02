"""Synchronize provider and internal locations into the gateway database."""

from __future__ import annotations

import asyncio
import json

from app.adapters.registry import load_supplier_configs

# Import adapters so they register before get_all_adapters() is called.
import app.adapters.adobe_car  # noqa: F401
import app.adapters.favrica  # noqa: F401
import app.adapters.green_motion  # noqa: F401
import app.adapters.internal  # noqa: F401
import app.adapters.locauto_rent  # noqa: F401
import app.adapters.ok_mobility  # noqa: F401
import app.adapters.recordgo  # noqa: F401
import app.adapters.renteon  # noqa: F401
import app.adapters.sicily_by_car  # noqa: F401
import app.adapters.surprice  # noqa: F401
import app.adapters.usave  # noqa: F401
import app.adapters.wheelsys  # noqa: F401
import app.adapters.xdrive  # noqa: F401
import app.adapters.emr  # noqa: F401
import app.adapters.click2rent  # noqa: F401
from app.db.session import close_db, get_session_factory
from app.services.location_sync_service import LocationSyncService


async def main() -> dict[str, int]:
    load_supplier_configs()

    factory = get_session_factory()
    service = LocationSyncService()

    async with factory() as session:
        summary = await service.sync_locations(session)

    await close_db()
    print(json.dumps(summary, sort_keys=True))
    return summary


if __name__ == '__main__':
    asyncio.run(main())
