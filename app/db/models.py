"""SQLAlchemy models for gateway database tables."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ProviderLocation(Base):
    """Raw location data from each provider (before unification)."""

    __tablename__ = "provider_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False)
    provider_location_id = Column(String(255), nullable=False)
    name = Column(String(500), nullable=False)
    name_norm = Column(String(500), nullable=False, default="")
    city = Column(String(255), default="")
    city_norm = Column(String(255), default="")
    country = Column(String(255), default="")
    country_code = Column(String(10), default="")
    country_norm = Column(String(255), default="")
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    location_type = Column(String(50), default="other")
    type_norm = Column(String(50), default="other")
    iata = Column(String(10), nullable=True)
    iata_norm = Column(String(10), nullable=True)
    geohash = Column(String(12), default="")
    raw_data = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    last_synced_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)
    sync_status = Column(String(20), default="active")
    provider_payload_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_provider_loc_unique", "provider", "provider_location_id", unique=True),
        Index("ix_provider_loc_country_city", "provider", "country_norm", "city_norm"),
        Index("ix_provider_loc_iata", "provider", "iata_norm"),
    )


class LocationSyncRun(Base):
    """Tracks each provider location sync attempt."""

    __tablename__ = "location_sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False)
    status = Column(String(20), default="success")
    locations_received = Column(Integer, default=0)
    locations_upserted = Column(Integer, default=0)
    locations_deactivated = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_location_sync_provider_started", "provider", "started_at"),
    )


class UnifiedLocation(Base):
    """Unified/merged location records — the canonical locations."""

    __tablename__ = "unified_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    unified_location_id = Column(BigInteger, unique=True, nullable=False, index=True)
    match_key = Column(String(500), unique=True, nullable=False)
    name = Column(String(500), nullable=False)
    aliases = Column(JSON, default=list)
    city = Column(String(255), default="")
    country = Column(String(255), default="")
    country_code = Column(String(10), default="")
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    location_type = Column(String(50), default="other")
    iata = Column(String(10), nullable=True)
    confidence = Column(Float, default=1.0)
    is_manual = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    our_location_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mappings = relationship("UnifiedLocationMapping", back_populates="unified_location")

    __table_args__ = (
        Index("ix_unified_loc_country_city", "country", "city"),
        Index("ix_unified_loc_iata", "iata"),
    )


class UnifiedLocationMapping(Base):
    """Maps provider locations to unified locations."""

    __tablename__ = "unified_location_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    unified_location_id = Column(
        BigInteger, ForeignKey("unified_locations.unified_location_id"), nullable=False
    )
    provider = Column(String(50), nullable=False)
    provider_location_id = Column(String(255), nullable=False)
    original_name = Column(String(500), default="")
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    dropoffs = Column(JSON, default=list)
    status = Column(String(20), default="auto")  # auto, manual, blocked
    created_at = Column(DateTime, default=datetime.utcnow)

    unified_location = relationship("UnifiedLocation", back_populates="mappings")

    __table_args__ = (
        Index("ix_mapping_provider_unique", "provider", "provider_location_id", unique=True),
        Index("ix_mapping_unified_id", "unified_location_id"),
    )


class GatewayBooking(Base):
    """Booking records stored in the gateway database."""

    __tablename__ = "gateway_bookings"

    id = Column(String(50), primary_key=True)  # bk_<uuid>
    supplier_id = Column(String(50), nullable=False)
    supplier_booking_id = Column(String(255), default="")
    laravel_booking_id = Column(Integer, nullable=True)
    status = Column(String(20), default="pending")
    vehicle_name = Column(String(500), default="")
    pickup_location = Column(String(500), default="")
    dropoff_location = Column(String(500), default="")
    pickup_datetime = Column(DateTime, nullable=True)
    dropoff_datetime = Column(DateTime, nullable=True)
    total_price = Column(Float, default=0)
    currency = Column(String(10), default="EUR")
    driver_email = Column(String(255), default="")
    request_data = Column(JSON, default=dict)
    response_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_booking_supplier", "supplier_id", "supplier_booking_id"),
        Index("ix_booking_laravel", "laravel_booking_id"),
    )


class ApiLog(Base):
    """Log of every API call to/from providers."""

    __tablename__ = "api_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    supplier_id = Column(String(50), nullable=False)
    direction = Column(String(10), default="outbound")  # outbound (to provider), inbound (webhook)
    method = Column(String(10), default="GET")
    url = Column(Text, default="")
    request_headers = Column(JSON, default=dict)
    request_body = Column(Text, default="")
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, default="")
    response_time_ms = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    search_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_api_log_supplier", "supplier_id", "created_at"),
        Index("ix_api_log_search", "search_id"),
    )
