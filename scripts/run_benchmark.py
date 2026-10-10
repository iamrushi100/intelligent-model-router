"""Run the deterministic synthetic cost benchmark from the repository root."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.benchmark import load_dataset, run_benchmark  # noqa: E402
from app.model_config import load_model_catalog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmark-data" / "synthetic_prompts.json")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark_results.json")
    arguments = parser.parse_args()
    result = run_benchmark(load_dataset(arguments.dataset), load_model_catalog())
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"Wrote deterministic results to {arguments.output}")


if __name__ == "__main__":
    main()
