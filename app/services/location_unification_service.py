"""Canonicalize provider/internal locations into a stable unified dataset."""

from __future__ import annotations

import math
import statistics
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
        unified_locations = self._dedupe_by_our_location_id(unified_locations)

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
        filtered = self._filter_by_query_location_type(filtered, normalized_query)
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
            "extended_location_code": (
                str(location.get("extended_location_code")).strip()
                if location.get("extended_location_code") is not None
                and str(location.get("extended_location_code")).strip()
                else None
            ),
            "extended_dropoff_code": (
                str(location.get("extended_dropoff_code")).strip()
                if location.get("extended_dropoff_code") is not None
                and str(location.get("extended_dropoff_code")).strip()
                else None
            ),
            "provider_code": (
                str(location.get("provider_code")).strip()
                if location.get("provider_code") is not None
                and str(location.get("provider_code")).strip()
                else None
            ),
            "our_location_id": location.get("our_location_id"),
            "alias_tokens": self._build_aliases(location, city),
        }

    def _build_group_key(self, location: dict) -> str:
        country_code = location["country_code"] or normalize_string(location["country"]).upper()
        location_type = location["location_type"]

        if location_type == "airport":
            if location["iata"]:
                return f"{country_code}|airport|{location['iata']}"

            geo_key = coordinate_bucket(location["latitude"], location["longitude"])
            if geo_key:
                return f"{country_code}|airport|geo|{geo_key}"

            name_key = normalize_string(location["name"])
            if name_key:
                return f"{country_code}|airport|name|{name_key}"

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
                    "extended_location_code": item.get("extended_location_code"),
                    "extended_dropoff_code": item.get("extended_dropoff_code"),
                    "country_code": item.get("country_code"),
                    "iata": item.get("iata"),
                    "provider_code": item.get("provider_code"),
                }
            )

        location_type = first["location_type"]
        city = first["city"]
        iata = first.get("iata")
        name = self._build_display_name(city, location_type, iata, items)
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
            "latitude": round(statistics.median(latitudes), 6) if latitudes else 0.0,
            "longitude": round(statistics.median(longitudes), 6) if longitudes else 0.0,
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

            if has_coords or not self._is_generic_city_airport_name(location):
                continue

            # Only nameless/generic airport rows without coordinates inherit the
            # city's primary IATA. That keeps providers like renteon mergeable
            # without collapsing distinct same-city airports into one row.
            city_key = (location["country_code"], normalize_string(location["city"]))
            city_iatas = iata_popularity.get(city_key)
            if city_iatas:
                location["iata"] = max(city_iatas, key=city_iatas.get)

    def _is_generic_city_airport_name(self, location: dict) -> bool:
        city = normalize_string(location.get("city"))
        name = normalize_string(location.get("name"))
        if not city or not name:
            return False

        generic_names = {
            city,
            f"{city} airport",
            f"airport {city}",
            f"{city} international airport",
        }
        return name in generic_names

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

    def _filter_by_query_location_type(self, locations: list[dict], query: str) -> list[dict]:
        requested_types = self._requested_location_types(query)
        if not requested_types:
            return locations

        filtered = [
            location
            for location in locations
            if self._location_matches_requested_type(location, requested_types)
        ]
        return filtered or locations

    def _requested_location_types(self, query: str) -> set[str]:
        requested: set[str] = set()

        if "airport" in query or "terminal" in query:
            requested.add("airport")
        if "downtown" in query or "city center" in query or "city centre" in query or "office" in query:
            requested.add("downtown")
        if "port" in query or "harbour" in query or "harbor" in query or "ferry" in query:
            requested.add("port")
        if (
            "bus station" in query
            or "bus stop" in query
            or "coach station" in query
            or "coach stop" in query
        ):
            requested.add("bus_station")
        if (
            "train station" in query
            or "railway station" in query
            or "rail station" in query
            or "gare" in query
            or "bahnhof" in query
        ):
            requested.add("train_station")

        # Generic "station" search should still surface both station types.
        if "station" in query and not (requested & {"bus_station", "train_station"}):
            requested.update({"bus_station", "train_station"})

        return requested

    def _location_matches_requested_type(self, location: dict, requested_types: set[str]) -> bool:
        location_type = str(location.get("location_type") or "")
        if location_type in requested_types:
            return True

        name = normalize_string(location.get("name"))
        aliases = [normalize_string(alias) for alias in location.get("aliases", [])]
        haystacks = [name, *aliases]

        if "airport" in requested_types and any(
            "airport" in value or "terminal" in value
            for value in haystacks
        ):
            return True

        if "downtown" in requested_types and any(
            "downtown" in value or "city center" in value or "city centre" in value or "office" in value
            for value in haystacks
        ):
            return True

        if "port" in requested_types and any(
            "port" in value or "harbour" in value or "harbor" in value or "ferry" in value
            for value in haystacks
        ):
            return True

        if "bus_station" in requested_types and any(
            "bus station" in value or "bus stop" in value or "coach station" in value or "coach stop" in value
            for value in haystacks
        ):
            return True

        if "train_station" in requested_types and any(
            "train station" in value or "railway station" in value or "rail station" in value
            or "gare" in value or "bahnhof" in value
            for value in haystacks
        ):
            return True

        return False

    def _dedupe_by_our_location_id(self, locations: list[dict]) -> list[dict]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        passthrough: list[dict] = []

        for location in locations:
            our_location_id = location.get("our_location_id")
            if not our_location_id:
                passthrough.append(location)
                continue
            grouped[str(our_location_id)].append(location)

        deduped = list(passthrough)
        for items in grouped.values():
            if len(items) == 1:
                deduped.extend(items)
                continue
            deduped.append(max(items, key=self._our_location_rank))

        return deduped

    def _our_location_rank(self, location: dict) -> tuple[int, int, int]:
        has_iata = 1 if location.get("iata") else 0
        provider_count = int(location.get("provider_count") or 0)
        has_coords = 1 if location.get("latitude") not in (None, 0.0) and location.get("longitude") not in (None, 0.0) else 0
        return (has_iata, provider_count, has_coords)

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

    def _build_display_name(
        self,
        city: str,
        location_type: str,
        iata: str | None = None,
        items: list[dict] | None = None,
    ) -> str:
        type_labels = {
            "airport": "Airport",
            "downtown": "Downtown",
            "port": "Port",
            "train_station": "Train Station",
            "bus_station": "Bus Station",
            "hotel": "Hotel",
            "other": "",
        }

        if location_type == "airport" and not iata:
            distinct_airport_name = self._build_distinct_airport_name(city, items or [])
            if distinct_airport_name:
                return distinct_airport_name

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

    def _build_distinct_airport_name(self, city: str, items: list[dict]) -> str | None:
        city_key = normalize_string(city)
        generic_names = {
            city_key,
            f"{city_key} airport",
            f"airport {city_key}",
            f"{city_key} international airport",
        }

        for item in items:
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                continue

            candidate = raw_name.split(",", 1)[0].strip()
            candidate = candidate.replace("  ", " ")
            candidate_key = normalize_string(candidate)
            if not candidate_key or candidate_key in generic_names:
                continue

            if "airport" not in candidate_key and "terminal" not in candidate_key:
                continue

            return candidate

        return None

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
