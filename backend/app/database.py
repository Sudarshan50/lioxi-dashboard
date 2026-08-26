from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        yield session


_ACCOUNT_EXTRA_COLUMNS = (
    ("credits_remaining", "DOUBLE PRECISION"),
    ("credits_used", "DOUBLE PRECISION"),
    ("credits_limit", "DOUBLE PRECISION"),
    ("credits_currency", "VARCHAR(8)"),
    ("credits_unit", "VARCHAR(16)"),
    ("credits_label", "VARCHAR(256)"),
    ("credits_available", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("credits_limit_manual", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("new_api_gateway", "VARCHAR(8)"),
    ("new_api_channel_id", "INTEGER"),
    ("new_api_name", "VARCHAR(128)"),
    ("new_api_tag", "VARCHAR(128)"),
    ("owner_tag", "VARCHAR(64)"),
    ("new_api_used_quota", "DOUBLE PRECISION"),
    ("new_api_cost_o1_usd", "DOUBLE PRECISION"),
    ("new_api_cost_o2_usd", "DOUBLE PRECISION"),
    ("new_api_cost_usd", "DOUBLE PRECISION"),
    ("new_api_status", "INTEGER"),
    ("new_api_status_o1", "INTEGER"),
    ("new_api_status_o2", "INTEGER"),
    ("new_api_weight", "INTEGER"),
    ("new_api_priority", "INTEGER"),
    ("new_api_synced_at", "TIMESTAMPTZ"),
    ("new_api_alert_level", "INTEGER NOT NULL DEFAULT 0"),
    ("payable_settled", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("payable_settled_at", "TIMESTAMPTZ"),
    ("at_cap_manual", "BOOLEAN NOT NULL DEFAULT FALSE"),
)


async def _ensure_account_columns(conn) -> None:
    for name, definition in _ACCOUNT_EXTRA_COLUMNS:
        await conn.execute(text(f"ALTER TABLE provider_accounts ADD COLUMN IF NOT EXISTS {name} {definition}"))


async def init_models() -> None:
    import app.models  # noqa: F401 - registers ORM models on Base.metadata before create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_account_columns(conn)
