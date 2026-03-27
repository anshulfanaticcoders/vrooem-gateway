from app.adapters.internal import InternalAdapter, _extract_image_url


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
