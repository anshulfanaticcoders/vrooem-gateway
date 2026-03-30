import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.search import VehicleSearchBody, _do_search
from app.api.v1.locations import location_status
from app.services.circuit_breaker import CircuitBreakerRegistry
from app.schemas.search import ProviderFailure, SearchResponse


class SearchApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_do_search_uses_json_location_repository_when_provider_locations_missing(self) -> None:
        body = VehicleSearchBody(
            unified_location_id=4112884149,
            pickup_date='2026-05-21',
            dropoff_date='2026-05-24',
            pickup_time='09:00',
            dropoff_time='09:00',
            currency='EUR',
            driver_age=35,
        )
        fake_location = {
            'unified_location_id': 4112884149,
            'country': 'Morocco',
            'providers': [
                {
                    'provider': 'surprice',
                    'pickup_id': 'CMN:CMNA01',
                    'original_name': 'Casablanca Airport',
                }
            ],
        }
        fake_response = MagicMock()
        fake_cache = MagicMock()

        with patch('app.api.v1.search._json_location_repository.get_location_by_unified_id', return_value=fake_location, create=True) as get_location, \
             patch('app.api.v1.search.get_redis', new=AsyncMock(return_value=object())), \
             patch('app.api.v1.search.CacheService', return_value=fake_cache), \
             patch('app.api.v1.search.search_vehicles', new=AsyncMock(return_value=fake_response)) as search_vehicles, \
             patch('app.api.v1.search._cb_registry', CircuitBreakerRegistry()):
            result = await _do_search(body)

        get_location.assert_called_once_with(4112884149)
        self.assertIs(result, fake_response)
        search_vehicles.assert_awaited_once()
        request = search_vehicles.await_args.kwargs['request']
        provider_entries = search_vehicles.await_args.kwargs['provider_entries']
        self.assertEqual(request.country_code, 'MA')
        self.assertEqual(provider_entries[0]['pickup_id'], 'CMN:CMNA01')

    async def test_vehicle_search_payload_includes_structured_provider_status(self) -> None:
        from app.api.v1.search import build_search_vehicle_response

        response = SearchResponse(
            search_id="search_123",
            provider_status=[
                ProviderFailure(
                    provider="surprice",
                    stage="vehicle_search",
                    failure_type="provider_error",
                    http_status=422,
                    provider_code="225",
                    message="One way rentals not allowed to this location",
                    retryable=False,
                    raw_excerpt='{"code":"225"}',
                )
            ],
        )

        payload = build_search_vehicle_response(response)

        self.assertEqual(len(payload.provider_status), 1)
        self.assertEqual(payload.provider_status[0].provider, "surprice")
        self.assertEqual(payload.provider_status[0].failure_type, "provider_error")
        self.assertEqual(payload.provider_status[0].http_status, 422)

    async def test_location_status_exposes_loaded_location_metadata(self) -> None:
        fake_metadata = {
            "location_count": 1666,
            "location_data_loaded_at": "2026-03-30T12:00:00+00:00",
            "location_data_version": "abc123",
            "location_data_mtime": 1711780800.0,
            "location_data_size": 1024,
            "location_data_path": "/app/data/unified_locations.json",
        }

        with patch("app.api.v1.locations._repository.metadata", return_value=fake_metadata):
            payload = await location_status(_api_key="dev-key")

        self.assertEqual(payload["location_count"], 1666)
        self.assertEqual(payload["location_data_version"], "abc123")
        self.assertEqual(payload["location_data_path"], "/app/data/unified_locations.json")


if __name__ == '__main__':
    unittest.main()
