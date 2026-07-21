from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DOMUS Guardian"
    app_version: str = "0.3.0"

    database_url: str

    ha_url: str = "http://supervisor/core"
    ha_ws_url: str = "ws://supervisor/core/websocket"
    ha_token: str = ""
    ha_request_timeout_seconds: float = 10
    ha_verify_ssl: bool = False

    watchdog_interval_seconds: int = 60
    watchdog_websocket_stale_minutes: int = 10
    watchdog_memory_threshold_mb: int = 512

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
