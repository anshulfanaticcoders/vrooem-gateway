import json
from pathlib import Path


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "search-vehicle-v1.schema.json"


def test_search_vehicle_contract_schema_exists_and_defines_required_top_level_keys() -> None:
    assert CONTRACT_PATH.exists(), f"missing contract file: {CONTRACT_PATH}"

    schema = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    properties = set(schema["properties"])
    required = set(schema["required"])

    expected_top_level_keys = {
        "id",
        "gateway_vehicle_id",
        "provider_vehicle_id",
        "source",
        "provider_code",
        "display_name",
        "brand",
        "model",
        "category",
        "image",
        "specs",
        "pricing",
        "policies",
        "products",
        "extras_preview",
        "location",
        "data_quality_flags",
        "pricing_transparency_flags",
        "ui_placeholders",
        "booking_context",
    }

    assert expected_top_level_keys.issubset(properties), (
        f"missing top-level schema keys in properties: {sorted(expected_top_level_keys - properties)}"
    )
    assert expected_top_level_keys.issubset(required), (
        f"missing top-level schema keys in required: {sorted(expected_top_level_keys - required)}"
    )
