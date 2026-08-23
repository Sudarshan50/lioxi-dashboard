from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "portal"
    postgres_password: str = "portal"
    postgres_db: str = "llm_portal"
    postgres_host: str = "db"
    postgres_port: int = 5432

    encryption_key: str
    jwt_secret: str
    jwt_expire_minutes: int = 720

    admin_username: str = "admin"
    admin_password: str = "change_me"

    sync_interval_minutes: int = 15
    sync_lookback_hours: int = 168
    usd_inr_rate: float | None = None

    new_api_base_url: str = ""
    new_api_system_token: str | None = None
    new_api_user_id: int = 1
    new_api_quota_per_unit: float = 500_000

    new_api2_base_url: str = ""
    new_api2_system_token: str | None = None
    new_api2_user_id: int = 1
    new_api2_tag_filter: str = ""
    new_api2_proxy: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_admin_ids: str = ""  # comma-separated Telegram user ids allowed to command the bot

    @property
    def telegram_admin_id_set(self) -> set[str]:
        return {part.strip() for part in self.telegram_admin_ids.split(",") if part.strip()}

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
