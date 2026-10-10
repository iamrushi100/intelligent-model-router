"""FastAPI application entry point and safe request-flow orchestration."""

from collections.abc import Mapping
from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.audit_log import AuditLog, AuditRecord
from app.config import Settings, load_settings
from app.model_config import ModelCatalog, load_model_catalog
from app.providers import MockProvider, Provider
from app.reliability import ProviderExecutor, ProviderUnavailableError
from app.router import NoEligibleModel, PolicyDenied, estimate_cost, estimate_tokens, rank_models
from app.schemas import RouteRequest, RouteResponse
from app.sensitivity import detect_sensitivity, effective_sensitivity
from app.usage import ApiKeyAuthenticator, QuotaExceeded, QuotaLedger, RateLimiter


class RequestBodyLimitMiddleware:
    """Boundedly read actual ASGI body bytes while allowing auth to run first."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] == "/route"
        ):
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value for name, value in scope.get("headers", ())
            if name.lower() == b"content-length"
        ]
        # A single parseable oversized length can be rejected without consuming the body.
        # Missing, malformed, duplicate, or smaller lengths still go through byte counting.
        if len(content_lengths) == 1:
            try:
                declared_length = int(content_lengths[0].decode("ascii").strip())
            except (UnicodeDecodeError, ValueError):
                declared_length = -1
            if declared_length > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body exceeds the configured size limit."},
                )
                await response(scope, receive, send)
                return

        # Keep references to ASGI-provided chunks rather than joining them into a
        # bytearray and copying the complete body again during replay.
        body_chunks: list[bytes] = []
        disconnected = False
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if received_bytes + len(chunk) > self.max_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "Request body exceeds the configured size limit."},
                    )
                    await response(scope, receive, send)
                    return
                received_bytes += len(chunk)
                if chunk:
                    body_chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                disconnected = True
                break

        body_replayed = False
        replay_index = 0
        had_body_chunks = bool(body_chunks)

        async def replay_body():
            nonlocal body_replayed, replay_index
            if disconnected:
                return {"type": "http.disconnect"}
            if replay_index < len(body_chunks):
                chunk = body_chunks[replay_index]
                replay_index += 1
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": replay_index < len(body_chunks),
                }
            if not had_body_chunks and not body_replayed:
                body_replayed = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_body, send)


def create_app(
    settings: Settings | None = None,
    catalog: ModelCatalog | None = None,
    provider: Provider | Mapping[str, Provider] | None = None,
) -> FastAPI:
    app_settings = settings or load_settings()
    application = FastAPI(title=app_settings.app_name)
    # Added before the auth middleware below; Starlette places later additions outside it,
    # so unauthenticated requests retain the existing early 401 behavior.
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=app_settings.max_request_body_bytes,
    )

    model_catalog = catalog or load_model_catalog()
    if provider is None:
        providers = {model.model_id: MockProvider() for model in model_catalog.models}
    elif isinstance(provider, Mapping):
        providers = dict(provider)
    else:
        providers = {model.model_id: provider for model in model_catalog.models}
    executor = ProviderExecutor(
        providers,
        timeout_seconds=app_settings.provider_timeout_seconds,
        retry_delay_seconds=app_settings.provider_retry_delay_seconds,
    )
    audit_log = AuditLog(app_settings.database_path)
    authenticator = ApiKeyAuthenticator(app_settings.api_keys)
    rate_limiter = RateLimiter(
        limit=app_settings.rate_limit_requests,
        window_seconds=app_settings.rate_limit_window_seconds,
    )
    quota_ledger = QuotaLedger(
        app_settings.database_path,
        audit_log,
        cost_limit_usd=app_settings.daily_cost_limit_usd,
        large_tier_limit=app_settings.daily_large_tier_limit,
    )
    application.state.provider = (
        provider
        if provider is not None and not isinstance(provider, Mapping)
        else next(iter(providers.values()), None)
    )
    application.state.providers = providers
    application.state.model_catalog = model_catalog
    application.state.provider_executor = executor
    application.state.audit_log = audit_log
    application.state.quota_ledger = quota_ledger
    application.state.rate_limiter = rate_limiter

    def record_request(
        request_id: str,
        api_key_id: str | None,
        timestamp_utc: str,
        status: int,
        fields: dict[str, object] | None = None,
        failure_category: str | None = None,
    ) -> None:
        values = fields or {}
        audit_log.record(
            AuditRecord(
                request_id=request_id,
                api_key_id=api_key_id,
                timestamp_utc=timestamp_utc,
                task_type=values.get("task_type"),
                effective_sensitivity=values.get("effective_sensitivity"),
                selected_model=values.get("selected_model"),
                selected_tier=values.get("selected_tier"),
                status=status,
                retry_count=int(values.get("retry_count", 0)),
                attempt_count=int(values.get("attempt_count", 0)),
                fallback_used=bool(values.get("fallback_used", False)),
                input_tokens=values.get("input_tokens"),
                output_tokens=values.get("output_tokens"),
                estimated_cost_usd=values.get("estimated_cost_usd"),
                latency_ms=values.get("latency_ms"),
                failure_category=failure_category,
            )
        )

    @application.middleware("http")
    async def authenticate_rate_limit_and_audit(request: Request, call_next):
        if request.url.path != "/route" or request.method != "POST":
            return await call_next(request)

        request_id = str(uuid4())
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        key_id = authenticator.authenticate(request.headers.get("X-API-Key"))
        if key_id is None:
            record_request(
                request_id,
                None,
                timestamp_utc,
                401,
                failure_category="authentication",
            )
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})

        if not rate_limiter.allow(key_id):
            record_request(
                request_id,
                key_id,
                timestamp_utc,
                429,
                failure_category="rate_limit",
            )
            return JSONResponse(status_code=429, content={"detail": "Request rate limit exceeded."})

        request.state.request_id = request_id
        request.state.api_key_id = key_id
        request.state.audit_timestamp_utc = timestamp_utc
        request.state.audit_fields = {}
        request.state.audit_failure_category = None
        try:
            response = await call_next(request)
        except Exception:
            record_request(
                request_id,
                key_id,
                timestamp_utc,
                500,
                getattr(request.state, "audit_fields", {}),
                "internal_error",
            )
            raise

        failure_category = getattr(request.state, "audit_failure_category", None)
        if response.status_code == 422 and failure_category is None:
            failure_category = "validation"
        elif response.status_code == 403 and failure_category is None:
            failure_category = "policy_denial"
        elif response.status_code == 429 and failure_category is None:
            failure_category = "quota"
        elif response.status_code in {502, 503, 504} and failure_category is None:
            failure_category = "provider_failure"
        record_request(
            request_id,
            key_id,
            timestamp_utc,
            response.status_code,
            getattr(request.state, "audit_fields", {}),
            failure_category,
        )
        return response

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/route", response_model=RouteResponse)
    async def route(request: Request, route_request: RouteRequest) -> RouteResponse:
        started = perf_counter()
        detected = detect_sensitivity(route_request.prompt)
        effective = effective_sensitivity(route_request.sensitivity, detected)
        fields: dict[str, object] = {
            "task_type": route_request.task_type.value,
            "effective_sensitivity": effective.value,
            "input_tokens": estimate_tokens(route_request.prompt),
        }
        request.state.audit_fields = fields

        try:
            decisions = rank_models(route_request, effective, model_catalog)
        except PolicyDenied:
            request.state.audit_failure_category = "policy_denial"
            raise HTTPException(
                status_code=403,
                detail=f"No configured model is approved for {effective.value} data.",
            ) from None
        except NoEligibleModel:
            request.state.audit_failure_category = "eligibility"
            raise HTTPException(
                status_code=422,
                detail="No approved model meets the task, context, and cost constraints.",
            ) from None

        primary = decisions[0]
        fallback = decisions[1] if len(decisions) > 1 else None
        # Reserve the maximum possible attempt cost: primary + retry + fallback,
        # or two fallback attempts if the primary circuit is already open.
        primary_cost = primary.estimated_cost_usd
        fallback_cost = fallback.estimated_cost_usd if fallback is not None else 0.0
        reserve_cost = max(
            2 * primary_cost + fallback_cost,
            2 * fallback_cost if fallback is not None else 0.0,
        )
        reserve_large = int(
            primary.model.tier == "large"
            or (fallback is not None and fallback.model.tier == "large")
        )
        fields.update(
            selected_model=primary.model.model_id,
            selected_tier=primary.model.tier,
        )
        try:
            reservation = quota_ledger.reserve(
                request.state.request_id,
                request.state.api_key_id,
                reserve_cost,
                reserve_large,
            )
        except QuotaExceeded:
            request.state.audit_failure_category = "quota"
            raise HTTPException(
                status_code=429,
                detail="Daily usage quota exceeded.",
            ) from None

        try:
            result = await executor.execute(route_request, effective, model_catalog)
        except ProviderUnavailableError as exc:
            actual_cost = sum(
                estimate_cost(model, estimate_tokens(route_request.prompt), route_request.max_output_tokens)
                for model in exc.attempted_models
            )
            actual_large = int(any(model.tier == "large" for model in exc.attempted_models))
            quota_ledger.reconcile(reservation, actual_cost, actual_large)
            latency_ms = (perf_counter() - started) * 1000
            fields.update(
                retry_count=exc.retry_count,
                attempt_count=exc.attempt_count,
                input_tokens=estimate_tokens(route_request.prompt),
                estimated_cost_usd=actual_cost,
                latency_ms=latency_ms,
            )
            request.state.audit_failure_category = {
                502: "provider_failure",
                503: "circuit_open",
                504: "provider_timeout",
            }.get(exc.status_code, "provider_failure")
            raise HTTPException(
                status_code=exc.status_code,
                detail="Eligible provider execution failed or is temporarily unavailable.",
            ) from None
        except Exception:
            # Executor converts provider exceptions; an unexpected pre-result error releases
            # the reservation so a local programming failure does not consume daily quota.
            quota_ledger.reconcile(reservation, 0.0, 0)
            request.state.audit_failure_category = "internal_error"
            raise

        actual_cost = sum(
            estimate_cost(model, result.decision.input_tokens, route_request.max_output_tokens)
            for model in result.attempted_models
        )
        actual_large = int(any(model.tier == "large" for model in result.attempted_models))
        quota_ledger.reconcile(reservation, actual_cost, actual_large)
        latency_ms = (perf_counter() - started) * 1000
        output_tokens = ceil(len(result.output) / 4)
        decision_reason = result.decision.reason
        if result.fallback_used:
            decision_reason += " Used as the single eligible fallback after primary unavailability."
        fields.update(
            selected_model=result.decision.model.model_id,
            selected_tier=result.decision.model.tier,
            retry_count=result.retry_count,
            attempt_count=result.attempt_count,
            fallback_used=result.fallback_used,
            input_tokens=result.decision.input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=actual_cost,
            latency_ms=latency_ms,
        )
        return RouteResponse(
            output=result.output,
            model_used=result.decision.model.model_id,
            final_sensitivity=result.decision.effective_sensitivity,
            decision_reason=decision_reason,
            fallback_used=result.fallback_used,
            latency_ms=latency_ms,
            estimated_cost_usd=actual_cost,
            input_tokens=result.decision.input_tokens,
            output_tokens=output_tokens,
            retry_count=result.retry_count,
            attempt_count=result.attempt_count,
        )

    return application


app = create_app()
