"""Policy-first, deterministic model selection."""

from dataclasses import dataclass
from math import ceil

from app.model_config import ModelCatalog, ModelDefinition
from app.schemas import RouteRequest, Sensitivity


MINIMUM_TIER = {
    "classification": "small",
    "extraction": "small",
    "summarization": "medium",
    "reasoning": "large",
    "coding": "large",
}
_TIER_ORDER = {"small": 0, "medium": 1, "large": 2}


class PolicyDenied(Exception):
    """No configured model is approved for the effective sensitivity."""


class NoEligibleModel(Exception):
    """Approved models exist, but none can safely serve this request."""


@dataclass(frozen=True)
class RoutingDecision:
    model: ModelDefinition
    effective_sensitivity: Sensitivity
    input_tokens: int
    output_budget_tokens: int
    estimated_cost_usd: float
    reason: str


def estimate_tokens(text: str) -> int:
    """Approximate tokens as one token per four characters (rounded up)."""
    return ceil(len(text) / 4)


def estimate_cost(model: ModelDefinition, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * model.input_price_per_million
        + output_tokens * model.output_price_per_million
    ) / 1_000_000


def choose_model(
    request: RouteRequest,
    effective_level: Sensitivity,
    catalog: ModelCatalog,
) -> RoutingDecision:
    # Authorization is deliberately completed before any capability or cost checks.
    approved = [model for model in catalog.models if effective_level in model.approved_for]
    if not approved:
        raise PolicyDenied

    minimum_tier = MINIMUM_TIER[request.task_type.value]
    input_tokens = estimate_tokens(request.prompt)
    output_budget = request.max_output_tokens
    capable = [
        model
        for model in approved
        if request.task_type in model.capabilities
        and _TIER_ORDER[model.tier] >= _TIER_ORDER[minimum_tier]
        and input_tokens + output_budget <= model.context_window
    ]

    within_budget = [
        (model, estimate_cost(model, input_tokens, output_budget))
        for model in capable
    ]
    if request.max_cost_usd is not None:
        within_budget = [
            (model, cost) for model, cost in within_budget if cost <= request.max_cost_usd
        ]
    if not within_budget:
        raise NoEligibleModel

    model, cost = min(
        within_budget,
        key=lambda item: (item[1], _TIER_ORDER[item[0].tier], item[0].model_id),
    )
    reason = (
        f"Selected {model.model_id}: approved for {effective_level.value}, "
        f"supports {request.task_type.value}, meets the {minimum_tier} minimum, "
        "fits the estimated context, and is within the requested cost limit."
    )
    return RoutingDecision(
        model=model,
        effective_sensitivity=effective_level,
        input_tokens=input_tokens,
        output_budget_tokens=output_budget,
        estimated_cost_usd=cost,
        reason=reason,
    )
