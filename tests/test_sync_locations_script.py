import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.scripts.sync_locations import main


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class SyncLocationsScriptTest(unittest.IsolatedAsyncioTestCase):
    async def test_main_loads_configs_runs_sync_and_closes_db(self) -> None:
        session = object()
        factory = MagicMock(return_value=FakeSessionContext(session))
        service_instance = MagicMock()
        service_instance.sync_locations = AsyncMock(return_value={"providers_succeeded": 2})

        with patch('app.scripts.sync_locations.load_supplier_configs') as load_configs, \
             patch('app.scripts.sync_locations.get_session_factory', return_value=factory), \
             patch('app.scripts.sync_locations.LocationSyncService', return_value=service_instance), \
             patch('app.scripts.sync_locations.close_db', new=AsyncMock()) as close_db:
            await main()

        load_configs.assert_called_once()
        factory.assert_called_once()
        service_instance.sync_locations.assert_awaited_once_with(session)
        close_db.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
