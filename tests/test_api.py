from fastapi.testclient import TestClient

from app.main import create_app, app
from app.providers import MockProvider


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_route_returns_mock_result_without_echoing_prompt() -> None:
    provider = MockProvider()
    client = TestClient(create_app(provider=provider))
    response = client.post(
        "/route",
        json={
            "prompt": "hello person@example.com",
            "task_type": "classification",
            "sensitivity": "public",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model_used"] == "mock-medium"
    assert body["final_sensitivity"] == "internal"
    assert body["output"] == "mock-classification"
    assert "person@example.com" not in response.text
    assert provider.calls == ["mock-medium"]


def test_restricted_request_is_denied_without_provider_call() -> None:
    provider = MockProvider()
    client = TestClient(create_app(provider=provider))
    response = client.post(
        "/route",
        json={
            "prompt": "My PAN is ABCDE1234F",
            "task_type": "classification",
            "sensitivity": "public",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "No configured model is approved for restricted data."
    assert provider.calls == []


def test_invalid_or_ineligible_request_never_calls_provider() -> None:
    provider = MockProvider()
    client = TestClient(create_app(provider=provider))
    invalid = client.post("/route", json={"prompt": "bad"})
    over_budget = client.post(
        "/route",
        json={
            "prompt": "classify",
            "task_type": "classification",
            "sensitivity": "public",
            "max_cost_usd": 0,
        },
    )

    assert invalid.status_code == 422
    assert over_budget.status_code == 422
    assert provider.calls == []
