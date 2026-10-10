"""FastAPI application entry point."""

from math import ceil
from time import perf_counter
from collections.abc import Mapping

from fastapi import FastAPI, HTTPException

from app.config import Settings, load_settings
from app.model_config import ModelCatalog, load_model_catalog
from app.providers import MockProvider, Provider
from app.reliability import ProviderExecutor, ProviderUnavailableError
from app.router import NoEligibleModel, PolicyDenied
from app.schemas import RouteRequest, RouteResponse
from app.sensitivity import detect_sensitivity, effective_sensitivity


def create_app(
    settings: Settings | None = None,
    catalog: ModelCatalog | None = None,
    provider: Provider | Mapping[str, Provider] | None = None,
) -> FastAPI:
    app_settings = settings or load_settings()
    application = FastAPI(title=app_settings.app_name)
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
    application.state.provider = (
        provider
        if provider is not None and not isinstance(provider, Mapping)
        else next(iter(providers.values()), None)
    )
    application.state.providers = providers
    application.state.model_catalog = model_catalog
    application.state.provider_executor = executor

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/route", response_model=RouteResponse)
    async def route(request: RouteRequest) -> RouteResponse:
        detected = detect_sensitivity(request.prompt)
        effective = effective_sensitivity(request.sensitivity, detected)
        started = perf_counter()
        try:
            result = await executor.execute(request, effective, model_catalog)
        except PolicyDenied:
            raise HTTPException(
                status_code=403,
                detail=f"No configured model is approved for {effective.value} data.",
            ) from None
        except NoEligibleModel:
            raise HTTPException(
                status_code=422,
                detail="No approved model meets the task, context, and cost constraints.",
            ) from None
        except ProviderUnavailableError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail="Eligible provider execution failed or is temporarily unavailable.",
            ) from None

        latency_ms = (perf_counter() - started) * 1000
        output_tokens = ceil(len(result.output) / 4)
        actual_estimated_cost = (
            result.decision.input_tokens * result.decision.model.input_price_per_million
            + output_tokens * result.decision.model.output_price_per_million
        ) / 1_000_000
        decision_reason = result.decision.reason
        if result.fallback_used:
            decision_reason += " Used as the single eligible fallback after primary unavailability."
        return RouteResponse(
            output=result.output,
            model_used=result.decision.model.model_id,
            final_sensitivity=result.decision.effective_sensitivity,
            decision_reason=decision_reason,
            fallback_used=result.fallback_used,
            latency_ms=latency_ms,
            estimated_cost_usd=actual_estimated_cost,
            input_tokens=result.decision.input_tokens,
            output_tokens=output_tokens,
            retry_count=result.retry_count,
            attempt_count=result.attempt_count,
        )

    return application


app = create_app()
