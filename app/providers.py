"""Provider interface and deterministic local mock implementation."""

from typing import Protocol

from app.model_config import ModelDefinition
from app.schemas import RouteRequest


class Provider(Protocol):
    async def generate(self, request: RouteRequest, model: ModelDefinition) -> str: ...


class ProviderFailure(Exception):
    """Sanitized base category for failures returned by provider adapters."""

    retryable = False


class TransientProviderFailure(ProviderFailure):
    """A provider failure that may succeed on one bounded retry."""

    retryable = True


class ProviderTimeoutFailure(TransientProviderFailure):
    """Provider execution exceeded its configured timeout."""


class ProviderConnectionFailure(TransientProviderFailure):
    """A provider adapter reported a connection failure."""


class PermanentProviderFailure(ProviderFailure):
    """A non-retryable provider error, including ordinary HTTP 4xx."""


class ProviderHTTPFailure(ProviderFailure):
    """HTTP adapter error categorized by status without exposing its body."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Provider returned HTTP {status_code}")
        self.status_code = status_code
        self.retryable = status_code in {408, 429} or 500 <= status_code <= 599


class MockProvider:
    """Return fixed task-appropriate output without echoing or transmitting input."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, request: RouteRequest, model: ModelDefinition) -> str:
        self.calls.append(model.model_id)
        return {
            "classification": "mock-classification",
            "extraction": '{"result":"mock-extraction"}',
            "summarization": "Mock summary output.",
            "reasoning": "Mock reasoning output.",
            "coding": "# Mock code output",
        }[request.task_type.value]
