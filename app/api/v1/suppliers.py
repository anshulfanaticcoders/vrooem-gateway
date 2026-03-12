"""Supplier management endpoints."""

from fastapi import APIRouter, Depends

from app.adapters.registry import list_suppliers
from app.core.auth import verify_api_key
from app.services.circuit_breaker import CircuitBreakerRegistry

router = APIRouter(prefix="/api/v1/suppliers", tags=["suppliers"])

# Shared circuit breaker registry (created at app startup, injected here)
_cb_registry: CircuitBreakerRegistry | None = None


def set_circuit_breaker_registry(registry: CircuitBreakerRegistry) -> None:
    global _cb_registry
    _cb_registry = registry


@router.get("")
async def get_suppliers(_api_key: str = Depends(verify_api_key)):
    """List all configured suppliers with their status."""
    suppliers = list_suppliers()

    # Attach circuit breaker state if available
    if _cb_registry:
        cb_states = {s["supplier_id"]: s for s in _cb_registry.all_states()}
        for supplier in suppliers:
            cb = cb_states.get(supplier["id"])
            supplier["circuit_breaker"] = cb["state"] if cb else "unknown"
    else:
        for supplier in suppliers:
            supplier["circuit_breaker"] = "unknown"

    return {"suppliers": suppliers, "total": len(suppliers)}


@router.get("/{supplier_id}")
async def get_supplier(supplier_id: str, _api_key: str = Depends(verify_api_key)):
    """Get details for a specific supplier."""
    suppliers = list_suppliers()
    for supplier in suppliers:
        if supplier["id"] == supplier_id:
            if _cb_registry:
                cb = _cb_registry.get(supplier_id)
                supplier["circuit_breaker_detail"] = cb.to_dict()
            return supplier
    return {"error": f"Supplier '{supplier_id}' not found"}, 404
