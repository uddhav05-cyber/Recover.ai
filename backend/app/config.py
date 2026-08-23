"""Application configuration loaded from the environment / backend/.env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Values are read from environment variables, falling back to a local
    ``.env`` file (see ``.env.example``). Names are case-insensitive.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://user:password@host:5432/recoverai",
        description="Postgres URL. A plain postgres:// URL is normalized to asyncpg.",
    )

    # --- Razorpay (test mode) ----------------------------------------------
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None

    # --- Vertex AI / Gemini (Phase 3+) -------------------------------------
    google_application_credentials: str | None = None
    vertex_project_id: str | None = None
    vertex_location: str = "us-central1"
    gemini_model: str = "gemini-2.5-flash"

    # --- App ----------------------------------------------------------------
    app_env: str = "dev"
    log_level: str = "INFO"

    @property
    def async_database_url(self) -> str:
        """Normalize the configured URL to the ``postgresql+asyncpg`` scheme."""
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
