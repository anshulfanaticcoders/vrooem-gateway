"""Vrooem Gateway — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.registry import close_all_adapters, load_supplier_configs
from app.api.v1 import bookings, health, locations, search, suppliers

# Import adapters so they auto-register via @register_adapter
import app.adapters.green_motion  # noqa: F401
import app.adapters.usave  # noqa: F401
import app.adapters.renteon  # noqa: F401
import app.adapters.favrica  # noqa: F401
import app.adapters.xdrive  # noqa: F401
import app.adapters.adobe_car  # noqa: F401
import app.adapters.ok_mobility  # noqa: F401
import app.adapters.locauto_rent  # noqa: F401
import app.adapters.wheelsys  # noqa: F401
import app.adapters.surprice  # noqa: F401
import app.adapters.sicily_by_car  # noqa: F401
import app.adapters.recordgo  # noqa: F401
import app.adapters.internal  # noqa: F401
from app.core.config import get_settings
from app.core.exceptions import GatewayError, gateway_error_handler
from app.db.session import close_db
from app.services.cache_service import close_redis
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
    await close_all_adapters()
    await close_redis()
    await close_db()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Vrooem Gateway",
        description="Unified car rental provider gateway",
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

    # Routers
    app.include_router(health.router)
    app.include_router(suppliers.router)
    app.include_router(locations.router)
    app.include_router(search.router)
    app.include_router(bookings.router)

    return app


app = create_app()
