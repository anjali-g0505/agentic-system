"""The only place configuration is read. Nothing else calls os.getenv()."""
from __future__ import annotations

import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # No defaults: every value comes from .env (via compose) so nothing can drift from it.
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str
    JWT_EXPIRY_MINUTES: int
    GROQ_API_KEY: str
    GROQ_MODEL_GENERATION: str
    GROQ_MODEL_ROUTING: str
    EMBED_MODEL: str
    EMBED_DIM: int
    DATA_DIR: str
    CIRCULARS_DIR: str
    CHUNKS_PATH: str


def _load() -> Settings:
    try:
        s = Settings()
    except ValidationError as e:
        missing = ", ".join(str(err["loc"][0]) for err in e.errors())
        sys.exit(f"CONFIG ERROR: missing or invalid settings: {missing}. Check .env and docker-compose env_file.")
    if not s.GROQ_API_KEY.strip():
        sys.exit("CONFIG ERROR: GROQ_API_KEY is empty. Export it in the shell before `docker compose up` "
                 "(it is passed through by compose and never written to a file).")
    if "${" in s.DATABASE_URL:
        sys.exit("CONFIG ERROR: DATABASE_URL was not interpolated. Run through docker compose, which expands it.")
    return s


settings = _load()
