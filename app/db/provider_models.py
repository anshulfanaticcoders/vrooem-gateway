"""SQLAlchemy models for Provider API tables (stored in MySQL/Laravel database)."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class MySQLBase(DeclarativeBase):
    """Separate base for MySQL-backed Provider API models."""
    pass


class ApiConsumer(MySQLBase):
    """External companies consuming the Provider API."""

    __tablename__ = "api_consumers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    contact_name = Column(String(255), nullable=False)
    contact_email = Column(String(255), unique=True, nullable=False)
    contact_phone = Column(String(50), nullable=True)
    company_url = Column(String(255), nullable=True)
    status = Column(String(20), default="active", nullable=False)
    mode = Column(String(10), default="sandbox", nullable=False)
    plan = Column(String(20), default="basic", nullable=False)
    rate_limit = Column(Integer, default=60, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    keys = relationship("ApiKey", back_populates="consumer", cascade="all, delete-orphan")


class ApiKey(MySQLBase):
    """Hashed API keys for Provider API authentication."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_consumer_id = Column(Integer, ForeignKey("api_consumers.id", ondelete="CASCADE"), nullable=False)
    key_hash = Column(String(64), unique=True, nullable=False)
    key_prefix = Column(String(12), nullable=False)
    name = Column(String(255), nullable=False)
    status = Column(String(20), default="active", nullable=False)
    scopes = Column(JSON, default=list)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    consumer = relationship("ApiConsumer", back_populates="keys")


class ProviderApiLog(MySQLBase):
    """Log of every Provider API request from external consumers."""

    __tablename__ = "api_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_consumer_id = Column(Integer, nullable=False)
    api_key_id = Column(Integer, nullable=False)
    method = Column(String(10), nullable=False)
    endpoint = Column(String(255), nullable=False)
    request_payload = Column(JSON, nullable=True)
    response_status = Column(SmallInteger, nullable=False)
    ip_address = Column(String(45), nullable=False)
    user_agent = Column(String(500), nullable=True)
    processing_time_ms = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
