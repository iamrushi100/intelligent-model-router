import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import TransientProviderFailure
from app.reliability import CircuitState


class FlowProvider:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def generate(self, request, model):
        self.calls.append(model.model_id)
        result = self.results.pop(0) if self.results else "synthetic-safe-output"
        if isinstance(result, BaseException):
            raise result
        return result


def make_client(tmp_path, providers, **overrides):
    settings = Settings(
        api_keys=("e2e-test-key",),
        database_path=str(tmp_path / "e2e.sqlite3"),
        provider_retry_delay_seconds=0,
        **overrides,
    )
    return TestClient(create_app(settings=settings, provider=providers))


def post_route(client, prompt="synthetic puzzle", task="classification"):
    return client.post(
        "/route",
        headers={"X-API-Key": "e2e-test-key"},
        json={"prompt": prompt, "task_type": task, "sensitivity": "public"},
    )


def test_end_to_end_retry_fallback_and_single_quota_unit(tmp_path):
    primary = FlowProvider(TransientProviderFailure("private provider detail"), TransientProviderFailure(), "unused")
    fallback = FlowProvider("synthetic-safe-output")
    client = make_client(tmp_path, {"mock-small": primary, "mock-medium": fallback})

    response = post_route(client)

    assert response.status_code == 200
    assert response.json()["fallback_used"] is True
    assert response.json()["attempt_count"] == 3
    assert primary.calls == ["mock-small", "mock-small"]
    assert fallback.calls == ["mock-medium"]
    rows = client.app.state.audit_log.rows()
    assert len(rows) == 1
    assert rows[0]["status"] == 200
    assert rows[0]["retry_count"] == 1
    assert rows[0]["attempt_count"] == 3
    assert rows[0]["fallback_used"] == 1
    connection = sqlite3.connect(client.app.state.audit_log.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM quota_reservations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0] == 1
    finally:
        connection.close()


def test_end_to_end_policy_auth_circuit_and_private_metadata(tmp_path):
    small = FlowProvider()
    medium = FlowProvider()
    client = make_client(tmp_path, {"mock-small": small, "mock-medium": medium})
    app = client.app

    assert client.get("/health").status_code == 200
    assert client.post("/route", json={}).status_code == 401
    assert client.post("/route", json={}, headers={"X-API-Key": "wrong"}).status_code == 401
    restricted = post_route(client, "Synthetic PAN ABCDE1234F")
    assert restricted.status_code == 403
    assert small.calls == medium.calls == []

    for model_id in ("mock-small", "mock-medium"):
        provider = app.state.providers[model_id]
        breaker = app.state.provider_executor.breaker_for(provider)
        for _ in range(3):
            breaker.record_retryable_failure()
        assert breaker.state is CircuitState.OPEN
    open_response = post_route(client)
    assert open_response.status_code == 503
    assert "private provider detail" not in open_response.text
    assert "Traceback" not in open_response.text
    assert small.calls == medium.calls == []

    rows = app.state.audit_log.rows()
    assert [row["status"] for row in rows] == [401, 401, 403, 503]
    assert rows[-1]["failure_category"] == "circuit_open"
    connection = sqlite3.connect(app.state.audit_log.database_path)
    try:
        dumped = repr(connection.execute("SELECT * FROM request_audit").fetchall())
    finally:
        connection.close()
    for forbidden in ("e2e-test-key", "wrong", "Synthetic PAN ABCDE1234F", "synthetic-safe-output", "Traceback"):
        assert forbidden not in dumped
