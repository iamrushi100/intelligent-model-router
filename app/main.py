"""FastAPI application entry point."""

from math import ceil
from time import perf_counter

from fastapi import FastAPI, HTTPException

from app.config import Settings, load_settings
from app.model_config import ModelCatalog, load_model_catalog
from app.providers import MockProvider, Provider
from app.router import NoEligibleModel, PolicyDenied, choose_model
from app.schemas import RouteRequest, RouteResponse
from app.sensitivity import detect_sensitivity, effective_sensitivity


def create_app(
    settings: Settings | None = None,
    catalog: ModelCatalog | None = None,
    provider: Provider | None = None,
) -> FastAPI:
    app_settings = settings or load_settings()
    application = FastAPI(title=app_settings.app_name)
    model_catalog = catalog or load_model_catalog()
    mock_provider = provider or MockProvider()
    application.state.provider = mock_provider
    application.state.model_catalog = model_catalog

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/route", response_model=RouteResponse)
    async def route(request: RouteRequest) -> RouteResponse:
        detected = detect_sensitivity(request.prompt)
        effective = effective_sensitivity(request.sensitivity, detected)
        try:
            decision = choose_model(request, effective, model_catalog)
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

        started = perf_counter()
        output = await mock_provider.generate(request, decision.model)
        latency_ms = (perf_counter() - started) * 1000
        output_tokens = ceil(len(output) / 4)
        actual_estimated_cost = (
            decision.input_tokens * decision.model.input_price_per_million
            + output_tokens * decision.model.output_price_per_million
        ) / 1_000_000
        return RouteResponse(
            output=output,
            model_used=decision.model.model_id,
            final_sensitivity=decision.effective_sensitivity,
            decision_reason=decision.reason,
            fallback_used=False,
            latency_ms=latency_ms,
            estimated_cost_usd=actual_estimated_cost,
            input_tokens=decision.input_tokens,
            output_tokens=output_tokens,
        )

    return application


app = create_app()
