"""Small HTTP client helpers for the optional Streamlit interface."""

from typing import Any

import httpx

from app.schemas import Sensitivity, TaskType


SUPPORTED_TASKS = tuple(task.value for task in TaskType)
SUPPORTED_SENSITIVITIES = tuple(level.value for level in Sensitivity)


class RouterAPIError(Exception):
    """A safe, user-displayable API or connection error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_route_payload(
    prompt: str,
    task_type: str,
    sensitivity: str,
) -> dict[str, str]:
    """Build only fields supported by the current RouteRequest schema."""
    if task_type not in SUPPORTED_TASKS:
        raise ValueError("Choose a supported task type.")
    if sensitivity not in SUPPORTED_SENSITIVITIES:
        raise ValueError("Choose a supported sensitivity level.")
    if not prompt:
        raise ValueError("Enter a prompt before routing.")
    return {"prompt": prompt, "task_type": task_type, "sensitivity": sensitivity}


def _safe_error_detail(response: httpx.Response) -> str:
    """Format API errors without rendering submitted input fields."""
    try:
        detail: Any = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None

    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            location = ".".join(str(part) for part in item.get("loc", ()))
            message = item.get("msg")
            if isinstance(message, str):
                messages.append(f"{location}: {message}" if location else message)
        if messages:
            return "\n".join(messages)
    return f"The router returned HTTP {response.status_code}. Check the request and server logs."


def send_route_request(
    api_base_url: str,
    api_key: str,
    payload: dict[str, str],
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Send one authenticated request to the existing FastAPI endpoint."""
    if not api_key:
        raise RouterAPIError("Enter the API key configured for the FastAPI server.")
    if not api_base_url.strip():
        raise RouterAPIError("Enter the FastAPI base URL.")

    url = f"{api_base_url.rstrip('/')}/route"
    try:
        if client is None:
            with httpx.Client(timeout=20.0) as session:
                response = session.post(
                    url,
                    headers={"X-API-Key": api_key},
                    json=payload,
                )
        else:
            response = client.post(
                url,
                headers={"X-API-Key": api_key},
                json=payload,
            )
    except httpx.HTTPError:
        # Avoid displaying exception/request objects, which could expose headers.
        raise RouterAPIError(
            "Could not reach the FastAPI server. Check its URL and confirm it is running."
        ) from None

    if not response.is_success:
        raise RouterAPIError(_safe_error_detail(response), response.status_code)
    try:
        result = response.json()
    except ValueError:
        raise RouterAPIError("The router returned an unreadable response.") from None
    if not isinstance(result, dict):
        raise RouterAPIError("The router returned an unexpected response format.")
    return result
