from pathlib import Path

from app.benchmark import load_dataset, run_benchmark
from app.model_config import ModelCatalog, load_model_catalog


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmark-data" / "synthetic_prompts.json"


def test_dataset_has_50_artificial_cases_with_all_task_labels() -> None:
    cases = load_dataset(DATASET)
    assert len(cases) == 50
    assert {case["task_type"] for case in cases} == {
        "classification", "extraction", "summarization", "reasoning", "coding"
    }
    assert all(case["sensitivity"] == "public" for case in cases)
    assert all(case["expected_outcome"] == "routable" for case in cases)


def test_benchmark_is_deterministic_and_reports_cost_savings() -> None:
    cases = load_dataset(DATASET)
    catalog = load_model_catalog()
    first = run_benchmark(cases, catalog)
    second = run_benchmark(cases, catalog)
    assert first == second
    assert first["case_count"] == 50
    assert first["routed_total_cost_usd"] < first["baseline_total_cost_usd"]
    assert first["estimated_savings_usd"] > 0
    assert first["synthetic_routing_checks"]["checked"] == 50
    assert sum(task["count"] for task in first["tasks"].values()) == 50


def test_zero_cost_baseline_has_no_percentage_or_currency_savings() -> None:
    catalog = load_model_catalog()
    free_catalog = ModelCatalog(models=[
        model.model_copy(update={"input_price_per_million": 0, "output_price_per_million": 0})
        for model in catalog.models
    ])
    result = run_benchmark(load_dataset(DATASET), free_catalog)
    assert result["baseline_total_cost_usd"] == 0
    assert result["estimated_savings_usd"] is None
    assert result["estimated_savings_percent"] is None
