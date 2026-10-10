import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_config import ModelCatalog, ModelDefinition, load_model_catalog
from app.providers import (
    PermanentProviderFailure,
    ProviderHTTPFailure,
    ProviderTimeoutFailure,
    TransientProviderFailure,
)
from app.reliability import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ProviderExecutor,
    ProviderUnavailableError,
)
from app.router import rank_models
from app.schemas import RouteRequest, Sensitivity, TaskType


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedProvider:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    async def generate(self, request: RouteRequest, model: ModelDefinition) -> str:
        self.calls.append(model.model_id)
        outcome = self.outcomes.pop(0) if self.outcomes else "mock-ok"
        if isinstance(outcome, BaseException):
            raise outcome
        return str(outcome)


def one_model_catalog() -> ModelCatalog:
    catalog = load_model_catalog()
    return ModelCatalog(models=[catalog.model_by_id("mock-small")])


def make_settings(tmp_path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "api_keys": ("test-api-key",),
        "database_path": str(tmp_path / "audit.sqlite3"),
    }
    values.update(overrides)
    return Settings(**values)


def public_classification() -> RouteRequest:
    return RouteRequest(prompt="classify this", task_type="classification", sensitivity="public")


def make_large_model(model_id: str, price: float = 0.1) -> ModelDefinition:
    return ModelDefinition(
        model_id=model_id,
        tier="large",
        context_window=32_768,
        capabilities={TaskType.REASONING},
        input_price_per_million=price,
        output_price_per_million=price,
        approved_for={Sensitivity.PUBLIC},
        is_local=False,
    )


def test_transient_failure_retries_once_then_succeeds() -> None:
    provider = ScriptedProvider(TransientProviderFailure("private detail"), "ok")
    executor = ProviderExecutor(
        {"mock-small": provider}, timeout_seconds=1, retry_delay_seconds=0
    )

    result = run(executor.execute(public_classification(), Sensitivity.PUBLIC, one_model_catalog()))

    assert result.output == "ok"
    assert provider.calls == ["mock-small", "mock-small"]
    assert result.retry_count == 1
    assert result.attempt_count == 2
    breaker = executor.breaker_for(provider)
    assert breaker.consecutive_failures == 0


def test_second_transient_failure_stops_after_one_retry_and_counts_once() -> None:
    provider = ScriptedProvider(
        TransientProviderFailure("first"), TransientProviderFailure("second")
    )
    executor = ProviderExecutor(
        {"mock-small": provider}, timeout_seconds=1, retry_delay_seconds=0
    )

    with pytest.raises(ProviderUnavailableError) as error:
        run(executor.execute(public_classification(), Sensitivity.PUBLIC, one_model_catalog()))

    assert error.value.status_code == 502
    assert provider.calls == ["mock-small", "mock-small"]
    assert executor.breaker_for(provider).consecutive_failures == 1


@pytest.mark.parametrize(
    "failure",
    [PermanentProviderFailure("do not expose this"), ProviderHTTPFailure(400)],
)
def test_permanent_provider_failure_is_not_retried_or_fallbacked(failure: Exception) -> None:
    catalog = load_model_catalog()
    provider = ScriptedProvider(failure)
    other = ScriptedProvider("should not run")
    executor = ProviderExecutor(
        {"mock-small": provider, "mock-medium": other, "mock-large": other},
        timeout_seconds=1,
        retry_delay_seconds=0,
    )

    with pytest.raises(ProviderUnavailableError):
        run(executor.execute(public_classification(), Sensitivity.PUBLIC, catalog))

    assert provider.calls == ["mock-small"]
    assert other.calls == []
    assert executor.breaker_for(provider).consecutive_failures == 0


def test_http_error_retry_classification() -> None:
    assert ProviderHTTPFailure(408).retryable
    assert ProviderHTTPFailure(429).retryable
    assert ProviderHTTPFailure(503).retryable
    assert not ProviderHTTPFailure(400).retryable


def test_retry_delay_is_injectable() -> None:
    provider = ScriptedProvider(TransientProviderFailure(), "ok")
    delays: list[float] = []

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    executor = ProviderExecutor(
        {"mock-small": provider},
        timeout_seconds=1,
        retry_delay_seconds=1,
        sleeper=no_wait,
    )
    run(executor.execute(public_classification(), Sensitivity.PUBLIC, one_model_catalog()))

    assert delays == [1]


def test_breaker_opens_after_three_logical_failures() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)

    for _ in range(3):
        breaker.acquire()
        breaker.record_retryable_failure()

    assert breaker.state is CircuitState.OPEN
    assert breaker.consecutive_failures == 3
    with pytest.raises(CircuitOpenError):
        breaker.acquire()


