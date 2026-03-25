"""Common types and enums used across all schemas."""

from enum import Enum


class TransmissionType(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class FuelType(str, Enum):
    PETROL = "petrol"
    DIESEL = "diesel"
    ELECTRIC = "electric"
    HYBRID = "hybrid"
    LPG = "lpg"
    UNKNOWN = "unknown"


class LocationType(str, Enum):
    AIRPORT = "airport"
    DOWNTOWN = "downtown"
    PORT = "port"
    TRAIN_STATION = "train_station"
    BUS_STATION = "bus_station"
    HOTEL = "hotel"
    OTHER = "other"


class VehicleCategory(str, Enum):
    MINI = "mini"
    ECONOMY = "economy"
    COMPACT = "compact"
    INTERMEDIATE = "intermediate"
    STANDARD = "standard"
    FULLSIZE = "fullsize"
    PREMIUM = "premium"
    LUXURY = "luxury"
    SUV = "suv"
    VAN = "van"
    OTHER = "other"


class PaymentOption(str, Enum):
    PAY_NOW = "pay_now"
    PAY_AT_PICKUP = "pay_at_pickup"


class CoverageType(str, Enum):
    BASIC = "basic"
    STANDARD = "standard"
    FULL = "full"
    PREMIUM = "premium"


class ExtraType(str, Enum):
    EQUIPMENT = "equipment"
    SERVICE = "service"
    INSURANCE = "insurance"
    FEE = "fee"


class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


class MileagePolicy(str, Enum):
    UNLIMITED = "unlimited"
    LIMITED = "limited"


# SIPP first-letter → category mapping
SIPP_CATEGORY_MAP: dict[str, VehicleCategory] = {
    "M": VehicleCategory.MINI,
    "N": VehicleCategory.MINI,
    "E": VehicleCategory.ECONOMY,
    "H": VehicleCategory.ECONOMY,
    "C": VehicleCategory.COMPACT,
    "D": VehicleCategory.COMPACT,
    "I": VehicleCategory.INTERMEDIATE,
    "J": VehicleCategory.INTERMEDIATE,
    "S": VehicleCategory.STANDARD,
    "R": VehicleCategory.STANDARD,
    "F": VehicleCategory.FULLSIZE,
    "G": VehicleCategory.FULLSIZE,
    "P": VehicleCategory.PREMIUM,
    "U": VehicleCategory.PREMIUM,
    "L": VehicleCategory.LUXURY,
    "W": VehicleCategory.LUXURY,
    "O": VehicleCategory.SUV,
    "X": VehicleCategory.SUV,
}


def category_from_sipp(sipp_code: str | None) -> VehicleCategory:
    """Derive vehicle category from first character of SIPP/ACRISS code."""
    if not sipp_code:
        return VehicleCategory.OTHER
    return SIPP_CATEGORY_MAP.get(sipp_code[0].upper(), VehicleCategory.OTHER)
