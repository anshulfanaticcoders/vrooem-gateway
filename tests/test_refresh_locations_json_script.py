import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.scripts.refresh_locations_json import main


class RefreshLocationsJsonScriptTest(unittest.IsolatedAsyncioTestCase):
    async def test_main_loads_configs_refreshes_json_and_prints_summary(self) -> None:
        service_instance = MagicMock()
        service_instance.refresh = AsyncMock(return_value={'providers_succeeded': 2})

        with patch('app.scripts.refresh_locations_json.load_supplier_configs') as load_configs, \
             patch('app.scripts.refresh_locations_json.LocationJsonRefreshService', return_value=service_instance), \
             patch('builtins.print') as print_mock:
            result = await main()

        load_configs.assert_called_once()
        service_instance.refresh.assert_awaited_once_with()
        print_mock.assert_called_once()
        self.assertEqual(result, {'providers_succeeded': 2})


if __name__ == '__main__':
    unittest.main()
