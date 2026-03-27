import unittest
from datetime import date, time
from unittest.mock import MagicMock, patch

from app.schemas.search import SearchRequest
from app.services.search_service import search_vehicles


class _FakeAdapter:
    supplier_id = "surprice"
    supports_one_way = True

    def __init__(self) -> None:
        self.last_pickup_entry = None
        self.last_dropoff_entry = None

    async def search_vehicles(self, request, pickup_entry, dropoff_entry):
        self.last_pickup_entry = pickup_entry
        self.last_dropoff_entry = dropoff_entry
        return []


class _RecordingCache:
    def __init__(self) -> None:
        self.get_search_calls = []

    async def get_search(self, **kwargs):
        self.get_search_calls.append(kwargs)
        return None

    async def set_vehicle(self, vehicle_id, data):
        return None

    async def set_search(self, data, **kwargs):
        return None


class _FakeCircuitBreaker:
    is_available = True

    def record_success(self) -> None:
        return None

    def record_failure(self) -> None:
        return None


class _FakeCircuitBreakerRegistry:
    def get(self, supplier_id: str):
        return _FakeCircuitBreaker()


class SearchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_surprice_extended_location_code_when_laravel_passes_provider_locations(self) -> None:
        adapter = _FakeAdapter()
        request = SearchRequest(
            unified_location_id=2929145933,
            pickup_date=date(2026, 6, 25),
            pickup_time=time(9, 0),
            dropoff_date=date(2026, 6, 28),
            dropoff_time=time(9, 0),
            currency="EUR",
            driver_age=35,
            country_code="GR",
        )

        provider_entries = [
            {
                "provider": "surprice",
                "pickup_id": "MLO",
                "original_name": "Milos Airport",
                "latitude": 36.698053,
                "longitude": 24.469153,
                "extended_location_code": "MLOA01",
                "country_code": "GR",
                "iata": "MLO",
            }
        ]

        with patch("app.services.search_service.get_adapter", return_value=adapter):
            await search_vehicles(
                request=request,
                provider_entries=provider_entries,
                cache=_RecordingCache(),
                cb_registry=_FakeCircuitBreakerRegistry(),
            )

        self.assertIsNotNone(adapter.last_pickup_entry)
        self.assertEqual(adapter.last_pickup_entry.pickup_id, "MLO")
        self.assertEqual(getattr(adapter.last_pickup_entry, "extended_location_code", None), "MLOA01")

    async def test_resolves_provider_specific_dropoff_entry_from_dropoff_unified_location(self) -> None:
        adapter = _FakeAdapter()
        request = SearchRequest(
            unified_location_id=111,
            pickup_date=date(2026, 6, 25),
            pickup_time=time(9, 0),
            dropoff_date=date(2026, 6, 28),
            dropoff_time=time(9, 0),
            dropoff_unified_location_id=222,
            currency="EUR",
            driver_age=35,
            country_code="MA",
        )

        provider_entries = [
            {
                "provider": "surprice",
                "pickup_id": "CMN:CMNA01",
                "original_name": "Casablanca Airport",
                "dropoffs": ["CMNC01:CMNC01"],
                "supports_one_way": True,
                "extended_location_code": "CMNA01",
                "extended_dropoff_code": "CMNC01",
            }
        ]

        dropoff_location = {
            "unified_location_id": 222,
            "providers": [
                {
                    "provider": "surprice",
                    "pickup_id": "CMNC01:CMNC01",
                    "original_name": "Casablanca Downtown",
                    "extended_location_code": "CMNC01",
                    "extended_dropoff_code": "CMNC01",
                }
            ],
        }

        dropoff_repository = MagicMock()
        dropoff_repository.get_location_by_unified_id.return_value = dropoff_location

        with patch("app.services.search_service.get_adapter", return_value=adapter), \
             patch("app.services.search_service._json_location_repository", new=dropoff_repository, create=True):
            await search_vehicles(
                request=request,
                provider_entries=provider_entries,
                cache=_RecordingCache(),
                cb_registry=_FakeCircuitBreakerRegistry(),
            )

        self.assertIsNotNone(adapter.last_dropoff_entry)
        self.assertEqual(adapter.last_dropoff_entry.pickup_id, "CMNC01:CMNC01")
        self.assertEqual(adapter.last_dropoff_entry.original_name, "Casablanca Downtown")
        self.assertEqual(adapter.last_dropoff_entry.extended_location_code, "CMNC01")

    async def test_cache_lookup_varies_when_provider_entries_change(self) -> None:
        request = SearchRequest(
            unified_location_id=1191543869,
            pickup_date=date(2026, 5, 21),
            pickup_time=time(9, 0),
            dropoff_date=date(2026, 5, 24),
            dropoff_time=time(9, 0),
            currency="EUR",
            driver_age=35,
        )
        cache = _RecordingCache()
        locauto_only_entries = [
            {"provider": "locauto_rent", "pickup_id": "FCO", "original_name": "Rome Fiumicino Airport"},
        ]
        mixed_entries = [
            {"provider": "greenmotion", "pickup_id": "157", "original_name": "Rome Fiumicino International Airport"},
            {"provider": "locauto_rent", "pickup_id": "FCO", "original_name": "Rome Fiumicino Airport"},
        ]

        with patch("app.services.search_service.get_adapter", return_value=_FakeAdapter()):
            await search_vehicles(
                request=request,
                provider_entries=locauto_only_entries,
                cache=cache,
                cb_registry=_FakeCircuitBreakerRegistry(),
            )
            await search_vehicles(
                request=request,
                provider_entries=mixed_entries,
                cache=cache,
                cb_registry=_FakeCircuitBreakerRegistry(),
            )

        self.assertEqual(len(cache.get_search_calls), 2)
        self.assertNotEqual(cache.get_search_calls[0], cache.get_search_calls[1])
        self.assertIn("ploc", cache.get_search_calls[0])
        self.assertIn("ploc", cache.get_search_calls[1])
