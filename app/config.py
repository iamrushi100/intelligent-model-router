"""Small, validated settings loaded from environment variables."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class Settings:
    app_name: str = "Intelligent Model Router"
    environment: str = "development"
    max_prompt_chars: int = 20_000
    max_output_tokens: int = 2_048
    max_request_body_bytes: int = 65_536
    provider_timeout_seconds: float = 15.0
    provider_retry_delay_seconds: float = 1.0
    api_keys: tuple[str, ...] = field(default=(), repr=False)
    rate_limit_requests: int = 30
    rate_limit_window_seconds: float = 60.0
    daily_cost_limit_usd: float = 1.0
    daily_large_tier_limit: int = 10
    database_path: str = "router_audit.sqlite3"

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
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be positive")
        if self.provider_retry_delay_seconds < 0:
            raise ValueError("provider_retry_delay_seconds must not be negative")
        if any(not key for key in self.api_keys):
            raise ValueError("api_keys must not contain empty values")
        if self.environment == "production" and not self.api_keys:
            raise ValueError("at least one API key is required in production")
        if self.rate_limit_requests < 1 or self.rate_limit_window_seconds <= 0:
            raise ValueError("rate limit settings must be positive")
        if self.daily_cost_limit_usd <= 0 or self.daily_large_tier_limit < 0:
            raise ValueError("daily quota settings are invalid")
        if not self.database_path.strip():
            raise ValueError("database_path must not be empty")


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
            provider_timeout_seconds=float(values.get("ROUTER_PROVIDER_TIMEOUT_SECONDS", "15")),
            provider_retry_delay_seconds=float(
                values.get("ROUTER_PROVIDER_RETRY_DELAY_SECONDS", "1")
            ),
            api_keys=tuple(
                key.strip()
                for key in values.get("ROUTER_API_KEYS", "").split(",")
                if key.strip()
            ),
            rate_limit_requests=int(values.get("ROUTER_RATE_LIMIT_REQUESTS", "30")),
            rate_limit_window_seconds=float(
                values.get("ROUTER_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
            daily_cost_limit_usd=float(values.get("ROUTER_DAILY_COST_LIMIT_USD", "1")),
            daily_large_tier_limit=int(values.get("ROUTER_DAILY_LARGE_TIER_LIMIT", "10")),
            database_path=values.get("ROUTER_DATABASE_PATH", "router_audit.sqlite3"),
        )
    except ValueError as exc:
        raise ValueError(f"Invalid router configuration: {exc}") from exc
