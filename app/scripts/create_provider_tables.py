"""Create Provider API tables in PostgreSQL."""

import asyncio

from app.db.models import ApiConsumer, ApiKey, Base, ProviderApiLog
from app.db.session import get_engine


async def create_tables():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                ApiConsumer.__table__,
                ApiKey.__table__,
                ProviderApiLog.__table__,
            ],
        )
    print("Provider API tables created successfully.")


if __name__ == "__main__":
    asyncio.run(create_tables())
