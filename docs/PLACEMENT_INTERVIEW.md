# Placement interview preparation

Use these notes to explain the code that exists today. The project is a rule-based policy-aware router with deterministic mock responses, not a trained ML system or a hosted LLM integration.

## 60-second explanation

“I built a small FastAPI model-routing API to demonstrate how an application can choose a suitable model while enforcing data policy. Each request declares a task and sensitivity. The server also checks for common sensitive patterns and applies the stricter sensitivity. A policy gate removes models that are not approved before capability, context, minimum-tier, and illustrative cost selection. The selected model is called through a deterministic local mock behind timeout, one retry, one revalidated fallback, and a per-provider circuit breaker. The API also uses API-key authentication, per-key rate and daily quota controls, and SQLite metadata-only logging. I added end-to-end tests and a 50-prompt synthetic cost benchmark. The prices and benchmark results are illustrative; they do not establish real model quality or savings.”

## Likely questions and clear answers

### 1. Is the router using machine learning?

No. It uses explicit task minimums, model capabilities, sensitivity approval, context fit, and estimated cost. This makes decisions reproducible and explainable. A learned router would need representative training labels, an evaluation showing it improves routing safely, and a fallback policy for uncertain predictions.

### 2. Why does the policy gate run before cost optimization?

Cost is only meaningful among models that are allowed to process the effective sensitivity. Ranking price first could select a cheaper model that should never receive that request. The implementation first forms the approved pool, then applies capability, minimum-tier, context, and cost constraints.

### 3. How is sensitivity detected?

The heuristic recognizes common email and phone patterns as at least internal, and likely card numbers, Aadhaar-like values, PAN-like values, and obvious API-key patterns as restricted. The effective level is the stricter of the client declaration and detected level. Regex heuristics can miss formats and create false positives, so this is not complete DLP.

### 4. What happens with restricted data?

The current mock catalog has no model approved for restricted data. After detection and policy filtering, the API returns `403` and does not invoke a provider. A mock is not treated as verified private or compliant processing.

### 5. How does the router select a model?

It keeps approved models that support the task, meet that task's minimum tier, and have enough context for estimated input plus output allowance. It estimates token count as `ceil(characters / 4)` and cost from illustrative per-million-token prices. It chooses the lowest cost; ties are resolved by tier and then model ID.

### 6. Why have both retry and fallback?

A retry gives the same provider/model one chance to recover from a transient failure. If execution still fails, a single fallback can try another eligible model. They are bounded to avoid request storms, and fallback reruns eligibility for the same effective sensitivity. The system calls providers sequentially.

### 7. How does the circuit breaker work?

Each provider instance starts closed. Three consecutive retryable logical failures open it. After a 30-second cooldown, exactly one half-open trial is allowed; success closes it and a retryable failure reopens it. It is in-memory and per process, so restart clears it and other workers do not share it.

### 8. What is the concurrency story?

The rate limiter uses a thread lock and a monotonic rolling window. Daily quota capacity is reserved with SQLite transactions (`BEGIN IMMEDIATE`) before provider execution, so concurrent local requests cannot reserve beyond the configured capacity. This is still a single application/database design, not a distributed quota service. Provider calls for one request are sequential.

### 9. How are API keys protected?

`POST /route` reads `X-API-Key`, compares candidates with `hmac.compare_digest`, and returns a generic `401` on missing or invalid credentials. Accounting and logs use a SHA-256 pseudonymous identifier. The raw key is not returned or stored in the SQLite audit table. Keys are supplied through `ROUTER_API_KEYS`, not source code.

### 10. What does SQLite store?

The audit row stores a request ID, pseudonymous key ID, UTC time, task, effective sensitivity, selected model/tier, status, attempt/retry/fallback counts, estimated token/cost fields, latency, and a sanitized failure category. It does not store raw prompts, keys, provider responses, or stack traces. The current implementation does not even store a prompt hash. Metadata can still be sensitive, and SQLite is not intended here as a distributed high-write store.

### 11. How are daily quotas safe under concurrent requests?

