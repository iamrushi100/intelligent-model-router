"""FastAPI application entry point."""

from fastapi import FastAPI

from app.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    application = FastAPI(title=app_settings.app_name)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
