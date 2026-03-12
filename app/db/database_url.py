"""Helpers for normalizing database URLs and optional SSL handling."""

from __future__ import annotations

import ssl


def database_url_requires_ssl(url: str) -> bool:
    lowered = url.lower()
    return "ssl=require" in lowered or "sslmode=require" in lowered


def build_connect_args(database_url: str) -> dict:
    """Build asyncpg connect_args only when SSL is explicitly required."""
    if not database_url_requires_ssl(database_url):
        return {}

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return {"ssl": ssl_ctx}


def clean_database_url(url: str) -> str:
    """Strip SSL query params that asyncpg receives via connect_args instead."""
    cleaned = url
    for suffix in ["?ssl=require", "&ssl=require", "?sslmode=require", "&sslmode=require"]:
        cleaned = cleaned.replace(suffix, "")
    return cleaned

