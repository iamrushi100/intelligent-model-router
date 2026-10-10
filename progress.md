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
