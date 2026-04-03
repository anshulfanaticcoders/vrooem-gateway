"""Authentication for Provider API consumers (external companies)."""

import logging
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.provider_models import ApiConsumer, ApiKey
from app.db.mysql_session import get_mysql_db as get_db
from app.services.provider_key_service import find_by_plaintext

logger = logging.getLogger(__name__)

provider_api_key_header = APIKeyHeader(
    name="X-Api-Key",
    description="API key for Provider API authentication. Obtain from Vrooem admin.",
    auto_error=False,
)


class ProviderAuthContext:
    """Holds the authenticated consumer and key for the current request."""

    def __init__(self, consumer: ApiConsumer, api_key: ApiKey):
        self.consumer = consumer
        self.api_key = api_key

    @property
    def is_sandbox(self) -> bool:
        return self.consumer.mode == "sandbox"


async def verify_provider_api_key(
    request: Request,
    api_key_value: str | None = Security(provider_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> ProviderAuthContext:
    """Validate Provider API key and return auth context."""
    if not api_key_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "INVALID_API_KEY", "message": "Missing X-Api-Key header.", "status": 401}},
        )

    api_key, consumer = await find_by_plaintext(db, api_key_value)

    if not api_key or not consumer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "INVALID_API_KEY", "message": "The provided API key is invalid or has been revoked.", "status": 401}},
        )

    if consumer.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "CONSUMER_SUSPENDED", "message": "Your API access has been suspended. Contact support.", "status": 403}},
        )

    # Debounce last_used_at (update at most every 60s)
    now = time.time()
    if not api_key.last_used_at or (now - api_key.last_used_at.timestamp()) > 60:
        api_key.last_used_at = datetime.utcnow()
        await db.commit()

    return ProviderAuthContext(consumer=consumer, api_key=api_key)


def require_scope(scope: str):
    """Factory that returns a dependency checking the key has a specific scope."""

    async def _check(auth: ProviderAuthContext = Depends(verify_provider_api_key)):
        scopes = auth.api_key.scopes or []
        if scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "INSUFFICIENT_SCOPE", "message": f"Your API key does not have the '{scope}' permission.", "status": 403}},
            )
        return auth

    return _check
