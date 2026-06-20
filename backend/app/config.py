"""Application configuration, loaded from the environment / `.env`."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# `.env` lives at the backend root (one level above the `app` package).
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Project settings. Real values come from `.env`; the default keeps the
    package importable (and Alembic's offline SQL render working) before the
    Postgres password has been wired in."""

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH, env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/betstats"
    )


settings = Settings()
