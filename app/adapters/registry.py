"""Adapter registry — discovers and manages all supplier adapters."""

import logging
from pathlib import Path

import yaml

from app.adapters.base import BaseAdapter
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Alias map: unified_locations.json name → adapter supplier_id
_PROVIDER_ALIASES: dict[str, str] = {
    "greenmotion": "green_motion",
    "adobe": "adobe_car",
    "okmobility": "ok_mobility",
}

# All registered adapter classes (populated by register_adapter)
_adapter_classes: dict[str, type[BaseAdapter]] = {}

# Active adapter instances
_adapter_instances: dict[str, BaseAdapter] = {}

# Supplier configs loaded from YAML
_supplier_configs: dict[str, dict] = {}


def register_adapter(cls: type[BaseAdapter]) -> type[BaseAdapter]:
    """Decorator to register an adapter class.

    Usage:
        @register_adapter
        class GreenMotionAdapter(BaseAdapter):
            supplier_id = "green_motion"
    """
    if not cls.supplier_id:
        raise ValueError(f"Adapter {cls.__name__} must set supplier_id")
    _adapter_classes[cls.supplier_id] = cls
    logger.info("Registered adapter: %s (%s)", cls.supplier_id, cls.__name__)
    return cls


def load_supplier_configs(config_dir: str = "config/suppliers") -> dict[str, dict]:
    """Load all supplier YAML configs from the config directory."""
    global _supplier_configs
    config_path = Path(config_dir)
    if not config_path.exists():
        logger.warning("Supplier config directory not found: %s", config_dir)
        return {}

    for yaml_file in sorted(config_path.glob("*.yaml")):
        with open(yaml_file) as f:
            config = yaml.safe_load(f) or {}
        supplier_id = config.get("id", yaml_file.stem)
        _supplier_configs[supplier_id] = config
        logger.info("Loaded config for supplier: %s", supplier_id)

    return _supplier_configs


def get_supplier_config(supplier_id: str) -> dict:
    """Get a supplier's YAML config."""
    return _supplier_configs.get(supplier_id, {})


def _resolve_id(supplier_id: str) -> str:
    """Resolve provider aliases to canonical adapter ID."""
    return _PROVIDER_ALIASES.get(supplier_id, supplier_id)


def get_adapter(supplier_id: str) -> BaseAdapter | None:
    """Get an active adapter instance by supplier ID."""
    supplier_id = _resolve_id(supplier_id)

    if supplier_id in _adapter_instances:
        return _adapter_instances[supplier_id]

    cls = _adapter_classes.get(supplier_id)
    if cls is None:
        return None

    config = _supplier_configs.get(supplier_id, {})
    if not config.get("enabled", True):
        logger.info("Adapter %s is disabled in config", supplier_id)
        return None

    instance = cls()
    _adapter_instances[supplier_id] = instance
    return instance


def get_all_adapters() -> list[BaseAdapter]:
    """Get all enabled adapter instances."""
    adapters = []
    for supplier_id in _supplier_configs:
        adapter = get_adapter(supplier_id)
        if adapter is not None:
            adapters.append(adapter)
    return adapters


def get_adapters_for_location(provider_entries: list[dict]) -> list[tuple[BaseAdapter, dict]]:
    """Get adapters that serve a specific location.

    Args:
        provider_entries: List of provider entries from the unified location.

    Returns:
        List of (adapter, provider_entry) tuples.
    """
    results = []
    for entry in provider_entries:
        provider = entry.get("provider", "")
        adapter = get_adapter(provider)
        if adapter is not None:
            results.append((adapter, entry))
    return results


def list_suppliers() -> list[dict]:
    """List all configured suppliers with their status."""
    suppliers = []
    for supplier_id, config in _supplier_configs.items():
        adapter = _adapter_classes.get(supplier_id)
        suppliers.append({
            "id": supplier_id,
            "name": config.get("name", supplier_id),
            "enabled": config.get("enabled", True),
            "has_adapter": adapter is not None,
            "supports_one_way": config.get("supports_one_way", False),
            "countries": config.get("countries", []),
        })
    return suppliers


async def close_all_adapters() -> None:
    """Close all active adapter instances."""
    for adapter in _adapter_instances.values():
        await adapter.close()
    _adapter_instances.clear()
