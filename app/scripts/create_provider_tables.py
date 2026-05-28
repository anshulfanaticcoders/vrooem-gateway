"""Create Provider API tables in MySQL."""

import asyncio

from app.db.mysql_session import get_mysql_engine
from app.db.provider_models import ApiConsumer, ApiKey, MySQLBase, ProviderApiLog


async def create_tables():
    engine = get_mysql_engine()
    async with engine.begin() as conn:
        await conn.run_sync(
            MySQLBase.metadata.create_all,
            tables=[
                ApiConsumer.__table__,
                ApiKey.__table__,
                ProviderApiLog.__table__,
            ],
        )
    print("Provider API tables created successfully.")


if __name__ == "__main__":
    asyncio.run(create_tables())
