import pytest

from app.config import Settings, load_settings


def test_settings_load_defaults() -> None:
    settings = load_settings({})

    assert settings == Settings()


def test_settings_load_environment_values() -> None:
    settings = load_settings(
        {
            "ROUTER_APP_NAME": "Router Test",
            "ROUTER_ENVIRONMENT": "test",
            "ROUTER_MAX_PROMPT_CHARS": "1000",
        }
    )

    assert settings.app_name == "Router Test"
    assert settings.environment == "test"
    assert settings.max_prompt_chars == 1000


@pytest.mark.parametrize(
    "environment",
    [
        {"ROUTER_ENVIRONMENT": "staging"},
        {"ROUTER_MAX_PROMPT_CHARS": "not-a-number"},
        {"ROUTER_MAX_OUTPUT_TOKENS": "0"},
    ],
)
def test_settings_reject_invalid_configuration(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="Invalid router configuration"):
        load_settings(environment)
