import pytest

from app.model_config import ModelCatalog, ModelDefinition, load_model_catalog
from app.router import (
    MINIMUM_TIER,
    NoEligibleModel,
    PolicyDenied,
    choose_model,
    estimate_tokens,
)
from app.schemas import RouteRequest, Sensitivity, TaskType


@pytest.fixture
def catalog() -> ModelCatalog:
    return load_model_catalog()


def make_model(
    model_id: str,
    *,
    tier: str = "large",
    context: int = 32_768,
    capabilities: set[TaskType] | None = None,
    price: float = 1.0,
    approved_for: set[Sensitivity] | None = None,
    is_local: bool = False,
) -> ModelDefinition:
    return ModelDefinition(
        model_id=model_id,
        tier=tier,
        context_window=context,
        capabilities=capabilities or {TaskType.CLASSIFICATION},
        input_price_per_million=price,
        output_price_per_million=price,
        approved_for=approved_for or {Sensitivity.PUBLIC},
        is_local=is_local,
    )


@pytest.mark.parametrize(
    ("task", "minimum"),
    [
        (TaskType.CLASSIFICATION, "small"),
        (TaskType.EXTRACTION, "small"),
        (TaskType.SUMMARIZATION, "medium"),
        (TaskType.REASONING, "large"),
        (TaskType.CODING, "large"),
    ],
)
def test_each_task_uses_at_least_its_minimum_tier(
    catalog: ModelCatalog, task: TaskType, minimum: str
) -> None:
    request = RouteRequest(prompt="sample", task_type=task, sensitivity="public")

    decision = choose_model(request, Sensitivity.PUBLIC, catalog)

    tier_order = {"small": 0, "medium": 1, "large": 2}
    assert decision.model.tier == minimum
    assert tier_order[decision.model.tier] >= tier_order[MINIMUM_TIER[task.value]]


def test_policy_gate_precedes_capability_and_cost_selection() -> None:
    unapproved = make_model(
        "local-but-unapproved",
        capabilities=set(),
        approved_for=set(),
        is_local=True,
    )
    request = RouteRequest(prompt="sample", task_type="reasoning", sensitivity="restricted")

    with pytest.raises(PolicyDenied):
        choose_model(request, Sensitivity.RESTRICTED, ModelCatalog(models=[unapproved]))


def test_local_flag_does_not_bypass_approval() -> None:
    local = make_model("local", is_local=True, approved_for={Sensitivity.PUBLIC})
    request = RouteRequest(prompt="sample", task_type="classification", sensitivity="restricted")

    with pytest.raises(PolicyDenied):
        choose_model(request, Sensitivity.RESTRICTED, ModelCatalog(models=[local]))


def test_capability_and_context_window_filter_models() -> None:
    too_small_context = make_model("too-small-context", context=2_000)
    wrong_capability = make_model("wrong-capability", capabilities={TaskType.CODING})
    eligible = make_model("eligible", context=10_000)
    request = RouteRequest(prompt="x" * 100, task_type="classification", sensitivity="public")
    models = ModelCatalog(models=[too_small_context, wrong_capability, eligible])

    assert choose_model(request, Sensitivity.PUBLIC, models).model.model_id == "eligible"
    assert estimate_tokens("x" * 9) == 3


def test_choose_lowest_cost_and_break_ties_by_model_id() -> None:
    costly = make_model("z-costly", price=2.0)
    cheaper_z = make_model("z-cheap", price=0.5)
    cheaper_a = make_model("a-cheap", price=0.5)
    request = RouteRequest(prompt="sample", task_type="classification", sensitivity="public")
    models = ModelCatalog(models=[costly, cheaper_z, cheaper_a])

    assert choose_model(request, Sensitivity.PUBLIC, models).model.model_id == "a-cheap"


def test_cost_limit_can_leave_no_eligible_model(catalog: ModelCatalog) -> None:
    request = RouteRequest(
        prompt="sample", task_type="classification", sensitivity="public", max_cost_usd=0
    )

    with pytest.raises(NoEligibleModel):
        choose_model(request, Sensitivity.PUBLIC, catalog)
