"""Reproducible synthetic cost comparison; this is not a model-quality benchmark."""

import json
from pathlib import Path
from typing import Any

from app.model_config import ModelCatalog
from app.router import NoEligibleModel, PolicyDenied, estimate_cost, estimate_tokens, rank_models
from app.schemas import RouteRequest, Sensitivity
from app.sensitivity import detect_sensitivity, effective_sensitivity


def load_dataset(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    if len(cases) != 50:
        raise ValueError("The synthetic benchmark dataset must contain exactly 50 cases")
    allowed_tasks = {"classification", "extraction", "summarization", "reasoning", "coding"}
    for case in cases:
        if case["task_type"] not in allowed_tasks or case["expected_outcome"] not in {"routable", "denied"}:
            raise ValueError(f"Invalid benchmark labels in case {case.get('id', '<unknown>')}")
    return cases


def run_benchmark(cases: list[dict[str, Any]], catalog: ModelCatalog) -> dict[str, Any]:
    """Compare selected-model estimates with the fixed mock-large permitted baseline."""
    baseline_model = catalog.model_by_id("mock-large")
    tasks: dict[str, dict[str, Any]] = {}
    routed_total = 0.0
    baseline_total = 0.0
    routed_available = baseline_available = 0
    routing_checks = routing_check_cases = 0

    for case in cases:
        task = case["task_type"]
        task_result = tasks.setdefault(task, {
            "count": 0, "routed_available": 0, "routed_denied_or_unavailable": 0,
            "baseline_available": 0, "baseline_denied_or_unavailable": 0,
            "routed_cost_usd": 0.0, "baseline_cost_usd": 0.0,
            "synthetic_routing_checks_passed": 0,
        })
        task_result["count"] += 1
        request = RouteRequest(
            prompt=case["prompt"], task_type=task, sensitivity=case["sensitivity"],
            max_output_tokens=case["output_tokens"],
        )
        detected = detect_sensitivity(request.prompt)
        effective = effective_sensitivity(request.sensitivity, detected)
        input_tokens = estimate_tokens(request.prompt)

        try:
            decision = rank_models(request, effective, catalog)[0]
            routed_cost = estimate_cost(decision.model, input_tokens, case["output_tokens"])
            task_result["routed_available"] += 1
            task_result["routed_cost_usd"] += routed_cost
            routed_total += routed_cost
            routed_available += 1
            routing_check_cases += 1
            if case["expected_outcome"] == "routable" and _tier_rank(decision.model.tier) >= _tier_rank(case["expected_minimum_tier"]):
                task_result["synthetic_routing_checks_passed"] += 1
                routing_checks += 1
        except (PolicyDenied, NoEligibleModel):
            task_result["routed_denied_or_unavailable"] += 1

        if effective in baseline_model.approved_for and task in {cap.value for cap in baseline_model.capabilities} and input_tokens + case["output_tokens"] <= baseline_model.context_window:
            baseline_cost = estimate_cost(baseline_model, input_tokens, case["output_tokens"])
            task_result["baseline_available"] += 1
            task_result["baseline_cost_usd"] += baseline_cost
            baseline_total += baseline_cost
            baseline_available += 1
        else:
            task_result["baseline_denied_or_unavailable"] += 1

    baseline_total = round(baseline_total, 12)
    routed_total = round(routed_total, 12)
    savings = baseline_total - routed_total if baseline_total > 0 else None
    return {
        "benchmark_type": "synthetic routing and illustrative cost estimate; no model quality measured",
        "case_count": len(cases),
        "fixed_baseline_model": baseline_model.model_id,
        "token_estimator": "ceil(prompt characters / 4); approximate",
        "output_token_estimate_per_case": "dataset output_tokens value; same for routed and baseline",
        "routed_available_cases": routed_available,
        "baseline_available_cases": baseline_available,
        "routed_total_cost_usd": routed_total,
        "baseline_total_cost_usd": baseline_total,
        "estimated_savings_usd": round(savings, 12) if savings is not None else None,
        "estimated_savings_percent": round(savings / baseline_total * 100, 6) if baseline_total > 0 else None,
        "synthetic_routing_checks": {"passed": routing_checks, "checked": routing_check_cases},
        "tasks": {name: _round_costs(values) for name, values in sorted(tasks.items())},
        "assumptions": [
            "Prices are illustrative synthetic catalog values, not provider invoices.",
            "All prompts are artificial and public; both routed and baseline choices obey sensitivity, capability, and context rules.",
            "The fixed mock-large baseline is used only when policy and capability permit it.",
            "Routing label checks verify expected minimum tier only; they do not measure answer correctness or LLM quality.",
            "No provider is invoked and API quotas/audit records are not used by this benchmark.",
        ],
    }


def _tier_rank(tier: str) -> int:
    return {"small": 0, "medium": 1, "large": 2}[tier]


def _round_costs(result: dict[str, Any]) -> dict[str, Any]:
    rounded = dict(result)
    rounded["routed_cost_usd"] = round(rounded["routed_cost_usd"], 12)
    rounded["baseline_cost_usd"] = round(rounded["baseline_cost_usd"], 12)
    return rounded
