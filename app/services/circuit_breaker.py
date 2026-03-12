"""Per-supplier circuit breaker to handle provider failures gracefully."""

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal — requests pass through
    OPEN = "open"  # Broken — requests blocked
    HALF_OPEN = "half_open"  # Testing — one request allowed


class CircuitBreaker:
    """Circuit breaker for a single supplier.

    - CLOSED: All requests pass. If failures hit threshold → OPEN.
    - OPEN: All requests blocked. After recovery_timeout → HALF_OPEN.
    - HALF_OPEN: One test request allowed. Success → CLOSED, failure → OPEN.
    """

    def __init__(
        self,
        supplier_id: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ):
        self.supplier_id = supplier_id
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0
        self.last_success_time: float = 0

    @property
    def is_available(self) -> bool:
        """Check if requests should be allowed through."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("[%s] Circuit → HALF_OPEN (testing)", self.supplier_id)
                return True
            return False
        # HALF_OPEN — allow one test request
        return True

    def record_success(self) -> None:
        """Record a successful request."""
        self.failure_count = 0
        self.last_success_time = time.time()
        if self.state != CircuitState.CLOSED:
            logger.info("[%s] Circuit → CLOSED (recovered)", self.supplier_id)
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "[%s] Circuit → OPEN (%d failures)",
                self.supplier_id,
                self.failure_count,
            )

    def to_dict(self) -> dict:
        """Serialize state for API/dashboard."""
        return {
            "supplier_id": self.supplier_id,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self.last_failure_time or None,
            "last_success_time": self.last_success_time or None,
        }


class CircuitBreakerRegistry:
    """Manages circuit breakers for all suppliers."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self._breakers: dict[str, CircuitBreaker] = {}
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

    def get(self, supplier_id: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a supplier."""
        if supplier_id not in self._breakers:
            self._breakers[supplier_id] = CircuitBreaker(
                supplier_id=supplier_id,
                failure_threshold=self.failure_threshold,
                recovery_timeout=self.recovery_timeout,
            )
        return self._breakers[supplier_id]

    def all_states(self) -> list[dict]:
        """Get state of all circuit breakers."""
        return [cb.to_dict() for cb in self._breakers.values()]
