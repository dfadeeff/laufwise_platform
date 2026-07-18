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

    # --- Clerk auth (org = tenant, ADR-0003 D2) ---
    # The publishable key encodes the Frontend API host, from which the JWT issuer + JWKS URL
    # are derived — so no separate issuer config is needed. Secret key is unused for token
    # verification (JWTs verify against JWKS); kept for later Clerk backend-API calls.
    clerk_publishable_key: str | None = Field(
        default=None, validation_alias="NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
    )
    clerk_secret_key: str | None = Field(default=None, validation_alias="CLERK_SECRET_KEY")

    # --- connections (ADR-0003) ---
    # Fernet key (urlsafe base64) for encrypting connection credentials at rest. Generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    connection_enc_key: str | None = Field(default=None, validation_alias="CONNECTION_ENC_KEY")
    # Base URL of the thevea app API (the GraphQL endpoint is <base>/graphql).
    thevea_base_url: str = "https://mein.thevea.de"
    # Source admin API (ADR-0004). Preset source credentials for the demo can be provided via
    # env so the source connection can be seeded without typing them each time.
    healthyfeet_base_url: str = "https://www.healthyfeet-podologie.de/api/admin"
    healthyfeet_admin_user: str | None = Field(default=None, validation_alias="HEALTHYFEET_ADMIN_USER")
    healthyfeet_admin_password: str | None = Field(
        default=None, validation_alias="HEALTHYFEET_ADMIN_PASSWORD"
    )
    # doctolib Pro — a second import source into the same thevea destination. The agenda API lives
    # under this host (the Keycloak login is at auth.doctolib.de, handled inside the connector).
    doctolib_base_url: str = "https://pro.doctolib.de"

    # Comma-separated list of allowed CORS origins. Includes :3001 so a dev server that lands on
    # the next port (when :3000 is taken) isn't blocked by CORS.
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

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
    def clerk_issuer(self) -> str | None:
        """Derive the Clerk JWT issuer from the publishable key. The key is
        `pk_<env>_<base64(frontend_api_host + '$')>`; the host is the issuer authority."""
        if not self.clerk_publishable_key:
            return None
        import base64

        b64 = self.clerk_publishable_key.split("_", 2)[-1]
        host = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode().rstrip("$")
        return f"https://{host}"

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