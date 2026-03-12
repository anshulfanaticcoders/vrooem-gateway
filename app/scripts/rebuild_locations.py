"""Backward-compatible alias for the live provider location sync."""

from __future__ import annotations

import asyncio

from app.scripts.sync_locations import main


if __name__ == "__main__":
    asyncio.run(main())
