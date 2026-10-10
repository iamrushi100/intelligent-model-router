import pytest
from pydantic import ValidationError

from app.schemas import RouteRequest, RouteResponse, Sensitivity, TaskType


def test_route_request_accepts_supported_values_and_defaults() -> None:
    request = RouteRequest(
        prompt="Summarize this synthetic example.",
        task_type="summarization",
        sensitivity="internal",
    )

    assert request.task_type is TaskType.SUMMARIZATION
    assert request.sensitivity is Sensitivity.INTERNAL
    assert request.max_output_tokens == 2_048


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_type": "unknown"},
        {"sensitivity": "secret"},
        {"prompt": ""},
        {"prompt": "x" * 20_001},
        {"max_cost_usd": -0.01},
        {"max_output_tokens": 2_049},
        {"unexpected": "field"},
    ],
)
def test_route_request_rejects_invalid_values(overrides: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "prompt": "Classify this synthetic example.",
        "task_type": "classification",
        "sensitivity": "public",
    }
    payload.update(overrides)

    with pytest.raises(ValidationError):
        RouteRequest.model_validate(payload)


def test_route_response_validates_contract() -> None:
    response = RouteResponse(
        output="billing",
        model_used="mock-small",
        final_sensitivity="internal",
        decision_reason="Example only",
        fallback_used=False,
        latency_ms=1.2,
        estimated_cost_usd=0,
    )

    assert response.final_sensitivity is Sensitivity.INTERNAL
