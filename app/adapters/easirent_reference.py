"""Static reference metadata for Easirent.

The supplier-provided spreadsheets are normalized into repo-owned JSON files so
the gateway does not depend on ad-hoc files in a local Downloads directory.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.adapters.easirent_rules import is_placeholder_vehicle_code

_SUPPLIER_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "suppliers"


def _load_json(filename: str) -> dict:
    with (_SUPPLIER_CONFIG_DIR / filename).open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache
def load_us_fleet() -> dict[str, dict]:
    return _load_json("easirent_us_fleet.json")


@lru_cache
def load_roi_fleet() -> dict[str, dict]:
    return _load_json("easirent_roi_fleet.json")


@lru_cache
def load_roi_locations() -> dict[str, dict]:
    return _load_json("easirent_roi_locations.json")


@lru_cache
def load_collection_details() -> dict[str, dict]:
    return _load_json("easirent_collection_details.json")


@lru_cache
def load_us_locations() -> dict[str, dict]:
    return _load_json("easirent_us_locations.json")


def resolve_location_metadata(country_code: str | None, station_code: str | None) -> dict | None:
    normalized_country = (country_code or "").strip().upper()
    normalized_station = (station_code or "").strip().upper()
    if not normalized_station:
        return None

    if normalized_country == "US":
        location = load_us_locations().get(normalized_station)
        if not location:
            return None

        return {
            "provider": "easirent",
            "provider_location_id": normalized_station,
            "name": location.get("station_name", ""),
            "city": location.get("city", ""),
            "country": location.get("country", "United States"),
            "country_code": location.get("country_code", "US"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "location_type": "airport",
            "iata": normalized_station,
            "address": location.get("non_airport_address"),
            "phone": location.get("phone"),
            "pickup_instructions": location.get("pickup_instructions"),
            "dropoff_instructions": location.get("dropoff_instructions"),
        }

    if normalized_country in {"ROI", "IE"}:
        location = load_roi_locations().get(normalized_station)
        if not location:
            return None

        details = load_collection_details().get("ROI", {}).get(normalized_station, {})
        phone = " ".join(
            part for part in [location.get("phone_country_code"), location.get("phone_number")] if part
        ).strip()
        address_parts = [
            location.get("address_line_1"),
            location.get("address_line_2"),
            location.get("post_code"),
            location.get("address_city"),
        ]

        return {
            "provider": "easirent",
            "provider_location_id": normalized_station,
            "name": location.get("station_name", ""),
            "city": location.get("address_city") or location.get("city", ""),
            "country": "Ireland",
            "country_code": location.get("country_code", "ROI"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "location_type": "airport",
            "iata": normalized_station,
            "address": ", ".join(part for part in address_parts if part),
            "phone": phone,
            "pickup_instructions": details.get("airport"),
            "dropoff_instructions": details.get("non_airport"),
        }

    return None


def resolve_fleet_metadata(country_code: str | None, sipp_code: str | None) -> dict | None:
    if is_placeholder_vehicle_code(sipp_code):
        return None

    normalized_country = (country_code or "").strip().upper()
    normalized_sipp = (sipp_code or "").strip().upper()
    if not normalized_sipp:
        return None

    if normalized_country == "US":
        return load_us_fleet().get(normalized_sipp)

    if normalized_country in {"ROI", "IE"}:
        return load_roi_fleet().get(normalized_sipp)

    return None


def build_static_roi_locations() -> list[dict]:
    locations = []
    collection_details = load_collection_details().get("ROI", {})

    for station_code, location in load_roi_locations().items():
        details = collection_details.get(station_code, {})
        phone = " ".join(
            part for part in [location.get("phone_country_code"), location.get("phone_number")] if part
        ).strip()
        address_parts = [
            location.get("address_line_1"),
            location.get("address_line_2"),
            location.get("post_code"),
            location.get("address_city"),
        ]

        locations.append({
            "provider": "easirent",
            "provider_location_id": station_code,
            "name": location.get("station_name", ""),
            "city": location.get("address_city") or location.get("city", ""),
            "country": "Ireland",
            "country_code": location.get("country_code", "ROI"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "location_type": "airport",
            "iata": station_code,
            "address": ", ".join(part for part in address_parts if part),
            "phone": phone,
            "pickup_instructions": details.get("airport"),
            "dropoff_instructions": details.get("non_airport"),
        })

    return sorted(locations, key=lambda item: item["provider_location_id"])


def build_static_us_locations() -> list[dict]:
    locations = []

    for station_code, location in load_us_locations().items():
        locations.append({
            "provider": "easirent",
            "provider_location_id": station_code,
            "name": location.get("station_name", ""),
            "city": location.get("city", ""),
            "country": location.get("country", "United States"),
            "country_code": location.get("country_code", "US"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "location_type": "airport",
            "iata": station_code,
            "address": location.get("non_airport_address"),
            "phone": location.get("phone"),
            "pickup_instructions": location.get("pickup_instructions"),
            "dropoff_instructions": location.get("dropoff_instructions"),
        })

    return sorted(locations, key=lambda item: item["provider_location_id"])


def build_static_locations() -> list[dict]:
    return sorted(
        [*build_static_roi_locations(), *build_static_us_locations()],
        key=lambda item: (item.get("country_code", ""), item.get("provider_location_id", "")),
    )
