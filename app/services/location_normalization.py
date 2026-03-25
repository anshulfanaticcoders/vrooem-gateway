"""Helpers for canonicalizing provider and internal location data."""

from __future__ import annotations

import re
import unicodedata

from app.services.country_codes import resolve_country_code


_CITY_ALIASES = {
    "marrakesh": "marrakech",
    "antwerpen": "antwerp",
}

_TYPE_ALIASES = {
    "airport": "airport",
    "airports": "airport",
    "downtown": "downtown",
    "city": "downtown",
    "office": "downtown",
    "port": "port",
    "harbor": "port",
    "harbour": "port",
    "train": "train_station",
    "train_station": "train_station",
    "railway": "train_station",
    "station": "train_station",
    "bus station": "bus_station",
    "bus_stop": "bus_station",
    "bus stop": "bus_station",
    "coach station": "bus_station",
    "coach stop": "bus_station",
    "hotel": "hotel",
    "industrial": "downtown",
    "terminal": "airport",
    "residential": "downtown",
    "commercial": "downtown",
}

_AIRPORT_HINTS = ("airport", "terminal", "aeropuerto", "aeroport")
_PORT_HINTS = ("port", "harbour", "harbor", "ferry")
_BUS_HINTS = ("bus station", "bus stop", "coach station", "coach stop", "bus", "coach")
_TRAIN_HINTS = ("train station", "railway station", "train", "railway", "gare", "bahnhof", "station")
_DOWNTOWN_HINTS = ("downtown", "city center", "city centre", "center", "centre", "central", "office", "city")

# Ordered longest-first so specific suffixes are stripped before generic ones.
_CITY_TYPE_SUFFIXES = [
    "international airport", "intl airport",
    "airport", "aeropuerto", "aeroport",
    "railway station", "train station",
    "bus station", "coach station", "bus stop", "coach stop",
    "ferry terminal",
    "downtown", "city center", "city centre",
    "harbour", "harbor", "port",
    "station", "central", "office",
]


def normalize_string(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_text.lower().strip()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def canonicalize_country_code(country_code: str | None, country: str | None) -> str:
    code = normalize_string(country_code)
    if len(code) == 2:
        return code.upper()

    resolved = resolve_country_code(country)
    return resolved or code.upper()


def canonicalize_city(city: str | None) -> str:
    key = normalize_string(city)
    if not key:
        return ""

    return _CITY_ALIASES.get(key, key)


def strip_type_suffix(name: str) -> str:
    """Remove location-type words and trailing IATA codes from a city/name."""
    text = name.strip()

    text = re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", text).strip()
    text = re.sub(r"\s+[A-Z]{3}\s*$", "", text).strip()

    lowered = text.lower()
    for suffix in _CITY_TYPE_SUFFIXES:
        if lowered.endswith(suffix):
            stripped = text[: len(text) - len(suffix)].strip()
            if stripped:
                text = stripped
                lowered = text.lower()
                break

    return text


def display_city(city: str | None) -> str:
    if not city or not city.strip():
        return ""
    cleaned = strip_type_suffix(city.strip())
    canonical = canonicalize_city(cleaned)
    if not canonical:
        return ""
    return " ".join(part.capitalize() for part in canonical.split())


def canonicalize_location_type(location_type: str | None, name: str | None = None) -> str:
    value = normalize_string(location_type)
    mapped = _TYPE_ALIASES.get(value)

    # Provider feeds often send overly-generic types like "station" or "office".
    # Use the human-readable name first so "bus station" does not collapse into
    # a train station bucket just because the raw type was generic.
    haystack = normalize_string(name)
    if _contains_hint(haystack, _AIRPORT_HINTS):
        return "airport"
    if _contains_hint(haystack, _PORT_HINTS):
        return "port"
    if _contains_hint(haystack, _BUS_HINTS):
        return "bus_station"
    if _contains_hint(haystack, _TRAIN_HINTS):
        return "train_station"
    if _contains_hint(haystack, _DOWNTOWN_HINTS):
        return "downtown"

    if mapped:
        return mapped

    return "other"


def extract_iata_code(location: dict) -> str | None:
    raw_iata = (location.get("iata") or "").strip().upper()
    if _looks_like_iata(raw_iata):
        return raw_iata

    provider_location_id = str(location.get("provider_location_id") or "")
    for part in provider_location_id.split(":"):
        part = part.strip().upper()
        if _looks_like_iata(part):
            return part

    for part in reversed(re.split(r"[^A-Za-z]+", provider_location_id.upper())):
        if _looks_like_iata(part):
            return part

    name = str(location.get("name") or "")
    match = re.search(r"\(([A-Z]{3})\)", name)
    if match:
        return match.group(1)

    words = re.findall(r"\b[A-Z]{3}\b", name)
    for word in words:
        if _looks_like_iata(word):
            return word

    return None


def coordinate_bucket(latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return ""
    lat_bucket = int(round((float(latitude) + 90) * 10))
    lon_bucket = int(round((float(longitude) + 180) * 10))
    return f"{lat_bucket:04d}{lon_bucket:04d}"


def _looks_like_iata(value: str | None) -> bool:
    return bool(value and len(value) == 3 and value.isalpha())


def _contains_hint(haystack: str, hints: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(hint)}\b", haystack) for hint in hints)
