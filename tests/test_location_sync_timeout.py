import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.location_sync_service import LocationSyncService


class LocationSyncTimeoutTest(unittest.TestCase):
    def test_provider_timeout_uses_configured_location_refresh_window(self) -> None:
        service = LocationSyncService(adapters=[])

        with patch('app.services.location_sync_service.get_settings') as get_settings:
            get_settings.return_value.location_refresh_provider_timeout_seconds = 180.0

            self.assertEqual(service._provider_timeout_seconds(SimpleNamespace()), 180.0)

    def test_provider_timeout_allows_adapter_specific_override(self) -> None:
        service = LocationSyncService(adapters=[])
        adapter = SimpleNamespace(location_refresh_timeout_seconds=240.0)

        self.assertEqual(service._provider_timeout_seconds(adapter), 240.0)


if __name__ == '__main__':
    unittest.main()
