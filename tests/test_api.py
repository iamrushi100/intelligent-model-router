from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, app
from app.providers import MockProvider


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_route_returns_mock_result_without_echoing_prompt(tmp_path) -> None:
    provider = MockProvider()
    settings = Settings(api_keys=("test-api-key",), database_path=str(tmp_path / "audit.sqlite3"))
    client = TestClient(create_app(settings=settings, provider=provider))
    response = client.post(
        "/route",
        json={
            "prompt": "hello person@example.com",
            "task_type": "classification",
            "sensitivity": "public",
        },
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model_used"] == "mock-medium"
    assert body["final_sensitivity"] == "internal"
    assert body["output"] == "mock-classification"
    assert "person@example.com" not in response.text
    assert provider.calls == ["mock-medium"]


def test_restricted_request_is_denied_without_provider_call(tmp_path) -> None:
    provider = MockProvider()
    settings = Settings(api_keys=("test-api-key",), database_path=str(tmp_path / "audit.sqlite3"))
    client = TestClient(create_app(settings=settings, provider=provider))
    response = client.post(
        "/route",
        json={
            "prompt": "My PAN is ABCDE1234F",
            "task_type": "classification",
            "sensitivity": "public",
        },
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "No configured model is approved for restricted data."
    assert provider.calls == []


def test_invalid_or_ineligible_request_never_calls_provider(tmp_path) -> None:
    provider = MockProvider()
    settings = Settings(api_keys=("test-api-key",), database_path=str(tmp_path / "audit.sqlite3"))
    client = TestClient(create_app(settings=settings, provider=provider))
    headers = {"X-API-Key": "test-api-key"}
    invalid = client.post("/route", json={"prompt": "bad"}, headers=headers)
    over_budget = client.post(
        "/route",
        json={
            "prompt": "classify",
            "task_type": "classification",
            "sensitivity": "public",
            "max_cost_usd": 0,
        },
        headers=headers,
    )

    assert invalid.status_code == 422
    assert over_budget.status_code == 422
    assert provider.calls == []


def test_authentication_precedes_provider_and_key_is_not_persisted(tmp_path) -> None:
    provider = MockProvider()
    secret = "never-store-this-key"
    database = tmp_path / "audit.sqlite3"
    settings = Settings(api_keys=(secret,), database_path=str(database))
    client = TestClient(create_app(settings=settings, provider=provider))

    missing = client.post("/route", json={})
    invalid = client.post("/route", json={}, headers={"X-API-Key": "wrong-secret"})
    assert missing.status_code == invalid.status_code == 401
    assert secret not in missing.text + invalid.text
    assert provider.calls == []
    assert secret.encode() not in database.read_bytes()


def test_quota_denial_happens_before_provider_invocation(tmp_path) -> None:
    provider = MockProvider()
    settings = Settings(
        api_keys=("test-api-key",),
        daily_cost_limit_usd=0.00000001,
        database_path=str(tmp_path / "audit.sqlite3"),
    )
    client = TestClient(create_app(settings=settings, provider=provider))
    response = client.post(
        "/route",
        json={"prompt": "hello", "task_type": "classification", "sensitivity": "public"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 429
    assert provider.calls == []
