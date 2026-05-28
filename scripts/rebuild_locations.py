"""Backward-compatible alias for the live provider location JSON refresh."""

from __future__ import annotations

import asyncio

from app.scripts.refresh_locations_json import main


if __name__ == "__main__":
    asyncio.run(main())