def test_open_breaker_skips_provider_then_half_open_trial_closes() -> None:
    clock = FakeClock()
    provider = ScriptedProvider("recovered")
    executor = ProviderExecutor(
        {"mock-small": provider}, timeout_seconds=1, retry_delay_seconds=0, clock=clock
    )
    breaker = executor.breaker_for(provider)
    for _ in range(3):
        breaker.record_retryable_failure()
    with pytest.raises(ProviderUnavailableError) as error:
        run(executor.execute(public_classification(), Sensitivity.PUBLIC, one_model_catalog()))
    assert error.value.status_code == 503
    assert provider.calls == []

    clock.now = 30
    result = run(executor.execute(public_classification(), Sensitivity.PUBLIC, one_model_catalog()))
    assert result.output == "recovered"
    assert breaker.state is CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_half_open_retryable_trial_failure_reopens_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(3):
        breaker.record_retryable_failure()
    clock.now = 30
    breaker.acquire()
    breaker.record_retryable_failure()

    assert breaker.state is CircuitState.OPEN
    clock.now = 59.9
    with pytest.raises(CircuitOpenError):
        breaker.acquire()
    clock.now = 60
    breaker.acquire()
    assert breaker.state is CircuitState.HALF_OPEN


def test_permanent_failure_does_not_increment_breaker_counter() -> None:
    breaker = CircuitBreaker()
    breaker.record_permanent_failure()
    assert breaker.consecutive_failures == 0
    assert breaker.state is CircuitState.CLOSED


def test_permanent_half_open_trial_releases_slot_without_counting() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(3):
        breaker.record_retryable_failure()
    clock.now = 30
    breaker.acquire()
    breaker.record_permanent_failure()

    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.consecutive_failures == 3
    breaker.acquire()  # Permanent outcome releases the single trial permit.


def test_only_one_concurrent_half_open_trial_is_admitted() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(3):
        breaker.record_retryable_failure()
    clock.now = 30

    def acquire() -> bool:
        try:
            breaker.acquire()
            return True
        except CircuitOpenError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(lambda _: acquire(), range(8)))
    assert admitted.count(True) == 1
    assert breaker.state is CircuitState.HALF_OPEN


def test_breaker_state_is_per_provider_instance() -> None:
    first = ScriptedProvider()
    second = ScriptedProvider()
    executor = ProviderExecutor({"mock-small": first, "other": second})

    assert executor.breaker_for(first) is not executor.breaker_for(second)


def test_transient_failure_falls_back_once_to_next_eligible_model() -> None:
    catalog = load_model_catalog()
    primary = ScriptedProvider(TransientProviderFailure(), TransientProviderFailure())
    fallback = ScriptedProvider("fallback-ok")
    executor = ProviderExecutor(
        {"mock-small": primary, "mock-medium": fallback, "mock-large": ScriptedProvider()},
        timeout_seconds=1,
        retry_delay_seconds=0,
    )

    result = run(executor.execute(public_classification(), Sensitivity.PUBLIC, catalog))

    assert result.output == "fallback-ok"
    assert result.decision.model.model_id == "mock-medium"
    assert result.fallback_used
    assert result.retry_count == 1
    assert result.attempt_count == 3
    assert set(primary.calls + fallback.calls) == {"mock-small", "mock-medium"}


def test_open_primary_circuit_uses_one_next_eligible_model() -> None:
    catalog = load_model_catalog()
    primary = ScriptedProvider("must-not-run")
    fallback = ScriptedProvider("fallback-ok")
    executor = ProviderExecutor(
        {"mock-small": primary, "mock-medium": fallback, "mock-large": ScriptedProvider()},
        timeout_seconds=1,
        retry_delay_seconds=0,
    )
    breaker = executor.breaker_for(primary)
    for _ in range(3):
        breaker.record_retryable_failure()

    result = run(executor.execute(public_classification(), Sensitivity.PUBLIC, catalog))

    assert result.fallback_used
    assert result.decision.model.model_id == "mock-medium"
    assert primary.calls == []
    assert fallback.calls == ["mock-medium"]


def test_failed_fallback_stops_without_trying_a_third_model() -> None:
    catalog = load_model_catalog()
    primary = ScriptedProvider(TransientProviderFailure(), TransientProviderFailure())
    fallback = ScriptedProvider(TransientProviderFailure(), TransientProviderFailure())
    third = ScriptedProvider("must-not-run")
    executor = ProviderExecutor(
        {"mock-small": primary, "mock-medium": fallback, "mock-large": third},
        timeout_seconds=1,
        retry_delay_seconds=0,
    )

    with pytest.raises(ProviderUnavailableError):
        run(executor.execute(public_classification(), Sensitivity.PUBLIC, catalog))

    assert primary.calls == ["mock-small", "mock-small"]
    assert fallback.calls == ["mock-medium"]
    assert third.calls == []


