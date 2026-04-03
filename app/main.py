"""Vrooem Gateway — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.registry import close_all_adapters, load_supplier_configs
from app.api.v1 import bookings, health, locations, provider, search, suppliers

# Import adapters so they auto-register via @register_adapter
import app.adapters.green_motion  # noqa: F401
import app.adapters.usave  # noqa: F401
import app.adapters.renteon  # noqa: F401
import app.adapters.favrica  # noqa: F401
import app.adapters.xdrive  # noqa: F401
import app.adapters.emr  # noqa: F401
import app.adapters.adobe_car  # noqa: F401
import app.adapters.ok_mobility  # noqa: F401
import app.adapters.locauto_rent  # noqa: F401
import app.adapters.wheelsys  # noqa: F401
import app.adapters.surprice  # noqa: F401
import app.adapters.sicily_by_car  # noqa: F401
import app.adapters.recordgo  # noqa: F401
import app.adapters.internal  # noqa: F401
import app.adapters.click2rent  # noqa: F401
from app.core.config import get_settings
from app.core.exceptions import GatewayError, gateway_error_handler
from app.db.mysql_session import close_mysql
from app.db.session import close_db
from app.services.cache_service import close_redis
from app.services.provider_api_service import close_provider_api_service
from app.services.circuit_breaker import CircuitBreakerRegistry

logging.basicConfig(
    level=logging.DEBUG if get_settings().gateway_debug else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("Starting Vrooem Gateway...")

    # Load supplier configs from YAML
    configs = load_supplier_configs()
    logger.info("Loaded %d supplier configs", len(configs))

    # Initialize circuit breaker registry
    cb_registry = CircuitBreakerRegistry(failure_threshold=15, recovery_timeout=30)
    app.state.circuit_breakers = cb_registry
    suppliers.set_circuit_breaker_registry(cb_registry)
    search.set_circuit_breaker_registry(cb_registry)

    logger.info("Vrooem Gateway ready.")
    yield

    # Shutdown
    logger.info("Shutting down Vrooem Gateway...")
    await close_provider_api_service()
    await close_all_adapters()
    await close_redis()
    await close_db()
    await close_mysql()
    logger.info("Shutdown complete.")


def create_provider_app() -> FastAPI:
    """Separate FastAPI app for the Provider API — external companies see ONLY this."""
    provider_app = FastAPI(
        title="Vrooem Provider API",
        description=(
            "API for external platforms (OTAs, travel agencies, aggregators) "
            "to search Vrooem's vehicle inventory and create bookings.\n\n"
            "## Authentication\n"
            "All endpoints require an `X-Api-Key` header. "
            "Contact Vrooem to obtain your API key.\n\n"
            "## Rate Limits\n"
            "| Plan | Requests/min |\n"
            "|------|-------------|\n"
            "| Basic | 60 |\n"
            "| Premium | 500 |\n"
            "| Enterprise | 1,000 |\n\n"
            "Rate limit headers are included in every response: "
            "`X-RateLimit-Limit`, `X-RateLimit-Remaining`.\n\n"
            "## Support\n"
            "Contact integrations@vrooem.com for API key requests or technical support."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=[
            {
                "name": "Locations",
                "description": "Available pickup and dropoff locations.",
            },
            {
                "name": "Vehicles",
                "description": "Search vehicles and get extras/insurance options.",
            },
            {
                "name": "Bookings",
                "description": "Create, view, and cancel bookings.",
            },
        ],
    )

    provider_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    provider_app.include_router(provider.router)

    return provider_app


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Vrooem Gateway",
        description="Unified car rental provider gateway (internal)",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.gateway_debug else None,
        redoc_url="/redoc" if settings.gateway_debug else None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.gateway_debug else [settings.laravel_base_url],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    app.add_exception_handler(GatewayError, gateway_error_handler)

    # Internal gateway routers
    app.include_router(health.router)
    app.include_router(suppliers.router)
    app.include_router(locations.router)
    app.include_router(search.router)
    app.include_router(bookings.router)

    # Mount Provider API as separate app with its own docs
    app.mount("/provider", create_provider_app())

    return app


app = create_app()
