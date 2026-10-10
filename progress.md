# Project Progress

## Milestone 1 â€” API and configuration foundation

**Status:** Complete; focused tests passed (15 passed).

- Added a minimal FastAPI application with `GET /health`.
- Added validated settings and typed request/response schemas for the future route API.
- Added focused health, schema, and configuration tests.
- Verified with `.venv\Scripts\python.exe -m pytest -q tests`.
- No authentication, quotas, persistence, or resilience features are included.

## Milestone 2 â€” Sensitivity-aware intelligent routing

**Status:** Complete; full test suite passed (40 passed, 1 dependency deprecation warning).

- Added safely loaded demo model metadata, heuristic sensitivity detection, and strict policy-first deterministic model selection.
- Added a deterministic mock provider and `POST /route`; mock outputs do not echo prompts or leave the process.
- Demo prices, context windows, and capabilities are illustrative. Character-count token estimates are approximate; sensitivity patterns are not complete DLP.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` â€” 40 passed. Pytest reports a Starlette deprecation warning for its current HTTPX test-client integration.

**Next milestone:** Milestone 3 â€” bounded provider resilience (retry, fallback, and circuit breaker).

## Milestone 3 â€” Provider reliability

**Status:** Complete; full test suite passed (68 passed, 1 dependency deprecation warning).

- Added typed transient/permanent provider errors and configurable 15-second timeout / 1-second retry delay.
- Provider calls run sequentially under `asyncio.wait_for`; timeout cancels the async task, but future adapters must still account for remote work that may continue after cancellation.
- A request gets at most one retry total, then at most one next-cheapest eligible fallback. Fallback candidates are re-evaluated through the complete policy and eligibility pipeline.
- Circuit breakers are lock-protected and scoped to provider instances. Only the final outcome of each logical retry sequence increments the failure count; threshold is 3, cooldown 30 seconds, with one concurrent half-open trial.
- Breaker state is in-process and resets on application restart; it is not coordinated between workers. Mock provider remains the default and makes no network calls.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` â€” 68 passed. One Starlette deprecation warning remains for the installed HTTPX test-client integration.

**Next milestone:** Milestone 4 â€” authentication, usage limits, and safe logs.

## Milestone 4 - API security, usage quotas, and metadata-only logging

**Status:** Complete; full test suite passed (75 passed, 1 dependency deprecation warning).

- Added configurable API-key authentication on `POST /route`; `/health` stays public. API keys are compared without prefix hints and only SHA-256 pseudonymous IDs are used for accounting.
- Added a thread-safe, per-process rolling 30-request/60-second limiter. Authenticated rejected requests do not consume another slot; in-memory limits reset on restart and are not shared by workers.
- Added SQLite WAL metadata and quota tables. Quotas reserve atomically per UTC day before provider execution, then reconcile estimated costs of actual provider attempts; retries/fallbacks remain one API request. Unused reservations are released, and attempted provider usage is committed on execution failure.
- Request flow authenticates and rate-limits before schema validation, applies sensitivity policy and model eligibility before quota reservation, then invokes the existing reliability layer. Each route outcome is logged once with sanitized metadata; prompts, API keys, provider output, and exception details are not stored.
- Database connections are explicitly closed so temporary databases can be removed reliably on Windows.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` - 75 passed, 1 Starlette deprecation warning for the installed HTTPX test-client integration.

**Limitations:** Rate limiting is in-memory and single-process. API keys are configured using `ROUTER_API_KEYS`; production startup rejects an empty key list.

**Next milestone:** Benchmarking and documentation, per the project plan.


## Milestone 5 - end-to-end integration and reproducible benchmark

**Status:** Complete; full suite passed (80 passed, 1 Starlette deprecation warning).

- Added API-flow integration coverage for public health, authentication, restricted-data denial, transient retry plus one fallback, single-request quota accounting, open circuits, audit statuses, and privacy-safe error/audit content.
- Added a 50-case artificial public dataset (10 prompts for each supported task), machine-readable expected task/minimum-tier/outcome labels, and benchmark documentation.
- Added a standalone benchmark that reuses the router's sensitivity detection, policy/eligibility ranking, character-count token estimate, and synthetic model costs. The fixed `mock-large` baseline is counted only where approved and capable. No provider, API quota, or audit path is invoked.
- The benchmark reports routed/baseline availability and costs by task, plus savings only when baseline cost is positive. Its synthetic routing check validates minimum-tier labels; it does not evaluate model answers or quality.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` - 80 passed, 1 Starlette deprecation warning (the installed HTTPX test-client integration). `.venv\Scripts\python.exe scripts\run_benchmark.py` ran twice; both outputs had SHA-256 `6E0A0C270681C9F882D0036915D66133275CFCDC73951C68E22B45BCC02ADE77`.

**Illustrative benchmark result:** routed estimated total `$0.0164486`; fixed `mock-large` baseline `$0.0348275`; estimated difference `$0.0183789` (`52.77123%`). 50/50 cases were available on both sides, and 50/50 synthetic minimum-tier checks passed. These are synthetic catalog estimates, not real provider savings or quality results.

**Limitations:** Outputs depend on illustrative `models.yaml` prices and approximate `ceil(prompt characters / 4)` token estimates. Dataset cases are all public and routable, so denied/unavailable counts are reported but zero in this sample. No model quality, latency, or production savings is measured.

**Next milestone:** Review benchmark interpretation and project documentation; no hosted providers are added.
