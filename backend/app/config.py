"""Typed application settings, loaded from environment / .env."""

from __future__ import annotations

import re
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    # Comma-separated list of allowed CORS origins.
    cors_origins: str = "http://localhost:3000"

    # Where the local engine writes episode logs / run artifacts.
    runs_dir: str = "./runs"

    # Where template contracts can be seeded from (Stage 1 source; seeded into the DB in Stage 2).
    templates_dir: str = "./runbooks"

    # --- database (ADR-0001: Supabase EU Postgres, DIRECT connection on 5432) ---
    # Either set DATABASE_URL explicitly (postgresql+asyncpg://...), or provide the Supabase
    # password + URL and the direct asyncpg URL is derived.
    database_url: str | None = None
    supabase_pwd: str | None = Field(default=None, validation_alias="SUPABASE_PWD")
    supabase_url: str | None = Field(default=None, validation_alias="NEXT_PUBLIC_SUPABASE_URL")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """Async SQLAlchemy URL. Prefers DATABASE_URL; otherwise derives the Supabase direct
        (5432) connection from the password + project ref (ADR-0001: direct, not the pooler)."""
        if self.database_url:
            return self.database_url
        if not (self.supabase_pwd and self.supabase_url):
            raise RuntimeError(
                "No database configured: set DATABASE_URL, or SUPABASE_PWD + NEXT_PUBLIC_SUPABASE_URL"
            )
        match = re.search(r"https://([a-z0-9]+)\.supabase", self.supabase_url)
        if not match:
            raise RuntimeError(f"cannot parse Supabase project ref from {self.supabase_url!r}")
        ref = match.group(1)
        pwd = quote(self.supabase_pwd, safe="")
        return f"postgresql+asyncpg://postgres:{pwd}@db.{ref}.supabase.co:5432/postgres"


# Single shared instance — import this, don't re-read the environment elsewhere.
settings = Settings()