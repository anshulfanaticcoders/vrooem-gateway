"""API key management for Provider API consumers."""

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.provider_models import ApiConsumer, ApiKey

logger = logging.getLogger(__name__)

PREFIX = "vrm_live_"


async def generate_key(db: AsyncSession, consumer_id: int, name: str = "Default") -> tuple[ApiKey, str]:
    """Generate a new API key. Returns (ApiKey model, plaintext key shown once)."""
    plaintext = PREFIX + secrets.token_hex(20)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    scopes = [
        "locations:read", "vehicles:search", "vehicles:extras",
        "bookings:create", "bookings:read", "bookings:cancel",
    ]

    api_key = ApiKey(
        api_consumer_id=consumer_id,
        key_hash=key_hash,
        key_prefix=plaintext[:12],
        name=name,
        status="active",
        scopes=scopes,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    logger.info("Generated API key for consumer %d: %s...", consumer_id, plaintext[:12])
    return api_key, plaintext


async def find_by_plaintext(db: AsyncSession, plaintext: str) -> tuple[ApiKey | None, ApiConsumer | None]:
    """Look up an active key by its plaintext value. Returns (key, consumer) or (None, None)."""
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.status == "active")
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        return None, None

    # Check expiry
    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        api_key.status = "expired"
        await db.commit()
        return None, None

    # Load consumer
    result = await db.execute(
        select(ApiConsumer).where(ApiConsumer.id == api_key.api_consumer_id)
    )
    consumer = result.scalar_one_or_none()

    return api_key, consumer


async def rotate_key(db: AsyncSession, api_key: ApiKey) -> tuple[ApiKey, str]:
    """Revoke old key and issue new one with same scopes."""
    api_key.status = "revoked"
    api_key.revoked_at = datetime.utcnow()

    plaintext = PREFIX + secrets.token_hex(20)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    new_key = ApiKey(
        api_consumer_id=api_key.api_consumer_id,
        key_hash=key_hash,
        key_prefix=plaintext[:12],
        name=api_key.name,
        status="active",
        scopes=api_key.scopes,
    )
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)

    logger.info("Rotated API key for consumer %d: %s...", api_key.api_consumer_id, plaintext[:12])
    return new_key, plaintext


async def revoke_key(db: AsyncSession, api_key: ApiKey) -> None:
    """Revoke a key immediately."""
    api_key.status = "revoked"
    api_key.revoked_at = datetime.utcnow()
    await db.commit()
    logger.info("Revoked API key %d for consumer %d", api_key.id, api_key.api_consumer_id)
