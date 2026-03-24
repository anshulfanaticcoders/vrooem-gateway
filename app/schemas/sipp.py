"""SIPP/ACRISS code spec deriver — industry-standard source of truth.

SIPP is a 4-character code: [Category][Body][Transmission][Fuel/AC]
This module derives reliable specs from SIPP and validates provider data against it.
"""

from __future__ import annotations

from app.schemas.common import FuelType, TransmissionType


# ── Char 2: Body type → door range ──────────────────────────────────────────

# Maps SIPP 2nd character to (min_doors, max_doors).
# If provider-reported doors falls outside this range, we drop it.
SIPP_BODY_DOOR_RANGE: dict[str, tuple[int, int]] = {
    "B": (2, 3),   # 2-3 door
    "C": (2, 4),   # 2/4 door
    "D": (4, 5),   # 4-5 door
    "W": (4, 5),   # Wagon/Estate
    "V": (3, 9),   # Passenger Van (variable)
    "L": (4, 4),   # Limousine
    "S": (2, 3),   # Sport
    "T": (2, 2),   # Convertible
    "F": (4, 5),   # SUV
    "J": (2, 5),   # Open Off-road
    "X": (2, 9),   # Special
    "P": (2, 4),   # Pick-up
    "Q": (2, 4),   # Pick-up Extended cab
    "E": (2, 3),   # Coupe
    "M": (5, 5),   # Monospace / MPV
    "R": (2, 9),   # Recreational Vehicle
    "H": (2, 9),   # Motor Home
    "N": (2, 2),   # Roadster
    "G": (4, 5),   # Crossover
    "K": (2, 9),   # Commercial Van/Truck
}


# ── Char 3: Transmission ────────────────────────────────────────────────────

SIPP_TRANSMISSION: dict[str, TransmissionType] = {
    "M": TransmissionType.MANUAL,
    "N": TransmissionType.MANUAL,     # Manual AWD
    "C": TransmissionType.MANUAL,     # Manual 4WD
    "A": TransmissionType.AUTOMATIC,
    "B": TransmissionType.AUTOMATIC,  # Automatic 4WD
    "D": TransmissionType.AUTOMATIC,  # AWD
}


# ── Char 4: Fuel type + AC ─────────────────────────────────────────────────

SIPP_FUEL: dict[str, FuelType] = {
    "R": FuelType.PETROL,    # AC + Petrol
    "N": FuelType.PETROL,    # No AC + Petrol
    "D": FuelType.DIESEL,    # AC + Diesel
    "Q": FuelType.DIESEL,    # No AC + Diesel
    "H": FuelType.HYBRID,    # AC + Hybrid
    "I": FuelType.HYBRID,    # AC + Hybrid Plug-in
    "E": FuelType.ELECTRIC,  # AC + Electric
    "C": FuelType.PETROL,    # AC + Compressed Gas (map to petrol)
    "L": FuelType.LPG,       # AC + LPG/Compressed Gas
    "S": FuelType.PETROL,    # AC + Petrol (alternative code)
    "A": FuelType.HYBRID,    # AC + Hydrogen/Hydrogen fuel cell
    "B": FuelType.ELECTRIC,  # AC + Electric (alternative)
    "M": FuelType.PETROL,    # Multi fuel / No AC
    "F": FuelType.PETROL,    # Multi fuel / AC
    "V": FuelType.PETROL,    # Petrol (alternative)
    "Z": FuelType.PETROL,    # No AC + LPG
    "U": FuelType.ELECTRIC,  # No AC + Electric
    "X": FuelType.HYBRID,    # No AC + Hybrid
}

# 4th char → AC (True = has AC, False = no AC)
SIPP_AC: dict[str, bool] = {
    "R": True, "N": False, "D": True, "Q": False,
    "H": True, "I": True, "E": True, "C": True,
    "L": True, "S": True, "A": True, "B": True,
    "M": False, "F": True, "V": True, "Z": False,
    "U": False, "X": False,
}


def derive_transmission(sipp: str | None) -> TransmissionType | None:
    """Derive transmission from SIPP 3rd character."""
    if not sipp or len(sipp) < 3:
        return None
    return SIPP_TRANSMISSION.get(sipp[2].upper())


def derive_fuel(sipp: str | None) -> FuelType | None:
    """Derive fuel type from SIPP 4th character."""
    if not sipp or len(sipp) < 4:
        return None
    return SIPP_FUEL.get(sipp[3].upper())


def derive_ac(sipp: str | None) -> bool | None:
    """Derive air conditioning from SIPP 4th character."""
    if not sipp or len(sipp) < 4:
        return None
    return SIPP_AC.get(sipp[3].upper())


def validate_doors(sipp: str | None, doors: int | None) -> int | None:
    """Validate provider-reported doors against SIPP body type.

    Returns the door count if it's plausible, None if it contradicts SIPP.
    If no SIPP is available, returns the original value unchanged.
    """
    if doors is None:
        return None
    if not sipp or len(sipp) < 2:
        return doors
    body = sipp[1].upper()
    door_range = SIPP_BODY_DOOR_RANGE.get(body)
    if door_range is None:
        return doors  # unknown body type, trust provider
    min_d, max_d = door_range
    if min_d <= doors <= max_d:
        return doors
    # Provider doors contradicts SIPP — drop it
    return None


def apply_sipp_specs(
    sipp: str | None,
    *,
    transmission: TransmissionType | None = None,
    fuel_type: FuelType | None = None,
    air_conditioning: bool | None = None,
    doors: int | None = None,
    seats: int | None = None,
) -> dict:
    """Apply SIPP-derived specs as validation/fill layer.

    Rules:
    - transmission: SIPP wins if available (most reliable)
    - fuel_type: SIPP wins if available
    - air_conditioning: SIPP wins if available
    - doors: validate against SIPP body type, drop if contradictory
    - seats: pass through (SIPP doesn't encode seats)
    """
    sipp_trans = derive_transmission(sipp)
    sipp_fuel = derive_fuel(sipp)
    sipp_ac = derive_ac(sipp)
    validated_doors = validate_doors(sipp, doors)

    return {
        "transmission": sipp_trans if sipp_trans is not None else transmission,
        "fuel_type": sipp_fuel if sipp_fuel is not None else fuel_type,
        "air_conditioning": sipp_ac if sipp_ac is not None else air_conditioning,
        "doors": validated_doors,
        "seats": seats,
    }