The code reserves estimated cost and possible large-tier use inside a SQLite write transaction before any provider is called. After success or provider failure, it reconciles to the estimated costs and tiers of actual attempts and releases unused reserved capacity. Retry/fallback attempts belong to the same incoming request reservation. The accounting remains estimate-based and is not an invoice ledger.

### 12. What do the tests prove?

The unit and end-to-end tests check behaviors such as sensitivity policy, eligibility, no-provider-call denials, retries, fallback, circuit state, API auth, quota accounting, privacy-safe audit rows, and deterministic benchmark calculations. They show that this implementation follows those rules under test; they do not prove complete security, DLP, external provider behavior, or production capacity.

### 13. Is the benchmark a quality comparison?

No. It has 50 synthetic public prompts, illustrative prices, a fixed policy-permitted `mock-large` cost baseline, and the approximate character-count estimator. Labels check expected task/minimum-tier routing only. The mock does not generate meaningful answers, so no quality score is measured. The reported cost difference is not real provider savings or invoice cost.

### 14. What would need to change for production?

First, connect real provider adapters with verified data-handling approvals, provider-reported usage, and integration tests. Add a shared key-management system, distributed rate and circuit state, hardened transaction/idempotency and billing reconciliation, structured monitoring and retention controls, robust DLP or a reviewed policy service, upstream/proxy request limits, deployment controls, and load/security testing. Re-evaluate the model catalog and benchmark with policy-approved models and a real quality methodology. This project is not production-ready.

### 15. When would an ML router make sense?

Only when a reliable labeled workload and measurable objective exist, and a learned choice can outperform the transparent rules without violating policy. I would keep the policy gate independent and authoritative, then test learned recommendations offline before allowing them to affect provider calls.

## Explaining one real request

For a public request such as a fictional classification prompt:

1. The client calls `POST /route` with `X-API-Key` and JSON containing `prompt`, `task_type`, and declared `sensitivity`.
2. Middleware authenticates the key, derives its pseudonymous ID, and checks the rolling rate limit. Missing/invalid keys return `401`; rate rejection returns `429` before the provider.
3. The ASGI body guard uses the configured byte limit, rejecting oversized requests with `413` before schema parsing. It checks actual streamed bytes when the length header is missing, malformed, duplicated, or too small. FastAPI then validates the schema; the route detects common sensitivity patterns and takes the stricter of detected and declared levels.
4. `rank_models` applies the sensitivity approval gate first. It then checks task capability, minimum tier, estimated context, and optional request cost ceiling. No approved model means `403`; no eligible one means `422`.
5. The route reserves conservative daily estimated cost and possible large-tier capacity in SQLite. If that reservation fails, it returns `429` without a provider call.
6. The executor calls the selected mock model. On retryable failure it can retry once; after failure or an open circuit it can try one revalidated fallback. A successful mock returns its fixed task output without echoing the prompt.
7. The route reconciles quota estimates for actual attempted models, writes one safe metadata audit row, and returns the output, selected model, final sensitivity, reason, fallback flag, latency, cost/token estimates, and attempt counts.

If you are asked for numeric benchmark results, say: “The synthetic run estimates `$0.0164486` for routed selection versus `$0.0348275` for fixed `mock-large`, a difference of `$0.0183789` or `52.77123%`. This is based on synthetic prompts and illustrative prices; it says nothing about answer quality, actual provider savings, latency, or invoices.”

## Trade-offs to explain openly

- Rules are easier to audit and reproduce than an unvalidated learned classifier, but task labels and heuristics can be wrong.
- Regex sensitivity checks improve obvious-case handling but are not a full DLP system.
- Deterministic mock behavior makes tests stable but cannot demonstrate real inference, model quality, or latency.
- Token counts/prices are approximate demo inputs; daily cost quotas are not financial guarantees.
- Rate and circuit state is process-local; the application has no distributed coordination.
- The body guard operates after authentication and cannot control buffering in upstream proxies/servers or an already-delivered ASGI chunk. Prompt/output environment overrides remain unapplied; the request schema uses fixed limits.
- SQLite WAL supports the local project, but it has a single-writer limit and is not a general distributed database choice.

Related: [README](../README.md), [architecture guide](ARCHITECTURE.md), [project plan](../intelligent_model_router_final_project_plan.md), and [progress](../progress.md).
