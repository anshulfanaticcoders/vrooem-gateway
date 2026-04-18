"""Regression guard: every adapter whose `supports_one_way = True` must emit
`vehicle.dropoff_location` populated with coordinates when the caller supplies
a distinct dropoff entry. Without this, downstream map pins / dropoff
instructions are starved and the UI silently falls back to pickup data —
which misrepresents a one-way rental as round-trip on booking pages."""

import inspect

from app.adapters import (
    adobe_car,
    click2rent,
    easirent,
    green_motion,
    locauto_rent,
    recordgo,
    renteon,
    sicily_by_car,
    surprice,
)


ONE_WAY_ADAPTER_MODULES = {
    "green_motion": green_motion,
    "usave": None,  # inherits green_motion
    "adobe_car": adobe_car,
    "click2rent": click2rent,
    "easirent": easirent,
    "locauto_rent": locauto_rent,
    "recordgo": recordgo,
    "renteon": renteon,
    "surprice": surprice,
    "sicily_by_car": sicily_by_car,
}


def _adapter_source(module) -> str:
    return inspect.getsource(module)


def test_every_one_way_adapter_has_dropoff_location_code_path():
    """Source-level guard: every one-way adapter must reference the
    `dropoff_location` kwarg somewhere in its search path — either by
    building a VehicleLocation for it, or by explicitly threading
    dropoff_entry into the parsed Vehicle. This catches regressions where
    someone removes the dropoff branch and silently falls back to pickup."""
    missing = []
    for name, module in ONE_WAY_ADAPTER_MODULES.items():
        if module is None:
            continue
        src = _adapter_source(module)
        if "dropoff_location" not in src:
            missing.append(name)
    assert not missing, (
        f"These one-way-capable adapters never set `dropoff_location`: {missing}. "
        "Downstream booking pages will show pickup coords as dropoff — "
        "misrepresenting a one-way rental as round-trip."
    )


def test_every_one_way_adapter_references_dropoff_entry():
    """All one-way adapters must explicitly consult `dropoff_entry` so the
    provider receives the correct one-way request. A round-trip quote
    returned for a one-way request is worse than no result."""
    missing = []
    for name, module in ONE_WAY_ADAPTER_MODULES.items():
        if module is None:
            continue
        src = _adapter_source(module)
        if "dropoff_entry" not in src:
            missing.append(name)
    assert not missing, (
        f"Adapters missing dropoff_entry handling: {missing}"
    )
