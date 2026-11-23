"""App settings, read from environment variables (or the .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/contracts"

    # Clerk
    clerk_secret_key: str = ""
    clerk_jwks_url: str = "https://api.clerk.com/v1/jwks"
    # Comma-separated list of frontend origins allowed in the token's `azp` claim.
    clerk_authorized_parties: str = "http://localhost:5173"

    # OpenRouter (OpenAI-compatible API)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    chat_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    embed_model: str = "nvidia/nemotron-3.5-embed-1b:free"
    embed_dim: int = 2048
    embed_batch_size: int = 32

    # "openrouter" for real calls, "fake" for offline tests and demos without keys.
    ai_provider: str = "openrouter"

    max_upload_mb: int = 20
    review_workers: int = 2
    cors_origins: str = "http://localhost:5173"

    @property
    def authorized_parties(self) -> list[str]:
        return [p.strip() for p in self.clerk_authorized_parties.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [p.strip() for p in self.cors_origins.split(",") if p.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
