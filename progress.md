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

