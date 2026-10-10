import pytest

from app.config import Settings, load_settings


def test_settings_load_defaults() -> None:
    settings = load_settings({})

    assert settings == Settings()
    assert settings.provider_timeout_seconds == 15
    assert settings.provider_retry_delay_seconds == 1


def test_settings_load_environment_values() -> None:
    settings = load_settings(
        {
            "ROUTER_APP_NAME": "Router Test",
            "ROUTER_ENVIRONMENT": "test",
            "ROUTER_MAX_PROMPT_CHARS": "1000",
            "ROUTER_PROVIDER_TIMEOUT_SECONDS": "2.5",
            "ROUTER_PROVIDER_RETRY_DELAY_SECONDS": "0",
        }
    )

    assert settings.app_name == "Router Test"
    assert settings.environment == "test"
    assert settings.max_prompt_chars == 1000
    assert settings.provider_timeout_seconds == 2.5
    assert settings.provider_retry_delay_seconds == 0


@pytest.mark.parametrize(
    "environment",
    [
        {"ROUTER_ENVIRONMENT": "staging"},
        {"ROUTER_MAX_PROMPT_CHARS": "not-a-number"},
        {"ROUTER_MAX_OUTPUT_TOKENS": "0"},
        {"ROUTER_PROVIDER_TIMEOUT_SECONDS": "0"},
        {"ROUTER_PROVIDER_RETRY_DELAY_SECONDS": "-1"},
    ],
)
def test_settings_reject_invalid_configuration(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="Invalid router configuration"):
        load_settings(environment)
