"""Canonicalize provider/internal locations into a stable unified dataset."""

from __future__ import annotations

import math
import zlib
from collections import defaultdict

from app.services.location_normalization import (
    canonicalize_country_code,
    canonicalize_location_type,
    coordinate_bucket,
    display_city,
    extract_iata_code,
    normalize_string,
)


class LocationUnificationService:
    def build_unified_locations(self, locations: list[dict]) -> list[dict]:
        canonical_locations = [self._canonicalize_location(location) for location in locations]
        self._assign_nearby_airport_iata(canonical_locations)

        groups: dict[str, list[dict]] = defaultdict(list)
        for canonical in canonical_locations:
            groups[self._build_group_key(canonical)].append(canonical)

        unified_locations = [
            self._build_unified_location(match_key, items)
            for match_key, items in groups.items()
            if items
        ]

        return sorted(unified_locations, key=lambda item: (item["name"], item["country"]))

    def search_locations(self, unified_locations: list[dict], query: str, limit: int = 20) -> list[dict]:
        normalized_query = normalize_string(query)
        if len(normalized_query) < 2:
            return []

        scored: list[tuple[int, dict]] = []
        for location in unified_locations:
            score = self._score_location(location, normalized_query)
            if score <= 0:
                continue
            scored.append((score + min(location.get("provider_count", 0), 5), location))

        scored.sort(key=lambda item: (-item[0], item[1]["name"]))
        ranked = [location for _, location in scored]
        filtered = self._filter_generic_city_rows(ranked)
        return filtered[:limit]

    def _canonicalize_location(self, location: dict) -> dict:
        latitude = _safe_float(location.get("latitude"))
        longitude = _safe_float(location.get("longitude"))
        location_type = canonicalize_location_type(location.get("location_type"), location.get("name"))
        country_code = canonicalize_country_code(location.get("country_code"), location.get("country"))
        city = display_city(location.get("city") or location.get("name"))
        iata = extract_iata_code(location) if location_type == "airport" else None

        return {
            "provider": location.get("provider", "unknown"),
            "provider_location_id": str(location.get("provider_location_id") or ""),
            "name": str(location.get("name") or "").strip(),
            "city": city,
            "country": self._display_country(location.get("country"), country_code),
            "country_code": country_code,
            "latitude": latitude,
            "longitude": longitude,
            "location_type": location_type,
            "iata": iata,
            "dropoffs": list(location.get("dropoffs") or []),
            "supports_one_way": bool(location.get("supports_one_way")),
            "our_location_id": location.get("our_location_id"),
            "alias_tokens": self._build_aliases(location, city),
        }

    def _build_group_key(self, location: dict) -> str:
        country_code = location["country_code"] or normalize_string(location["country"]).upper()
        location_type = location["location_type"]

        if location_type == "airport" and location["iata"]:
            return f"{country_code}|airport|{location['iata']}"

        city_key = normalize_string(location["city"])

        # For non-airport locations, group purely by country + type + city.
        # This ensures all providers' "Dubai Downtown" entries merge into one
        # unified location regardless of whether they have coords or not.
        if city_key:
            return f"{country_code}|{location_type}|{city_key}"

        # Fallback for locations with no city: use name.
        name_key = normalize_string(location["name"])
        return f"{country_code}|{location_type}|{name_key}"

    def _build_unified_location(self, match_key: str, items: list[dict]) -> dict:
        first = items[0]
        aliases = []
        providers = []
        latitudes = [item["latitude"] for item in items if item["latitude"] is not None and item["latitude"] != 0.0]
        longitudes = [item["longitude"] for item in items if item["longitude"] is not None and item["longitude"] != 0.0]
        our_location_id = None

        for item in items:
            aliases.extend(item["alias_tokens"])
            aliases.append(item["name"])
            aliases.append(item["city"])
            if item["our_location_id"] and not our_location_id:
                our_location_id = item["our_location_id"]

            prov_lat = item["latitude"] if item["latitude"] and item["latitude"] != 0.0 else None
            prov_lon = item["longitude"] if item["longitude"] and item["longitude"] != 0.0 else None
            providers.append(
                {
                    "provider": item["provider"],
                    "pickup_id": item["provider_location_id"],
                    "original_name": item["name"],
                    "dropoffs": item["dropoffs"],
                    "latitude": prov_lat,
                    "longitude": prov_lon,
                    "supports_one_way": item["supports_one_way"],
                }
            )

        location_type = first["location_type"]
        city = first["city"]
        iata = first.get("iata")
        name = self._build_display_name(city, location_type, iata)
        if location_type == "airport" and iata and iata not in aliases:
            aliases.append(iata)

        unique_aliases = []
        for alias in aliases:
            alias = alias.strip()
            if not alias or alias == name or alias in unique_aliases:
                continue
            unique_aliases.append(alias)

        return {
            "id": f"loc_{zlib.crc32(match_key.encode()) & 0xFFFFFFFF}",
            "unified_location_id": zlib.crc32(match_key.encode()) & 0xFFFFFFFF,
            "match_key": match_key,
            "name": name,
            "aliases": unique_aliases,
            "city": city,
            "country": first["country"],
            "country_code": first["country_code"],
            "latitude": round(sum(latitudes) / len(latitudes), 6) if latitudes else 0.0,
            "longitude": round(sum(longitudes) / len(longitudes), 6) if longitudes else 0.0,
            "location_type": location_type,
            "iata": iata,
            "providers": providers,
            "provider_count": len(providers),
            "our_location_id": our_location_id,
            "confidence": 1.0,
        }

    def _score_location(self, location: dict, query: str) -> int:
        iata = normalize_string(location.get("iata"))
        name = normalize_string(location.get("name"))
        city = normalize_string(location.get("city"))
        country = normalize_string(location.get("country"))
        aliases = [normalize_string(alias) for alias in location.get("aliases", [])]

        if iata and iata == query:
            return 100
        if name == query:
            return 90
        if city == query:
            return 80
        if any(alias == query for alias in aliases):
            return 78
        if name.startswith(query):
            return 70
        if city.startswith(query):
            return 60
        if any(alias.startswith(query) for alias in aliases):
            return 55
        if query in name:
            return 50
        if query in city:
            return 45
        if any(query in alias for alias in aliases):
            return 40
        if query in country:
            return 20
        return 0

    def _assign_nearby_airport_iata(self, locations: list[dict]) -> None:
        airports_with_iata = [
            location
            for location in locations
            if location.get("location_type") == "airport" and location.get("iata")
        ]

        # Count how many providers use each IATA per city — used to pick the
        # "primary" airport when a provider gives no IATA and no coords.
        iata_popularity: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ap in airports_with_iata:
            key = (ap["country_code"], normalize_string(ap["city"]))
            iata_popularity[key][ap["iata"]] += 1

        for location in locations:
            if location.get("location_type") != "airport" or location.get("iata"):
                continue

            has_coords = location.get("latitude") is not None and location.get("longitude") is not None

            # Try coordinate-based match first (within 15 km).
            best_match = None
            best_distance = None
            if has_coords:
                for candidate in airports_with_iata:
                    if normalize_string(candidate["city"]) != normalize_string(location["city"]):
                        continue
                    if candidate["country_code"] != location["country_code"]:
                        continue
                    distance = _distance_km(
                        location["latitude"], location["longitude"],
                        candidate.get("latitude"), candidate.get("longitude"),
                    )
                    if distance is None or distance > 15:
                        continue
                    if best_distance is None or distance < best_distance:
                        best_match = candidate
                        best_distance = distance

            if best_match is not None:
                location["iata"] = best_match["iata"]
                continue

            # Fallback: no coords or no nearby match — assign the most popular
            # IATA code for this city so the location merges instead of creating
            # a duplicate group.
            city_key = (location["country_code"], normalize_string(location["city"]))
            city_iatas = iata_popularity.get(city_key)
            if city_iatas:
                location["iata"] = max(city_iatas, key=city_iatas.get)

    def _filter_generic_city_rows(self, locations: list[dict]) -> list[dict]:
        specific_cities = {
            normalize_string(location["city"])
            for location in locations
            if location.get("location_type") not in {"other", ""}
        }

        filtered = []
        for location in locations:
            city_key = normalize_string(location["city"])
            name_key = normalize_string(location["name"])
            is_generic_city_row = location.get("location_type") == "other" and city_key and name_key == city_key
            if is_generic_city_row and city_key in specific_cities:
                continue
            filtered.append(location)

        return filtered

    def _build_aliases(self, location: dict, canonical_city: str) -> list[str]:
        aliases = []
        name = str(location.get("name") or "").strip()
        raw_city = str(location.get("city") or "").strip()

        if raw_city and raw_city != canonical_city:
            aliases.append(raw_city)
        if name:
            aliases.append(name)

        if canonical_city == "Marrakech":
            aliases.append("Marrakesh")
        if canonical_city == "Antwerp":
            aliases.append("Antwerpen")

        return aliases

    def _build_display_name(self, city: str, location_type: str, iata: str | None = None) -> str:
        type_labels = {
            "airport": "Airport",
            "downtown": "Downtown",
            "port": "Port",
            "train_station": "Train Station",
            "bus_station": "Bus Station",
            "hotel": "Hotel",
            "other": "",
        }

        suffix = type_labels.get(location_type, "")
        if not suffix:
            return city
        # Guard against "Dubai Airport" + "Airport" → "Dubai Airport Airport"
        if city.lower().endswith(suffix.lower()):
            base = city
        else:
            base = f"{city} {suffix}"

        # Append IATA for airports so "Dubai Airport (DXB)" and "Dubai Airport (DWC)"
        # are distinguishable.
        if location_type == "airport" and iata:
            return f"{base} ({iata})"
        return base

    def _display_country(self, country: str | None, country_code: str) -> str:
        if country and len(country.strip()) > 2:
            return country.strip()

        country_names = {
            "AE": "United Arab Emirates",
            "BE": "Belgium",
            "MA": "Morocco",
        }
        return country_names.get(country_code, (country or country_code or "").strip())


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_km(lat1, lon1, lat2, lon2) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None

    radius = 6371.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c
