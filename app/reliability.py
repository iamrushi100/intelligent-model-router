"""Bounded provider execution, retries, fallback, and per-instance breakers."""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
import time
from typing import Awaitable, Callable, Mapping

from app.model_config import ModelCatalog, ModelDefinition
from app.providers import (
    PermanentProviderFailure,
    Provider,
    ProviderConnectionFailure,
    ProviderFailure,
    ProviderTimeoutFailure,
)
from app.router import RoutingDecision, rank_models
from app.schemas import RouteRequest, Sensitivity


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """The provider circuit is open or another half-open trial is running."""


class ProviderUnavailableError(Exception):
    """No available eligible provider/model completed the request."""

    def __init__(
        self,
        status_code: int = 502,
        attempted_models: tuple[ModelDefinition, ...] = (),
        retry_count: int = 0,
        attempt_count: int = 0,
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self.attempted_models = attempted_models
        self.retry_count = retry_count
        self.attempt_count = attempt_count


class CircuitBreaker:
    """Thread-safe in-process breaker; one final result counts per retry sequence."""

    def __init__(
        self,
        threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be positive")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def acquire(self) -> None:
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if self._clock() - self._opened_at < self.cooldown_seconds:
                    raise CircuitOpenError
                self._state = CircuitState.HALF_OPEN
            if self._state is CircuitState.HALF_OPEN:
                if self._trial_in_flight:
                    raise CircuitOpenError
                self._trial_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._trial_in_flight = False

    def record_retryable_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state is CircuitState.HALF_OPEN or self._consecutive_failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()
            self._trial_in_flight = False

    def record_permanent_failure(self) -> None:
        # Permanent errors do not affect the counter; release a half-open permit.
        with self._lock:
            self._trial_in_flight = False


@dataclass(frozen=True)
class ProviderResult:
    output: str
    decision: RoutingDecision
    fallback_used: bool
    retry_count: int
    attempt_count: int
    attempted_models: tuple[ModelDefinition, ...]


@dataclass(frozen=True)
class _AttemptResult:
    output: str
    retry_count: int
    attempt_count: int
    attempted_models: tuple[ModelDefinition, ...]


class ProviderExecutor:
    """Execute a selected model and optionally one revalidated fallback."""

    def __init__(
        self,
        providers: Mapping[str, Provider],
        timeout_seconds: float = 15.0,
        retry_delay_seconds: float = 1.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.providers = dict(providers)
        self.timeout_seconds = timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self._sleep = sleeper
        self._clock = clock
        self._threshold = threshold
        self._cooldown_seconds = cooldown_seconds
        self._breakers: dict[int, CircuitBreaker] = {}
        self._providers_by_identity: dict[int, Provider] = {}
        self._registry_lock = Lock()

    @property
    def breakers(self) -> Mapping[int, CircuitBreaker]:
        return self._breakers

    def breaker_for(self, provider: Provider) -> CircuitBreaker:
        identity = id(provider)
        with self._registry_lock:
            if identity not in self._breakers:
                self._providers_by_identity[identity] = provider
                self._breakers[identity] = CircuitBreaker(
                    threshold=self._threshold,
                    cooldown_seconds=self._cooldown_seconds,
                    clock=self._clock,
                )
            return self._breakers[identity]

    async def execute(
        self,
        request: RouteRequest,
        effective: Sensitivity,
        catalog: ModelCatalog,
    ) -> ProviderResult:
        ranked = rank_models(request, effective, catalog)
        primary = ranked[0]
        attempted_model_ids = {primary.model.model_id}
        attempted_models: tuple[ModelDefinition, ...] = ()
        retry_count = 0
        attempt_count = 0
        primary_provider = self.providers.get(primary.model.model_id)
        if primary_provider is None:
            raise ProviderUnavailableError from None
        try:
            first = await self._execute_one(request, primary, primary_provider)
            return ProviderResult(
                first.output,
                primary,
                False,
                first.retry_count,
                first.attempt_count,
                first.attempted_models,
            )
        except CircuitOpenError as exc:
            failure: Exception = exc
        except ProviderFailure as exc:
            if not exc.retryable:
                raise
            failure = exc
            retry_count += getattr(exc, "retry_count", 0)
            attempt_count += getattr(exc, "attempt_count", 0)
        except _ExecutionFailure as exc:
            attempted_models = exc.attempted_models
            retry_count += exc.retry_count
            attempt_count += exc.attempt_count
            if not exc.failure.retryable:
                raise ProviderUnavailableError(
                    _failure_status(exc.failure), attempted_models, retry_count, attempt_count
                ) from None
            failure = exc.failure

        # Recompute the full policy/capability/context/cost candidate set before fallback.
        rechecked = rank_models(request, effective, catalog)
        fallback = next(
            (decision for decision in rechecked if decision.model.model_id not in attempted_model_ids),
            None,
        )
        if fallback is None:
            raise ProviderUnavailableError(
                _failure_status(failure), attempted_models, retry_count, attempt_count
            ) from None
        fallback_provider = self.providers.get(fallback.model.model_id)
        if fallback_provider is None:
            raise ProviderUnavailableError from None
        try:
            second = await self._execute_one(
                request,
                fallback,
                fallback_provider,
                retry_allowed=retry_count == 0,
            )
            return ProviderResult(
                second.output,
                fallback,
                True,
                retry_count + second.retry_count,
                attempt_count + second.attempt_count,
                attempted_models + second.attempted_models,
            )
        except (CircuitOpenError, ProviderFailure, _ExecutionFailure) as exc:
            second_models: tuple[ModelDefinition, ...] = ()
            if isinstance(exc, _ExecutionFailure):
                retry_count += exc.retry_count
                attempt_count += exc.attempt_count
                second_models = exc.attempted_models
                status_code = _failure_status(exc.failure)
            elif isinstance(exc, ProviderFailure):
                status_code = _failure_status(exc)
            else:
                status_code = 503
            raise ProviderUnavailableError(
                status_code,
                attempted_models + second_models,
                retry_count,
                attempt_count,
            ) from None

    async def _execute_one(
        self,
        request: RouteRequest,
        decision: RoutingDecision,
        provider: Provider,
        retry_allowed: bool = True,
    ) -> _AttemptResult:
        breaker = self.breaker_for(provider)
        breaker.acquire()
        attempts = 0
        retries = 0
        attempted_models: list[ModelDefinition] = []
        maximum_attempts = 2 if retry_allowed else 1
        while attempts < maximum_attempts:
            attempts += 1
            attempted_models.append(decision.model)
            try:
                output = await asyncio.wait_for(
                    provider.generate(request, decision.model), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                failure: ProviderFailure = ProviderTimeoutFailure()
            except ConnectionError:
                failure = ProviderConnectionFailure()
            except ProviderFailure as exc:
                failure = exc
            except asyncio.CancelledError:
                breaker.record_permanent_failure()
                raise
            except Exception:
                failure = PermanentProviderFailure()
            else:
                breaker.record_success()
                return _AttemptResult(output, retries, attempts, tuple(attempted_models))

            if failure.retryable and attempts == 1 and retry_allowed:
                retries = 1
                try:
                    await self._sleep(self.retry_delay_seconds)
                except asyncio.CancelledError:
                    breaker.record_permanent_failure()
                    raise
                continue

            # Count the logical sequence once, using only its final outcome.
            if failure.retryable:
                breaker.record_retryable_failure()
            else:
                breaker.record_permanent_failure()
            raise _ExecutionFailure(failure, retries, attempts, tuple(attempted_models)) from None
        raise AssertionError("bounded retry loop exhausted unexpectedly")


class _ExecutionFailure(Exception):
    def __init__(
        self,
        failure: ProviderFailure,
        retry_count: int,
        attempt_count: int,
        attempted_models: tuple[ModelDefinition, ...],
    ) -> None:
        super().__init__()
        self.failure = failure
        self.retry_count = retry_count
        self.attempt_count = attempt_count
        self.attempted_models = attempted_models


def _failure_status(failure: Exception) -> int:
    if isinstance(failure, CircuitOpenError):
        return 503
    if isinstance(failure, ProviderTimeoutFailure):
        return 504
    return 502
