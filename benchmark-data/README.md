# Synthetic benchmark dataset

`synthetic_prompts.json` contains 50 artificial, public prompts (10 per supported task). Each case labels its task, declared sensitivity, expected minimum tier, expected routability, and fixed output-token estimate. The labels check task/tier routing constraints; they do not describe or score generated answer quality.

Run from the repository root with `.venv\Scripts\python.exe scripts\run_benchmark.py`. The command writes `benchmark_results.json`. Results are deterministic for a fixed dataset, code, and `models.yaml`; the JSON has no run timestamp. Prices and resulting costs/savings use illustrative synthetic model metadata, and token counts use the project's approximate character-count estimator. No provider is called, and the benchmark does not prove real model quality, production savings, latency, or invoice costs.
