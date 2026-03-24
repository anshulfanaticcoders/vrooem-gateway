"""Integration tests: SIPP validation on Vehicle construction.

Verifies that when any adapter builds a Vehicle with a SIPP code,
the model validator automatically corrects/validates specs.
"""

from app.schemas.common import FuelType, MileagePolicy, TransmissionType
from app.schemas.pricing import Pricing
from app.schemas.vehicle import Vehicle


def _make_pricing() -> Pricing:
    return Pricing(total_price=100.0, daily_rate=20.0, currency="EUR")


def test_vehicle_with_sipp_overrides_wrong_transmission():
    """Provider says manual but SIPP 3rd char 'A' = automatic."""
    v = Vehicle(
        id="gw_test",
        supplier_id="surprice",
        supplier_vehicle_id="123",
        name="Toyota Aygo or similar",
        sipp_code="EDMR",  # E=Economy, D=4-5door, M=Manual, R=AC+Petrol
        transmission=TransmissionType.AUTOMATIC,  # Wrong — SIPP says Manual
        pricing=_make_pricing(),
    )
    assert v.transmission == TransmissionType.MANUAL  # SIPP wins


def test_vehicle_with_sipp_drops_wrong_doors():
    """Provider says 5 doors but SIPP body 'B' = 2-3 door."""
    v = Vehicle(
        id="gw_test",
        supplier_id="surprice",
        supplier_vehicle_id="123",
        name="Toyota Aygo or similar",
        sipp_code="EBMR",  # B body = 2-3 door
        doors=5,  # Wrong
        pricing=_make_pricing(),
    )
    assert v.doors is None  # Dropped — contradicts SIPP


def test_vehicle_with_sipp_keeps_valid_doors():
    """Provider says 4 doors, SIPP body 'D' = 4-5 door → kept."""
    v = Vehicle(
        id="gw_test",
        supplier_id="renteon",
        supplier_vehicle_id="456",
        name="VW Golf or similar",
        sipp_code="CDMR",  # D body = 4-5 door
        doors=4,
        pricing=_make_pricing(),
    )
    assert v.doors == 4


def test_vehicle_with_sipp_fills_missing_fuel():
    """Provider didn't set fuel but SIPP 4th char 'D' = Diesel."""
    v = Vehicle(
        id="gw_test",
        supplier_id="ok_mobility",
        supplier_vehicle_id="789",
        name="Seat Leon or similar",
        sipp_code="CDAD",  # D = AC + Diesel
        pricing=_make_pricing(),
    )
    assert v.fuel_type == FuelType.DIESEL
    assert v.air_conditioning is True
    assert v.transmission == TransmissionType.AUTOMATIC


def test_vehicle_with_sipp_fills_missing_ac():
    """Provider didn't set AC but SIPP says AC."""
    v = Vehicle(
        id="gw_test",
        supplier_id="recordgo",
        supplier_vehicle_id="abc",
        name="Fiat 500 or similar",
        sipp_code="MBMR",  # R = AC + Petrol
        pricing=_make_pricing(),
    )
    assert v.air_conditioning is True
    assert v.fuel_type == FuelType.PETROL


def test_vehicle_without_sipp_passes_all_through():
    """No SIPP code — all provider values pass through unchanged."""
    v = Vehicle(
        id="gw_test",
        supplier_id="internal",
        supplier_vehicle_id="int1",
        name="Toyota Corolla",
        doors=5,
        seats=5,
        transmission=TransmissionType.MANUAL,
        fuel_type=FuelType.DIESEL,
        air_conditioning=True,
        pricing=_make_pricing(),
    )
    assert v.doors == 5
    assert v.seats == 5
    assert v.transmission == TransmissionType.MANUAL
    assert v.fuel_type == FuelType.DIESEL
    assert v.air_conditioning is True


def test_vehicle_sipp_suv_accepts_5_doors():
    """SUV body type (F) allows 4-5 doors."""
    v = Vehicle(
        id="gw_test",
        supplier_id="wheelsys",
        supplier_vehicle_id="suv1",
        name="Nissan Qashqai or similar",
        sipp_code="SFAR",  # F = SUV
        doors=5,
        pricing=_make_pricing(),
    )
    assert v.doors == 5  # Valid for SUV


def test_vehicle_sipp_coupe_rejects_4_doors():
    """Coupe body type (E) allows 2-3 doors only."""
    v = Vehicle(
        id="gw_test",
        supplier_id="surprice",
        supplier_vehicle_id="cp1",
        name="BMW 2 Series or similar",
        sipp_code="PEMR",  # E = Coupe
        doors=4,  # Wrong for coupe
        pricing=_make_pricing(),
    )
    assert v.doors is None  # Dropped


def test_vehicle_sipp_diesel_no_ac():
    """SIPP 'Q' = No AC + Diesel."""
    v = Vehicle(
        id="gw_test",
        supplier_id="locauto_rent",
        supplier_vehicle_id="loc1",
        name="Fiat Panda or similar",
        sipp_code="EDMQ",
        air_conditioning=True,  # Wrong — SIPP says no AC
        pricing=_make_pricing(),
    )
    assert v.air_conditioning is False  # SIPP wins
    assert v.fuel_type == FuelType.DIESEL


def test_multiple_providers_same_sipp_same_result():
    """Same SIPP code gives same validated specs regardless of provider."""
    sipp = "CDAR"  # Compact, 4-5 door, Automatic, AC+Petrol
    providers = ["surprice", "renteon", "recordgo", "ok_mobility", "sicily_by_car"]
    for provider in providers:
        v = Vehicle(
            id=f"gw_{provider}",
            supplier_id=provider,
            supplier_vehicle_id=f"{provider}_1",
            name="VW Golf or similar",
            sipp_code=sipp,
            doors=4,
            pricing=_make_pricing(),
        )
        assert v.transmission == TransmissionType.AUTOMATIC, f"{provider}: wrong transmission"
        assert v.fuel_type == FuelType.PETROL, f"{provider}: wrong fuel"
        assert v.air_conditioning is True, f"{provider}: wrong AC"
        assert v.doors == 4, f"{provider}: wrong doors"
