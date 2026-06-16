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
            part
            for part in [location.get("phone_country_code"), location.get("phone_number")]
            if part
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


def _money(amount: float | None, currency: str) -> str | None:
    if amount is None:
        return None
    return f"{currency} {amount:.2f}"


def resolve_terms_metadata(
    country_code: str | None,
    account_code: str | None,
    excess_amount: float | None,
) -> dict | None:
    """Return provider-approved static policy text for Easirent offers.

    Dynamic quote prices still come from Easirent's API. These terms are from
    supplier documents stored outside the codebase:
    - Partner Terms and Conditions US - 27022025.docx
    - Partner Terms and Conditions US Inclusive rates- 21112022.docx
    - USA Fleet 09012026.xlsx
    """

    normalized_country = (country_code or "").strip().upper()
    if normalized_country != "US":
        return None

    currency = "USD"
    deposit_amount = 250.0 if account_code == "$USA202A" else excess_amount
    deposit_label = _money(deposit_amount, currency)
    excess_label = _money(excess_amount, currency)

    insurance_conditions = [
        (
            "Renter must present proof of collision and liability coverage unless purchasing "
            "supplier coverage locally."
        ),
        (
            "Collision damage waiver and liability products may be purchased at the counter "
            "when available."
        ),
    ]
    if account_code == "$USA202A":
        insurance_conditions.append(
            "Inclusive US account terms include CDW with zero excess; a USD 250 deposit may "
            "still be held for tolls, fines and administration."
        )
    else:
        insurance_conditions.append(
            "If supplier collision waiver is not purchased locally, renter remains liable up "
            "to the applicable excess or vehicle value."
        )

    deposit_conditions = [
        "Credit and debit cards are accepted for rental payment.",
        (
            "A valid credit card in the main driver's name is required for the security "
            "deposit; debit cards are not accepted for the deposit."
        ),
    ]
    if deposit_label:
        deposit_conditions.append(
            f"Security deposit shown by Easirent fleet sheet: {deposit_label}."
        )
    if excess_label:
        deposit_conditions.append(
            f"Applicable excess shown by Easirent fleet sheet: {excess_label}."
        )

    return {
        "source": "Easirent US supplier documents 2025/2026",
        "deposit_amount": deposit_amount,
        "deposit_currency": currency if deposit_amount is not None else None,
        "excess_amount": excess_amount,
        "excess_currency": currency if excess_amount is not None else None,
        "fuel_policy": "Full-to-full / like-for-like",
        "fuel_policy_label": "Return with the same fuel level",
        "mileage_policy_text": (
            "Unlimited mileage for 1-29 day rentals except local renters. "
            "Local renters are limited to 150 miles per day; rentals over 30 days are "
            "limited to 500 miles per week."
        ),
        "counter_only_extras": [
            "GPS",
            "Child booster seat",
            "Additional driver",
        ],
        "product_benefits": [
            "Pay at pickup",
            "Credit card required for deposit",
            "Counter-only extras paid locally",
            "Supplier cancellation/no-show terms apply",
        ],
        "driver_requirements": {
            "Valid_credit_card_in_main_driver_name_required_for_deposit": "1",
            "Debit_cards_not_accepted_for_security_deposit": "1",
            (
                "Proof_of_collision_and_liability_coverage_required_unless_supplier_"
                "coverage_is_purchased_locally"
            ): "1",
            "Flight_number_required_for_airport_collection": "1",
            "mileage_type": (
                "Unlimited for 1-29 day rentals except local renters; local renters 150 miles/day"
            ),
        },
        "rental_policies": [
            {
                "label": "Fuel Policy",
                "value": "Return with the same fuel level",
                "detail": (
                    "If Easirent refuels the vehicle, supplier terms list USD 7.99 per "
                    "gallon plus USD 9.99 admin fee plus tax."
                ),
            },
            {
                "label": "Mileage",
                "value": "Conditional unlimited mileage",
                "detail": (
                    "Unlimited for 1-29 day rentals except local renters. "
                    "Local renters: 150 miles/day. Rentals over 30 days: 500 miles/week."
                ),
            },
            {
                "label": "Security Deposit",
                "value": deposit_label or "Supplier deposit applies",
                "detail": (
                    "A valid credit card in the main driver's name is required for the "
                    "security deposit."
                ),
            },
            {
                "label": "Payment at Pickup",
                "value": "Credit or debit card for rental payment",
                "detail": "Debit cards are not accepted for the security deposit.",
            },
            {
                "label": "Counter Extras",
                "value": "Paid locally when available",
                "detail": (
                    "GPS, child booster seat and additional driver are handled at the "
                    "counter unless Easirent provides live pre-bookable pricing."
                ),
            },
        ],
        "terms": [
            {"name": "Payment & Deposit", "conditions": deposit_conditions},
            {"name": "Insurance & Excess", "conditions": insurance_conditions},
            {
                "name": "Fuel & Mileage",
                "conditions": [
                    (
                        "Fuel policy is full-to-full or like-for-like depending on counter "
                        "agreement."
                    ),
                    (
                        "If Easirent refuels the vehicle, supplier terms list USD 7.99 per "
                        "gallon plus USD 9.99 admin fee plus tax."
                    ),
                    "Unused fuel is not refunded when fuel is purchased up front.",
                    (
                        "Unlimited mileage applies to 1-29 day rentals except local renters. "
                        "Local renters are limited to 150 miles per day."
                    ),
                ],
            },
            {
                "name": "Pickup Requirements",
                "conditions": [
                    "Flight number is required for airport collection.",
                    (
                        "If no flight number is provided and the customer is late, the "
                        "booking may be cancelled under supplier terms."
                    ),
                    "Out-of-hours service is on request and must be arranged with reservations.",
                ],
            },
            {
                "name": "Counter-only Services",
                "conditions": [
                    (
                        "GPS, child booster seat and additional driver may be paid locally "
                        "when available."
                    ),
                    (
                        "These items are not shown as pre-bookable Vrooem extras unless "
                        "Easirent provides live API pricing."
                    ),
                ],
            },
            {
                "name": "Cancellation & No-show",
                "conditions": [
                    (
                        "Cancellation must be made before pickup by email, telephone or "
                        "Manage My Booking."
                    ),
                    (
                        "If the booking is not cancelled and the vehicle is not collected, "
                        "supplier no-show fees apply."
                    ),
                ],
            },
        ],
    }


def build_static_roi_locations() -> list[dict]:
    locations = []
    collection_details = load_collection_details().get("ROI", {})

    for station_code, location in load_roi_locations().items():
        details = collection_details.get(station_code, {})
        phone = " ".join(
            part
            for part in [location.get("phone_country_code"), location.get("phone_number")]
            if part
        ).strip()
        address_parts = [
            location.get("address_line_1"),
            location.get("address_line_2"),
            location.get("post_code"),
            location.get("address_city"),
        ]

        locations.append(
            {
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
            }
        )

    return sorted(locations, key=lambda item: item["provider_location_id"])


def build_static_us_locations() -> list[dict]:
    locations = []

    for station_code, location in load_us_locations().items():
        locations.append(
            {
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
            }
        )

    return sorted(locations, key=lambda item: item["provider_location_id"])


def build_static_locations() -> list[dict]:
    return sorted(
        [*build_static_roi_locations(), *build_static_us_locations()],
        key=lambda item: (item.get("country_code", ""), item.get("provider_location_id", "")),
    )
