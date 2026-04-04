"""Search orchestration — dispatches to adapters in parallel, merges results."""

import asyncio
import logging
import time
import uuid

from app.adapters.base import BaseAdapter
from app.adapters.registry import get_adapter
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import ProviderFailure, SearchRequest, SearchResponse, SupplierResult
from app.schemas.vehicle import Vehicle
from app.services.cache_service import CacheService
from app.services.circuit_breaker import CircuitBreakerRegistry
from app.services.json_location_repository import JsonLocationRepository

logger = logging.getLogger(__name__)
_json_location_repository = JsonLocationRepository()


def _provider_entries_signature(provider_entries: list[dict]) -> str | None:
    signatures: list[str] = []
    for entry in provider_entries:
        provider = str(entry.get("provider", "")).strip().lower()
        pickup_id = str(entry.get("pickup_id", "")).strip()
        if not provider or not pickup_id:
            continue
        signatures.append(f"{provider}:{pickup_id}")

    if not signatures:
        return None

    return ",".join(sorted(dict.fromkeys(signatures)))


def _build_provider_location_entry(entry: dict, provider_id: str) -> ProviderLocationEntry:
    return ProviderLocationEntry.model_validate({
        **entry,
        "provider": provider_id,
        "pickup_id": entry.get("pickup_id", ""),
        "original_name": entry.get("original_name", ""),
        "latitude": entry.get("latitude"),
        "longitude": entry.get("longitude"),
        "dropoffs": entry.get("dropoffs", []),
        "supports_one_way": entry.get("supports_one_way", False),
        "extended_location_code": entry.get("extended_location_code"),
        "extended_dropoff_code": entry.get("extended_dropoff_code"),
        "country_code": entry.get("country_code"),
        "iata": entry.get("iata"),
        "provider_code": entry.get("provider_code"),
    })


def _resolve_dropoff_entry(
    dropoff_location: dict | None,
    provider_id: str,
) -> ProviderLocationEntry | None:
    if not dropoff_location:
        return None

    for entry in dropoff_location.get("providers") or []:
        if str(entry.get("provider", "")).strip().lower() != provider_id:
            continue
        return _build_provider_location_entry(entry, provider_id)

    return None


