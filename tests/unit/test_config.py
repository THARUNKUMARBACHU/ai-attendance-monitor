import pytest
from pydantic import SecretStr, ValidationError

from attendance_ai.core.config import Settings

from ..support import make_settings


def test_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make_settings(jwt_secret="too-short")


def test_rejects_dev_login_in_production() -> None:
    with pytest.raises(ValidationError, match="AUTH_DEV_LOGIN_ENABLED"):
        make_settings(app_env="prod", auth_dev_login_enabled=True)


def test_secrets_never_appear_in_repr() -> None:
    settings = make_settings(database_url="postgresql+psycopg://ai_app:hunter2@db:5432/attendance_ai")
    assert "hunter2" not in repr(settings)
    assert isinstance(settings.database_url, SecretStr)


def test_default_database_urls_carry_no_password() -> None:
    settings = make_settings()
    for url in (settings.database_url, settings.database_query_url, settings.database_owner_url):
        assert ":@" not in url.get_secret_value()
        assert url.get_secret_value().count("@") == 1
        userinfo = url.get_secret_value().split("//", 1)[1].split("@", 1)[0]
        assert ":" not in userinfo


def test_llm_api_key_follows_provider() -> None:
    settings = make_settings(llm_provider="openai", openai_api_key="sk-test", openrouter_api_key="sk-or-test")
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "sk-test"
    assert isinstance(Settings.model_fields["seed_file"].default.name, str)
