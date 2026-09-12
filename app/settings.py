from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DOMUS Guardian"
    # ``APP_VERSION`` is supplied by the Home Assistant App manifest.  Keeping
    # a production-safe default makes local tests and manual starts consistent
    # with the packaged release too.
    app_version: str = "1.0.4"

    database_url: str

    ha_url: str = "http://supervisor/core"
    ha_ws_url: str = "ws://supervisor/core/websocket"
    ha_token: str = ""
    ha_request_timeout_seconds: float = 10
    ha_verify_ssl: bool = False

    # Hostname, not the Docker bridge IP: the IP is not guaranteed stable
    # across restarts (see docs/DER-2026-09-05-guardian-core-identities.md
    # in domus-platform, "prossimi passi" #5).
    core_base_url: str = "http://local-domus-core:8000"
    core_request_timeout_seconds: float = 5

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
