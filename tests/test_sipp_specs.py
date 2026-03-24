"""Tests for SIPP/ACRISS spec derivation and validation."""

from app.schemas.common import FuelType, TransmissionType
from app.schemas.sipp import (
    apply_sipp_specs,
    derive_ac,
    derive_fuel,
    derive_transmission,
    validate_doors,
)


# ── Transmission ────────────────────────────────────────────────────────────

def test_sipp_manual_transmission():
    assert derive_transmission("EDMR") == TransmissionType.MANUAL
    assert derive_transmission("CCMR") == TransmissionType.MANUAL

def test_sipp_automatic_transmission():
    assert derive_transmission("CDAR") == TransmissionType.AUTOMATIC
    assert derive_transmission("IDAR") == TransmissionType.AUTOMATIC

def test_sipp_no_transmission_for_short_code():
    assert derive_transmission("ED") is None
    assert derive_transmission(None) is None
    assert derive_transmission("") is None


# ── Fuel ────────────────────────────────────────────────────────────────────

def test_sipp_petrol():
    assert derive_fuel("EDMR") == FuelType.PETROL
    assert derive_fuel("EDAN") == FuelType.PETROL

def test_sipp_diesel():
    assert derive_fuel("CDMD") == FuelType.DIESEL
    assert derive_fuel("CDMQ") == FuelType.DIESEL

def test_sipp_electric():
    assert derive_fuel("EDAE") == FuelType.ELECTRIC

def test_sipp_hybrid():
    assert derive_fuel("CDAH") == FuelType.HYBRID

def test_sipp_no_fuel_for_short_code():
    assert derive_fuel("EDM") is None
    assert derive_fuel(None) is None


# ── Air Conditioning ────────────────────────────────────────────────────────

def test_sipp_ac_present():
    assert derive_ac("EDMR") is True   # R = AC + Petrol
    assert derive_ac("CDAD") is True   # D = AC + Diesel

def test_sipp_no_ac():
    assert derive_ac("EDMN") is False  # N = No AC + Petrol
    assert derive_ac("CDMQ") is False  # Q = No AC + Diesel

def test_sipp_ac_unknown_for_short():
    assert derive_ac("EDM") is None
    assert derive_ac(None) is None


# ── Door validation ─────────────────────────────────────────────────────────

def test_doors_valid_for_body_type():
    """SIPP 'D' body = 4-5 door, provider says 4 → keep."""
    assert validate_doors("CDMR", 4) == 4
    assert validate_doors("CDMR", 5) == 5

def test_doors_invalid_for_body_type():
    """SIPP 'B' body = 2-3 door, provider says 5 → drop."""
    assert validate_doors("CBMR", 5) is None

def test_doors_sport_body_rejects_5():
    """SIPP 'S' body = sport = 2-3 door, provider says 5 → drop."""
    assert validate_doors("CSMR", 5) is None

def test_doors_suv_accepts_5():
    """SIPP 'F' body = SUV = 4-5 door, provider says 5 → keep."""
    assert validate_doors("CFAR", 5) == 5

def test_doors_no_sipp_passes_through():
    """No SIPP → trust provider."""
    assert validate_doors(None, 5) == 5
    assert validate_doors("", 4) == 4

def test_doors_none_stays_none():
    assert validate_doors("CDMR", None) is None

def test_doors_2door_coupe():
    """SIPP 'E' body = coupe = 2-3 door, provider says 4 → drop."""
    assert validate_doors("CEMR", 4) is None

def test_doors_4door_wagon():
    """SIPP 'W' body = wagon = 4-5 door, provider says 4 → keep."""
    assert validate_doors("CWMR", 4) == 4

def test_doors_monospace_5door():
    """SIPP 'M' body = monospace/MPV = 5 door only."""
    assert validate_doors("CMMR", 5) == 5
    assert validate_doors("CMMR", 4) is None


# ── Full apply_sipp_specs ───────────────────────────────────────────────────

def test_apply_fills_missing_specs():
    """SIPP fills in transmission/fuel/AC when adapter didn't set them."""
    result = apply_sipp_specs("CDAD")
    assert result["transmission"] == TransmissionType.AUTOMATIC
    assert result["fuel_type"] == FuelType.DIESEL
    assert result["air_conditioning"] is True

def test_apply_sipp_overrides_provider_transmission():
    """SIPP wins over provider for transmission (more reliable)."""
    result = apply_sipp_specs(
        "CDAR",
        transmission=TransmissionType.MANUAL,
        fuel_type=FuelType.DIESEL,
    )
    assert result["transmission"] == TransmissionType.AUTOMATIC  # SIPP wins
    assert result["fuel_type"] == FuelType.PETROL  # SIPP wins

def test_apply_sipp_validates_doors():
    """Provider says 5 doors but SIPP body B = 2-3 door → dropped."""
    result = apply_sipp_specs("CBMR", doors=5, seats=4)
    assert result["doors"] is None
    assert result["seats"] == 4  # seats passed through

def test_apply_sipp_keeps_valid_doors():
    """Provider says 4 doors, SIPP body D = 4-5 door → kept."""
    result = apply_sipp_specs("CDMR", doors=4)
    assert result["doors"] == 4

def test_apply_no_sipp_passes_everything_through():
    """Without SIPP, all provider values pass through."""
    result = apply_sipp_specs(
        None,
        transmission=TransmissionType.MANUAL,
        fuel_type=FuelType.DIESEL,
        air_conditioning=True,
        doors=5,
        seats=7,
    )
    assert result["transmission"] == TransmissionType.MANUAL
    assert result["fuel_type"] == FuelType.DIESEL
    assert result["air_conditioning"] is True
    assert result["doors"] == 5
    assert result["seats"] == 7
