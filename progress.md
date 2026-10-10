# Project Progress

## Milestone 1 — API and configuration foundation

**Status:** Complete; focused tests passed (15 passed).

- Added a minimal FastAPI application with `GET /health`.
- Added validated settings and typed request/response schemas for the future route API.
- Added focused health, schema, and configuration tests.
- Verified with `.venv\Scripts\python.exe -m pytest -q tests`.
- No authentication, quotas, persistence, or resilience features are included.

## Milestone 2 — Sensitivity-aware intelligent routing

**Status:** Complete; full test suite passed (40 passed, 1 dependency deprecation warning).

- Added safely loaded demo model metadata, heuristic sensitivity detection, and strict policy-first deterministic model selection.
- Added a deterministic mock provider and `POST /route`; mock outputs do not echo prompts or leave the process.
- Demo prices, context windows, and capabilities are illustrative. Character-count token estimates are approximate; sensitivity patterns are not complete DLP.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` — 40 passed. Pytest reports a Starlette deprecation warning for its current HTTPX test-client integration.

**Next milestone:** Milestone 3 — bounded provider resilience (retry, fallback, and circuit breaker).

## Milestone 3 — Provider reliability

**Status:** Complete; full test suite passed (68 passed, 1 dependency deprecation warning).

- Added typed transient/permanent provider errors and configurable 15-second timeout / 1-second retry delay.
- Provider calls run sequentially under `asyncio.wait_for`; timeout cancels the async task, but future adapters must still account for remote work that may continue after cancellation.
- A request gets at most one retry total, then at most one next-cheapest eligible fallback. Fallback candidates are re-evaluated through the complete policy and eligibility pipeline.
- Circuit breakers are lock-protected and scoped to provider instances. Only the final outcome of each logical retry sequence increments the failure count; threshold is 3, cooldown 30 seconds, with one concurrent half-open trial.
- Breaker state is in-process and resets on application restart; it is not coordinated between workers. Mock provider remains the default and makes no network calls.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` — 68 passed. One Starlette deprecation warning remains for the installed HTTPX test-client integration.

**Next milestone:** Milestone 4 — authentication, usage limits, and safe logs.

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


## Milestone 6 - Final documentation and placement readiness

**Status:** Complete; documentation checked against the implementation.

- Added a root README with architecture, exact setup/configuration/run commands, API examples, benchmark interpretation, limitations, repository map, and source-plan links.
- Added a detailed architecture guide and beginner-friendly placement interview preparation with 15 Q&A items and a request walkthrough.
- Confirmed the apparent unusual characters in the project plan were valid Unicode symbols. Repaired only the repeated mojibake em-dash sequence in this progress file; prior milestone meaning and history remain intact. New Markdown files are UTF-8.
- Checked all local Markdown links; none are missing.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` - 80 passed, 1 Starlette deprecation warning for the installed HTTPX test-client integration. `.venv\Scripts\python.exe scripts\run_benchmark.py` completed successfully: routed estimated cost `$0.0164486`, fixed `mock-large` baseline `$0.0348275`, difference `$0.0183789` (`52.77123%`). These are synthetic estimates, not measured model quality, real provider savings, latency, or invoice costs.

**Limitations:** Documentation records that the project is not production-ready, the provider remains deterministic mock behavior, sensitivity checks are heuristic, rate limits/circuit breakers are in-process, and configured body/prompt/output overrides are not currently enforced by the request path.


## Milestone 8 - Request body limits and Git ignore hardening

**Status:** Complete; full suite passed (87 passed, 1 Starlette deprecation warning).

- Added an ASGI body guard for authenticated `POST /route` requests using `max_request_body_bytes`. A single valid oversized `Content-Length` is rejected before body consumption; otherwise actual ASGI chunk lengths are counted, so missing, malformed, duplicate, or understated length headers cannot bypass the cap. Only in-limit bodies are retained and replayed to FastAPI.
- Oversized requests return a fixed `413` detail response before schema validation, quota reservation, and provider execution. The existing auth/rate middleware still runs first and records one safe 413 audit row for authenticated requests.
- Added boundary, custom-setting, streaming/header mismatch, provider/quota/audit, valid-route, and public-health tests.
- Added SQLite WAL/SHM/journal sidecar patterns and `.pytest-test-*` directory exclusion to `.gitignore`; existing rules and benchmark output remain unchanged.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` - 87 passed, 1 Starlette deprecation warning. `.venv\Scripts\python.exe scripts\run_benchmark.py` completed with routed estimate `$0.0164486`, baseline `$0.0348275`, difference `$0.0183789` (`52.77123%`), unchanged from the prior benchmark. `git diff --check` completed without whitespace errors; Git printed line-ending conversion notices for modified Python/Markdown files.

**Limitations:** The app counts and retains at most the configured in-limit body. It cannot control buffering by upstream servers/proxies before ASGI delivery, or the size of one ASGI chunk already delivered to it. Authentication and rate checks precede body reading; prompt/output limits remain fixed schema limits rather than configurable overrides.


## Milestone 10 - Request body replay memory and edge-case coverage

**Status:** Complete; full suite passed (96 passed, 1 Starlette deprecation warning).

- Replaced the accumulated `bytearray` plus `bytes(...)` replay copy with references to the already-received nonempty ASGI chunks. Replay delivers those chunks once in order; empty bodies get one empty final request message, and subsequent receives report disconnect.
- Added direct ASGI coverage for duplicate/negative/inaccurate content-length values, empty chunks, early disconnect, and repeated receives, plus zero/negative configuration and unauthenticated oversized requests.
- Updated README and architecture notes to describe the bounded accumulated payload without claiming a process-wide memory cap.

**Verification:** `.venv\Scripts\python.exe -m pytest -q tests/test_request_body_limit.py` - 16 passed, 1 Starlette deprecation warning. `.venv\Scripts\python.exe -m pytest -q` - 96 passed, 1 Starlette deprecation warning.

**Limitations:** The middleware bounds the total bytes it accumulates and rejects before retaining an over-limit chunk. The ASGI server may already have allocated that incoming chunk; Python and framework overhead are also outside this bound. Disconnect behavior is tested with a direct ASGI harness rather than a network server.


## Milestone 12 - Final request-flow documentation correction

**Status:** Complete; full suite passed (97 passed, 1 Starlette deprecation warning).

- Corrected the README Mermaid diagram to show authentication and rate limiting before the configured body guard, then schema validation for in-limit requests; included the guard's `413` path.
- Added a direct-ASGI regression test for a valid declared length above the configured limit with an actual under-limit body. It asserts fixed `413` behavior and zero provider calls.

**Verification:** `.venv\Scripts\python.exe -m pytest -q` - 97 passed, 1 Starlette deprecation warning. `git diff --check` completed without whitespace errors; Git printed line-ending conversion notices for modified files.
