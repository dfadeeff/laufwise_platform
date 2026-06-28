"""Typed application settings, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    # Comma-separated list of allowed CORS origins.
    cors_origins: str = "http://localhost:3000"

    # Where the local engine writes episode logs / run artifacts.
    runs_dir: str = "./runs"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# Single shared instance — import this, don't re-read the environment elsewhere.
settings = Settings()