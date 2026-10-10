import httpx
import pytest

from frontend.api_client import (
    RouterAPIError,
    SUPPORTED_SENSITIVITIES,
    SUPPORTED_TASKS,
    build_route_payload,
    send_route_request,
)


def test_frontend_options_and_payload_match_backend_enums():
    assert SUPPORTED_TASKS == (
        "classification", "extraction", "summarization", "reasoning", "coding"
    )
    assert SUPPORTED_SENSITIVITIES == ("public", "internal", "confidential", "restricted")
    assert build_route_payload("sample", "classification", "public") == {
        "prompt": "sample",
        "task_type": "classification",
        "sensitivity": "public",
    }


@pytest.mark.parametrize(
    ("prompt", "task", "sensitivity"),
    [
        ("", "classification", "public"),
        ("sample", "unsupported", "public"),
        ("sample", "classification", "unsupported"),
    ],
)
def test_payload_builder_rejects_empty_or_unsupported_values(prompt, task, sensitivity):
    with pytest.raises(ValueError):
        build_route_payload(prompt, task, sensitivity)


def test_send_route_request_uses_api_key_header_and_api_contract():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers["X-API-Key"]
        seen["payload"] = request.read()
        return httpx.Response(200, json={"model_used": "mock-small", "output": "mock"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = send_route_request(
            "http://router.test/", "test-key", {"prompt": "sample"}, client=client
        )

    assert seen["url"] == "http://router.test/route"
    assert seen["key"] == "test-key"
    assert b'"prompt":"sample"' in seen["payload"]
    assert result["model_used"] == "mock-small"


def test_api_validation_error_does_not_render_echoed_input():
    sensitive_value = "synthetic-secret-value"

    def handler(_request):
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "prompt"],
                        "msg": "Input should be valid",
                        "input": sensitive_value,
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RouterAPIError) as error:
            send_route_request("http://router.test", "test-key", {}, client=client)

    assert error.value.status_code == 422
    assert "body.prompt" in str(error.value)
    assert sensitive_value not in str(error.value)
