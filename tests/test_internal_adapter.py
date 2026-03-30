from datetime import date, time

from app.adapters.internal import InternalAdapter, _extract_image_url
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest


def test_internal_adapter_auth_headers_include_gateway_token_fallback():
    adapter = InternalAdapter()

    headers = adapter._auth_headers()

    assert headers["Authorization"] == "Bearer laravel_internal_token"
    assert headers["X-Gateway-Token"] == "laravel_internal_token"
    assert headers["Accept"] == "application/json"


def test_extract_image_url_supports_laravel_internal_image_shape():
    raw = {
        "images": [
            {
                "image_type": "primary",
                "image_url": "https://example.com/internal-primary.jpg",
            },
            {
                "image_type": "gallery",
                "image_url": "https://example.com/internal-gallery.jpg",
            },
        ]
    }

    assert _extract_image_url(raw) == "https://example.com/internal-primary.jpg"


def test_extract_image_url_falls_back_to_first_laravel_internal_image():
    raw = {
        "images": [
            {
                "image_type": "gallery",
                "image_url": "https://example.com/internal-gallery.jpg",
            },
        ]
    }

    assert _extract_image_url(raw) == "https://example.com/internal-gallery.jpg"


def test_parse_vehicle_preserves_gallery_images_and_vendor_payload():
    adapter = InternalAdapter()
    request = SearchRequest(
        unified_location_id=123,
        pickup_date=date(2026, 6, 15),
        pickup_time=time(9, 0),
        dropoff_date=date(2026, 6, 18),
        dropoff_time=time(9, 0),
        currency="USD",
        country_code="BE",
        driver_age=35,
    )
    pickup = ProviderLocationEntry(
        provider="internal",
        pickup_id="2",
        original_name="Antwerp Downtown",
        latitude=51.2194,
        longitude=4.4025,
    )
    raw = {
        "id": 2,
        "vendor_id": 77,
        "category_id": 5,
        "brand": "Citroen",
        "model": "Berlingo",
        "transmission": "manual",
        "fuel": "diesel",
        "seating_capacity": 3,
        "doors": 4,
        "latitude": 51.2194,
        "longitude": 4.4025,
        "location": "70 Nijverheidsstraat, Antwerp, Belgium",
        "security_deposit": 500,
        "price_per_day": 72.68,
        "vendor": {
            "profile": {
                "city": "Antwerp",
                "country_code": "BE",
                "company_name": "Wagenverhuur",
            }
        },
        "vendorProfileData": {
            "company_name": "Wagenverhuur",
            "company_phone_number": "+3200000000",
        },
        "benefits": {
            "km_per_day": 250,
            "min_driver_age": 25,
            "cancellation": "Free cancellation up to 2 days before pickup",
        },
        "images": [
            {
                "image_type": "primary",
                "image_url": "https://example.com/internal-primary.jpg",
            },
            {
                "image_type": "gallery",
                "image_url": "https://example.com/internal-gallery-1.jpg",
            },
            {
                "image_type": "gallery",
                "image_url": "https://example.com/internal-gallery-2.jpg",
            },
        ],
    }

    vehicle = adapter._parse_vehicle(raw, request, rental_days=3, pickup_entry=pickup)

    assert vehicle is not None
    assert vehicle.image_url == "https://example.com/internal-primary.jpg"
    assert vehicle.supplier_data["vendorProfileData"]["company_name"] == "Wagenverhuur"
    assert vehicle.supplier_data["vendor_profile_data"]["company_name"] == "Wagenverhuur"
    assert len(vehicle.supplier_data["images"]) == 3
    assert vehicle.supplier_data["images"][1]["image_url"] == "https://example.com/internal-gallery-1.jpg"
