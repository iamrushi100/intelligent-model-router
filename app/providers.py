"""Provider interface and deterministic local mock implementation."""

from typing import Protocol

from app.model_config import ModelDefinition
from app.schemas import RouteRequest


class Provider(Protocol):
    async def generate(self, request: RouteRequest, model: ModelDefinition) -> str: ...


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
