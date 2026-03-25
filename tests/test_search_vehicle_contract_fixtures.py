import json
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "search-vehicle-v1.schema.json"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "search_vehicle"
EXPECTED_FIXTURES = {
    "internal.json",
    "greenmotion.json",
    "usave.json",
    "okmobility.json",
    "surprice.json",
    "locauto_rent.json",
    "sicily_by_car.json",
    "recordgo.json",
    "renteon.json",
    "xdrive.json",
    "favrica.json",
    "adobe.json",
    "wheelsys.json",
}
UNKNOWN_SPEC_FLAG_MAP = {
    "unknown_transmission": ("specs", "transmission"),
    "unknown_fuel": ("specs", "fuel"),
    "unknown_seating_capacity": ("specs", "seating_capacity"),
    "unknown_doors": ("specs", "doors"),
    "unknown_luggage_small": ("specs", "luggage_small"),
    "unknown_luggage_medium": ("specs", "luggage_medium"),
    "unknown_luggage_large": ("specs", "luggage_large"),
    "unknown_air_conditioning": ("specs", "air_conditioning"),
    "unknown_mileage_policy": ("policies", "mileage_policy"),
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_provider_search_vehicle_fixtures_exist() -> None:
    assert FIXTURE_DIR.exists(), f"missing fixture directory: {FIXTURE_DIR}"

    fixture_names = {path.name for path in FIXTURE_DIR.glob("*.json")}
    assert EXPECTED_FIXTURES == fixture_names


def test_all_provider_search_vehicle_fixtures_validate_against_contract() -> None:
    schema = _load_json(CONTRACT_PATH)
    validator = Draft202012Validator(schema)

    for fixture_path in sorted(FIXTURE_DIR.glob("*.json")):
        payload = _load_json(fixture_path)
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        assert not errors, f"fixture {fixture_path.name} failed schema validation: {[error.message for error in errors]}"


def test_unknown_spec_flags_require_null_values() -> None:
    for fixture_path in sorted(FIXTURE_DIR.glob("*.json")):
        payload = _load_json(fixture_path)
        flags = set(payload.get("data_quality_flags", []))

        for flag, (section, key) in UNKNOWN_SPEC_FLAG_MAP.items():
            if flag in flags:
                assert payload[section][key] is None, (
                    f"fixture {fixture_path.name} marks {flag} but has non-null {section}.{key}"
                )
