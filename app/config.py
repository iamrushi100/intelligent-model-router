"""Small, validated settings loaded from environment variables."""

from collections.abc import Mapping
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_name: str = "Intelligent Model Router"
    environment: str = "development"
    max_prompt_chars: int = 20_000
    max_output_tokens: int = 2_048
    max_request_body_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not self.app_name.strip():
            raise ValueError("app_name must not be empty")
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        if self.max_prompt_chars < 1:
            raise ValueError("max_prompt_chars must be positive")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.max_request_body_bytes < 1:
            raise ValueError("max_request_body_bytes must be positive")


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings, using defaults when optional environment values are absent."""
    values = os.environ if environ is None else environ
    try:
        return Settings(
            app_name=values.get("ROUTER_APP_NAME", "Intelligent Model Router"),
            environment=values.get("ROUTER_ENVIRONMENT", "development"),
            max_prompt_chars=int(values.get("ROUTER_MAX_PROMPT_CHARS", "20000")),
            max_output_tokens=int(values.get("ROUTER_MAX_OUTPUT_TOKENS", "2048")),
            max_request_body_bytes=int(values.get("ROUTER_MAX_REQUEST_BODY_BYTES", "65536")),
        )
    except ValueError as exc:
        raise ValueError(f"Invalid router configuration: {exc}") from exc