@pytest.mark.parametrize("invalidates", ["approval", "capability", "tier", "context", "cost"])
def test_fallback_is_revalidated_before_invocation(invalidates: str) -> None:
    primary_model = make_large_model("a-primary")
    fallback_model = make_large_model("b-fallback")
    catalog = ModelCatalog(models=[primary_model, fallback_model])
    request = RouteRequest(
        prompt="solve this",
        task_type="reasoning",
        sensitivity="public",
        max_cost_usd=0.01,
    )

    class MutatingFailureProvider(ScriptedProvider):
        async def generate(self, request: RouteRequest, model: ModelDefinition) -> str:
            self.calls.append(model.model_id)
            if len(self.calls) == 1:
                if invalidates == "approval":
                    fallback_model.approved_for = set()
                elif invalidates == "capability":
                    fallback_model.capabilities = set()
                elif invalidates == "tier":
                    fallback_model.tier = "medium"
                elif invalidates == "context":
                    fallback_model.context_window = 10
                else:
                    fallback_model.input_price_per_million = 10_000
            raise TransientProviderFailure()

    primary = MutatingFailureProvider()
    fallback = ScriptedProvider("must-not-run")
    executor = ProviderExecutor(
        {"a-primary": primary, "b-fallback": fallback},
        timeout_seconds=1,
        retry_delay_seconds=0,
    )

    with pytest.raises(ProviderUnavailableError):
        run(executor.execute(request, Sensitivity.PUBLIC, catalog))

    assert primary.calls == ["a-primary", "a-primary"]
    assert fallback.calls == []


def test_permanent_error_does_not_attempt_fallback() -> None:
    catalog = load_model_catalog()
    primary = ScriptedProvider(PermanentProviderFailure("secret error"))
    fallback = ScriptedProvider()
    executor = ProviderExecutor(
        {"mock-small": primary, "mock-medium": fallback, "mock-large": ScriptedProvider()},
        timeout_seconds=1,
        retry_delay_seconds=0,
    )
    with pytest.raises(ProviderUnavailableError):
        run(executor.execute(public_classification(), Sensitivity.PUBLIC, catalog))
    assert fallback.calls == []


def test_http_timeout_is_controlled_and_cancels_async_provider_work(tmp_path) -> None:
    class SlowProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.cancelled = False

        async def generate(self, request: RouteRequest, model: ModelDefinition) -> str:
            self.calls += 1
            try:
                await asyncio.sleep(1)
            finally:
                self.cancelled = True
            return "late"

    provider = SlowProvider()
    settings = make_settings(
        tmp_path, provider_timeout_seconds=0.01, provider_retry_delay_seconds=0
    )
    client = TestClient(create_app(settings=settings, catalog=one_model_catalog(), provider=provider))
    response = client.post(
        "/route",
        json={"prompt": "classify this", "task_type": "classification", "sensitivity": "public"},
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 504
    assert response.json() == {
        "detail": "Eligible provider execution failed or is temporarily unavailable."
    }
    assert "late" not in response.text
    assert provider.calls == 2
    assert provider.cancelled


def test_permanent_provider_error_is_sanitized_at_http_boundary(tmp_path) -> None:
    provider = ScriptedProvider(PermanentProviderFailure("provider-secret-stack-detail"))
    client = TestClient(
        create_app(
            settings=make_settings(tmp_path, provider_retry_delay_seconds=0),
            catalog=one_model_catalog(),
            provider=provider,
        )
    )
    response = client.post(
        "/route",
        json={"prompt": "classify this", "task_type": "classification", "sensitivity": "public"},
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Eligible provider execution failed or is temporarily unavailable."
    )
    assert "provider-secret-stack-detail" not in response.text
    assert provider.calls == ["mock-small"]


def test_endpoint_reports_retry_and_fallback_counts(tmp_path) -> None:
    catalog = load_model_catalog()
    primary = ScriptedProvider(TransientProviderFailure(), TransientProviderFailure())
    fallback = ScriptedProvider("safe output")
    client = TestClient(
        create_app(
            settings=make_settings(
                tmp_path, provider_timeout_seconds=1, provider_retry_delay_seconds=0
            ),
            catalog=catalog,
            provider={
                "mock-small": primary,
                "mock-medium": fallback,
                "mock-large": ScriptedProvider(),
            },
        )
    )
    response = client.post(
        "/route",
        json={"prompt": "classify this", "task_type": "classification", "sensitivity": "public"},
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    assert response.json()["model_used"] == "mock-medium"
    assert response.json()["retry_count"] == 1
    assert response.json()["attempt_count"] == 3
    assert response.json()["fallback_used"] is True


def test_policy_denial_does_not_invoke_provider_or_fallback(tmp_path) -> None:
    provider = ScriptedProvider()
    client = TestClient(
        create_app(settings=make_settings(tmp_path), provider=provider)
    )
    response = client.post(
        "/route",
        json={
            "prompt": "PAN ABCDE1234F",
            "task_type": "classification",
            "sensitivity": "public",
        },
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 403
    assert provider.calls == []
