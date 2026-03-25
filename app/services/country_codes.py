"""Country name/code normalization helpers shared across gateway services."""

from __future__ import annotations

import re
import unicodedata

_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "albania": "AL", "antigua and barbuda": "AG", "argentina": "AR",
    "armenia": "AM", "australia": "AU", "austria": "AT", "azerbaijan": "AZ",
    "belgium": "BE", "belgie": "BE", "belgië": "BE", "bonaire": "BQ",
    "bosnia and herzegovina": "BA", "bulgaria": "BG",
    "canada": "CA", "colombia": "CO", "costa rica": "CR", "croatia": "HR",
    "curacao": "CW", "curaçao": "CW", "cyprus": "CY", "czech republic": "CZ",
    "denmark": "DK", "dominican republic": "DO",
    "egypt": "EG", "estonia": "EE", "ethiopia": "ET",
    "finland": "FI", "france": "FR", "georgia": "GE", "germany": "DE",
    "greece": "GR", "guadeloupe": "GP", "hungary": "HU",
    "iceland": "IS", "ireland": "IE", "israel": "IL", "italy": "IT",
    "jamaica": "JM", "japan": "JP", "jordan": "JO",
    "kenya": "KE", "kosovo": "XK", "kuwait": "KW",
    "latvia": "LV", "lebanon": "LB", "lithuania": "LT", "luxembourg": "LU",
    "malaysia": "MY", "malta": "MT", "martinique": "MQ", "mauritius": "MU",
    "mexico": "MX", "montenegro": "ME", "morocco": "MA",
    "namibia": "NA", "netherlands": "NL", "new zealand": "NZ",
    "north macedonia": "MK", "norway": "NO",
    "oman": "OM", "panama": "PA", "peru": "PE", "philippines": "PH",
    "poland": "PL", "portugal": "PT", "qatar": "QA",
    "romania": "RO", "rwanda": "RW",
    "saudi arabia": "SA", "serbia": "RS", "singapore": "SG",
    "slovakia": "SK", "slovenia": "SI", "south africa": "ZA",
    "south korea": "KR", "spain": "ES", "espana": "ES", "españa": "ES",
    "sri lanka": "LK", "sweden": "SE", "switzerland": "CH",
    "tanzania": "TZ", "thailand": "TH", "trinidad and tobago": "TT",
    "tunisia": "TN", "turkey": "TR", "turkiye": "TR", "türkiye": "TR",
    "united arab emirates": "AE", "uae": "AE", "united kingdom": "GB",
    "united states": "US", "usa": "US", "uruguay": "UY", "uzbekistan": "UZ",
}


def resolve_country_code(country: str | None) -> str | None:
    if not country:
        return None

    normalized = unicodedata.normalize("NFKD", country)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    key = re.sub(r"[^a-z0-9]+", " ", ascii_text.lower().strip()).strip()
    if len(key) == 2 and key.isalpha():
        return key.upper()

    return _COUNTRY_NAME_TO_CODE.get(key)
