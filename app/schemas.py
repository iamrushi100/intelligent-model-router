"""Typed API models for the future routing endpoint."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskType(StrEnum):
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    REASONING = "reasoning"
    CODING = "coding"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=20_000)
    task_type: TaskType
    sensitivity: Sensitivity
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_output_tokens: int = Field(default=2_048, ge=1, le=2_048)


class RouteResponse(BaseModel):
    output: str
    model_used: str
    final_sensitivity: Sensitivity
    decision_reason: str
    fallback_used: bool
    latency_ms: float = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
