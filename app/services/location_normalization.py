"""Helpers for canonicalizing provider and internal location data."""

from __future__ import annotations

import re
import unicodedata


_CITY_ALIASES = {
    "marrakesh": "marrakech",
    "antwerpen": "antwerp",
}

_COUNTRY_NAME_TO_CODE = {
    "belgium": "BE",
    "morocco": "MA",
    "united arab emirates": "AE",
    "uae": "AE",
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
    "hotel": "hotel",
}

_AIRPORT_HINTS = ("airport", "terminal", "aeropuerto", "aeroport")
_PORT_HINTS = ("port", "harbour", "harbor", "ferry")
_TRAIN_HINTS = ("train", "station", "railway", "gare", "bahnhof")
_DOWNTOWN_HINTS = ("downtown", "city", "office", "center", "centre", "central")

# Ordered longest-first so "international airport" is checked before "airport".
_CITY_TYPE_SUFFIXES = [
    "international airport", "intl airport",
    "airport", "aeropuerto", "aeroport",
    "railway station", "train station",
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

    country_key = normalize_string(country)
    return _COUNTRY_NAME_TO_CODE.get(country_key, code.upper())


def canonicalize_city(city: str | None) -> str:
    key = normalize_string(city)
    if not key:
        return ""

    return _CITY_ALIASES.get(key, key)


def strip_type_suffix(name: str) -> str:
    """Remove location-type words and trailing IATA codes from a city/name."""
    text = name.strip()

    # Strip trailing parenthetical codes first: "Dubai Airport (DXB)" → "Dubai Airport"
    text = re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", text).strip()

    # Strip trailing bare IATA-like codes: "Marrakech Menara Airport RAK" → "Marrakech Menara Airport"
    text = re.sub(r"\s+[A-Z]{3}\s*$", "", text).strip()

    # Now strip type suffixes (longest first).
    lowered = text.lower()
    for suffix in _CITY_TYPE_SUFFIXES:
        if lowered.endswith(suffix):
            stripped = text[: len(text) - len(suffix)].strip()
            if stripped:
                text = stripped
                lowered = text.lower()
                # Try another pass — handles "Marrakech Menara Airport" → strip
                # "airport" → "Marrakech Menara", but "Menara" is fine.
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
    if value in _TYPE_ALIASES:
        return _TYPE_ALIASES[value]

    haystack = normalize_string(name)
    if any(hint in haystack for hint in _AIRPORT_HINTS):
        return "airport"
    if any(hint in haystack for hint in _PORT_HINTS):
        return "port"
    if any(hint in haystack for hint in _TRAIN_HINTS):
        return "train_station"
    if any(hint in haystack for hint in _DOWNTOWN_HINTS):
        return "downtown"

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
