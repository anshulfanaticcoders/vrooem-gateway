"""Custom exceptions and global exception handlers."""

from fastapi import Request
from fastapi.responses import JSONResponse


class GatewayError(Exception):
    """Base gateway exception."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class SupplierError(GatewayError):
    """Error from a specific supplier API."""

    def __init__(self, supplier_id: str, message: str, status_code: int = 502):
        self.supplier_id = supplier_id
        super().__init__(f"[{supplier_id}] {message}", status_code)


class SupplierTimeoutError(SupplierError):
    """Supplier API timed out."""

    def __init__(self, supplier_id: str, timeout_seconds: float):
        super().__init__(supplier_id, f"Timed out after {timeout_seconds}s", 504)


class CircuitOpenError(GatewayError):
    """Circuit breaker is open for this supplier."""

    def __init__(self, supplier_id: str):
        super().__init__(f"Circuit breaker open for {supplier_id}", 503)


class VehicleNotFoundError(GatewayError):
    """Vehicle ID not found in cache or database."""

    def __init__(self, vehicle_id: str):
        super().__init__(f"Vehicle {vehicle_id} not found", 404)


class BookingError(GatewayError):
    """Error during booking creation/modification/cancellation."""

    pass


async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "type": type(exc).__name__},
    )
