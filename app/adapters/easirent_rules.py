"""Business-rule helpers for Easirent account selection and fleet filtering."""

from typing import Optional


def select_account_code(
    customer_country_code: Optional[str],
    pickup_country_code: Optional[str],
    settings,
) -> Optional[str]:
    customer = (customer_country_code or "").strip().upper()
    pickup_country = (pickup_country_code or "").strip().upper()

    if pickup_country == "US":
        if customer == "US":
            return getattr(settings, "easirent_account_us_domestic", None)
        return getattr(settings, "easirent_account_us_inbound", None)

    if pickup_country == "ROI":
        if customer == "US":
            return None
        return getattr(settings, "easirent_account_roi", None)

    return None


def is_placeholder_vehicle_code(sipp_code: Optional[str]) -> bool:
    return (sipp_code or "").strip().upper() == "XXAR"