async def _search_single_supplier(
    adapter: BaseAdapter,
    request: SearchRequest,
    pickup_entry: ProviderLocationEntry,
    dropoff_entry: ProviderLocationEntry | None,
    cb_registry: CircuitBreakerRegistry,
) -> SupplierResult:
    """Search a single supplier with circuit breaker protection."""
    cb = cb_registry.get(adapter.supplier_id)
    start = time.time()

    if not cb.is_available:
        return SupplierResult(
            supplier_id=adapter.supplier_id,
            error="Circuit breaker open",
            response_time_ms=0,
        )

    try:
        vehicles = await adapter.search_vehicles(request, pickup_entry, dropoff_entry)
        elapsed_ms = int((time.time() - start) * 1000)
        cb.record_success()

        return SupplierResult(
            supplier_id=adapter.supplier_id,
            vehicles=vehicles,
            vehicle_count=len(vehicles),
            response_time_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        cb.record_failure()
        logger.error("[%s] Search failed: %s (%dms)", adapter.supplier_id, str(exc), elapsed_ms)

        return SupplierResult(
            supplier_id=adapter.supplier_id,
            error=str(exc),
            response_time_ms=elapsed_ms,
        )


def _build_provider_failure(result: SupplierResult) -> ProviderFailure | None:
    if not result.error:
        return None

    error = result.error.strip()
    lowered = error.lower()

    failure_type = "provider_error"
    retryable = False

    if "circuit breaker open" in lowered:
        failure_type = "circuit_open"
        retryable = True
    elif "timeout" in lowered:
        failure_type = "timeout"
        retryable = True
    elif "connect" in lowered or "unreachable" in lowered or "connection" in lowered:
        failure_type = "transport_error"
        retryable = True

    return ProviderFailure(
        provider=result.supplier_id,
        stage="vehicle_search",
        failure_type=failure_type,
        message=error,
        retryable=retryable,
        raw_excerpt=error,
    )


async def search_vehicles(
    request: SearchRequest,
    provider_entries: list[dict],
    cache: CacheService,
    cb_registry: CircuitBreakerRegistry,
) -> SearchResponse:
    """Search all relevant suppliers in parallel and merge results.

    Args:
        request: Canonical search parameters.
        provider_entries: List of provider entries from the unified location.
            Each entry has: {"provider": "green_motion", "pickup_id": "123", ...}
        cache: Redis cache service.
        cb_registry: Circuit breaker registry.
    """
    search_id = f"search_{uuid.uuid4().hex[:12]}"
    start = time.time()
    is_one_way = (
        request.dropoff_unified_location_id is not None
        and request.dropoff_unified_location_id != request.unified_location_id
    )
    dropoff_location = None
    if is_one_way:
        dropoff_location = _json_location_repository.get_location_by_unified_id(
            request.dropoff_unified_location_id
        )

    # Check cache first
    cache_key_params = {
        "loc": request.unified_location_id,
        "pu": str(request.pickup_date),
        "pt": str(request.pickup_time),
        "do": str(request.dropoff_date),
        "dt": str(request.dropoff_time),
        "cur": request.currency,
        "age": request.driver_age,
        "dloc": request.dropoff_unified_location_id,
        "prov": ",".join(sorted(request.providers)) if request.providers else None,
        "ploc": _provider_entries_signature(provider_entries),
    }
    cached = await cache.get_search(**cache_key_params)
    if cached:
        logger.info("Cache hit for search %s", search_id)
        return SearchResponse(**cached, search_id=search_id, from_cache=True)

    # Build list of (adapter, pickup_entry, dropoff_entry) tuples
    tasks = []
    for entry in provider_entries:
        provider_id = entry.get("provider", "")

        # Filter by requested providers if specified
        if request.providers and provider_id not in request.providers:
            continue

        # Skip internal adapter — internal vehicles are already queried
        # directly from MySQL by Laravel's SearchController
        if provider_id == "internal":
            continue

        adapter = get_adapter(provider_id)
        if adapter is None:
            logger.warning("No adapter found for provider '%s' — skipping", provider_id)
            continue

        # Check one-way support
        if is_one_way and not adapter.supports_one_way:
            continue

        pickup = _build_provider_location_entry(entry, provider_id)
        dropoff = _resolve_dropoff_entry(dropoff_location, provider_id)

        tasks.append(
            _search_single_supplier(adapter, request, pickup, dropoff, cb_registry)
        )

    if not tasks:
        logger.warning("Search %s: No valid adapters found for any provider entry", search_id)
        return SearchResponse(
            search_id=search_id,
            suppliers_queried=0,
            response_time_ms=int((time.time() - start) * 1000),
        )

    # Dispatch all suppliers in parallel with global timeout.
    # return_exceptions=True prevents one crashing adapter from killing ALL results.
    # Global timeout (55s) ensures we respond before Laravel's 60s HTTP timeout.
    try:
        raw_results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=55.0,
        )
    except asyncio.TimeoutError:
        logger.error("Search %s: Global timeout (55s) exceeded", search_id)
        raw_results = []

    # Process results — handle any exceptions that leaked through
    supplier_results: list[SupplierResult] = []
    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            # An adapter raised an exception despite _search_single_supplier's try/except
            logger.error("Search %s: Adapter task %d raised exception: %s", search_id, i, str(result))
            supplier_results.append(SupplierResult(
                supplier_id="unknown",
                error=str(result),
                response_time_ms=0,
            ))
        elif isinstance(result, SupplierResult):
            supplier_results.append(result)

    # Merge all vehicles
    all_vehicles: list[Vehicle] = []
    suppliers_responded = 0
    provider_status: list[ProviderFailure] = []
    for result in supplier_results:
        if result.error is None:
            suppliers_responded += 1
            all_vehicles.extend(result.vehicles)
            continue

        failure = _build_provider_failure(result)
        if failure is not None:
            provider_status.append(failure)

    # Cache individual vehicles for booking retrieval
    for vehicle in all_vehicles:
        await cache.set_vehicle(vehicle.id, vehicle.model_dump(mode="json"))

    elapsed_ms = int((time.time() - start) * 1000)

    response = SearchResponse(
        search_id=search_id,
        vehicles=all_vehicles,
        total_vehicles=len(all_vehicles),
        suppliers_queried=len(tasks),
        suppliers_responded=suppliers_responded,
        supplier_results=supplier_results,
        provider_status=provider_status,
        response_time_ms=elapsed_ms,
    )

    # Cache the response (without individual vehicles to save memory)
    cache_data = response.model_dump(mode="json", exclude={"search_id", "from_cache"})
    await cache.set_search(cache_data, **cache_key_params)

    logger.info(
        "Search %s: %d vehicles from %d/%d suppliers in %dms",
        search_id,
        len(all_vehicles),
        suppliers_responded,
        len(tasks),
        elapsed_ms,
    )

    return response
