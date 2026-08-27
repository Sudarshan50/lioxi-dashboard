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
    ("openai_api_key_encrypted", "TEXT"),
)


async def _ensure_account_columns(conn) -> None:
    for name, definition in _ACCOUNT_EXTRA_COLUMNS:
        await conn.execute(text(f"ALTER TABLE provider_accounts ADD COLUMN IF NOT EXISTS {name} {definition}"))


async def _ensure_azure_openai_key_columns(conn) -> None:
    await conn.execute(text("ALTER TABLE azure_openai_keys ADD COLUMN IF NOT EXISTS owner_tag VARCHAR(64)"))


async def _ensure_submit_columns(conn) -> None:
    await conn.execute(text("ALTER TABLE sp_submit_requests ADD COLUMN IF NOT EXISTS error_kind VARCHAR(16)"))


async def _ensure_submit_indexes(conn) -> None:
    # Drop extras so the unique live-subscription index can be created.
    await conn.execute(
        text(
            """
            DELETE FROM sp_submit_requests AS extra
            USING sp_submit_requests AS kept
            WHERE extra.status IN ('pending_approval', 'creating_sp')
              AND kept.status IN ('pending_approval', 'creating_sp')
              AND extra.subscription_id IS NOT NULL
              AND btrim(extra.subscription_id) <> ''
              AND lower(extra.subscription_id) = lower(kept.subscription_id)
              AND extra.id > kept.id
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_sp_submit_live_subscription
            ON sp_submit_requests (lower(subscription_id))
            WHERE status IN ('pending_approval', 'creating_sp')
              AND subscription_id IS NOT NULL
              AND btrim(subscription_id) <> ''
            """
        )
    )


async def init_models() -> None:
    import app.models  # noqa: F401 - registers ORM models on Base.metadata before create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_account_columns(conn)
        await _ensure_azure_openai_key_columns(conn)
        await _ensure_submit_columns(conn)
        await _ensure_submit_indexes(conn)
