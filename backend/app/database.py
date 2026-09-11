import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)
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
    ("group_tag", "VARCHAR(8) NOT NULL DEFAULT 'sb'"),
)


async def _ensure_account_columns(conn) -> None:
    for name, definition in _ACCOUNT_EXTRA_COLUMNS:
        await conn.execute(text(f"ALTER TABLE provider_accounts ADD COLUMN IF NOT EXISTS {name} {definition}"))


async def _ensure_azure_openai_key_columns(conn) -> None:
    await conn.execute(text("ALTER TABLE azure_openai_keys ADD COLUMN IF NOT EXISTS owner_tag VARCHAR(64)"))


_SUBMIT_EXTRA_COLUMNS = (
    ("error_kind", "VARCHAR(16)"),
    ("auto_retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("credits_limit", "DOUBLE PRECISION"),
    ("credits_remaining", "DOUBLE PRECISION"),
    ("credits_used", "DOUBLE PRECISION"),
    ("credits_currency", "VARCHAR(8)"),
    ("credits_label", "VARCHAR(256)"),
    ("credits_available", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("credits_fetched_at", "TIMESTAMPTZ"),
    ("credits_error", "TEXT"),
    ("group_tag", "VARCHAR(8) NOT NULL DEFAULT 'sb'"),
)


async def _ensure_submit_columns(conn) -> None:
    for name, definition in _SUBMIT_EXTRA_COLUMNS:
        await conn.execute(text(f"ALTER TABLE sp_submit_requests ADD COLUMN IF NOT EXISTS {name} {definition}"))


async def _ensure_join_enrollee_group(conn) -> None:
    """Add group_tag and move the uniqueness from name to (name, group_tag).

    Existing names stay SB. The old unique index on name alone is dropped so
    the same person can be enrolled under both groups.
    """
    await conn.execute(
        text("ALTER TABLE join_enrollees ADD COLUMN IF NOT EXISTS group_tag VARCHAR(8) NOT NULL DEFAULT 'sb'")
    )
    # An older build declared this as an ORM UniqueConstraint, which owns the
    # same relation name and would make the CREATE UNIQUE INDEX below a no-op.
    await conn.execute(
        text("ALTER TABLE join_enrollees DROP CONSTRAINT IF EXISTS uq_join_enrollee_name_group")
    )
    await conn.execute(text("DROP INDEX IF EXISTS ix_join_enrollees_name"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_join_enrollees_name ON join_enrollees (name)"))
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_join_enrollees_group_tag ON join_enrollees (group_tag)")
    )
    # Refuse to fail the whole boot over pre-existing duplicates: log them and
    # leave the index absent so an operator can clean up and restart.
    duplicates = (
        await conn.execute(
            text(
                """
                SELECT lower(name) AS name, group_tag, count(*) AS n
                FROM join_enrollees
                GROUP BY lower(name), group_tag
                HAVING count(*) > 1
                """
            )
        )
    ).all()
    if duplicates:
        logger.error(
            "join_enrollees has duplicate (name, group) rows; unique index not created: %s",
            ", ".join(f"{row.name}/{row.group_tag} x{row.n}" for row in duplicates),
        )
        return
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_join_enrollee_name_group
            ON join_enrollees (lower(name), group_tag)
            """
        )
    )


async def _ensure_group_tag_indexes(conn) -> None:
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_provider_accounts_group_tag ON provider_accounts (group_tag)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_sp_submit_requests_group_tag ON sp_submit_requests (group_tag)")
    )


async def _ensure_submit_indexes(conn) -> None:
    """Create the unique live-subscription index, once.

    Everything here used to re-run on every boot: the DELETE destroyed rows at
    each restart, and the drop/recreate rebuilt the index under an ACCESS
    EXCLUSIVE lock held for the whole of init. Both only ever mattered while
    the index was missing, so skip them once it exists.
    """
    exists = (
        await conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_sp_submit_live_subscription'")
        )
    ).scalar()
    if exists:
        return
    # Drop extras so the unique live-subscription index can be created.
    await conn.execute(
        text(
            """
            DELETE FROM sp_submit_requests AS extra
            USING sp_submit_requests AS kept
            WHERE extra.status IN ('pending_approval', 'creating_sp', 'approving')
              AND kept.status IN ('pending_approval', 'creating_sp', 'approving')
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
            WHERE status IN ('pending_approval', 'creating_sp', 'approving')
              AND subscription_id IS NOT NULL
              AND btrim(subscription_id) <> ''
            """
        )
    )


ELEVATED_BACKFILL_KEY = "sp_elevated_access_backfilled"


async def _ensure_sp_elevated_access(conn) -> None:
    """Backfill elevated_access for pre-existing deploys, once.

    This re-ran on every boot and only ever sets TRUE, so an access grant an
    admin had deliberately revoked came back at the next restart. It is a
    one-time backfill of legacy rows, so record that it ran.
    """
    await conn.execute(
        text(
            "ALTER TABLE azure_service_principals ADD COLUMN IF NOT EXISTS elevated_access BOOLEAN NOT NULL DEFAULT FALSE"
        )
    )
    done = (
        await conn.execute(
            text("SELECT 1 FROM app_settings WHERE key = :key"), {"key": ELEVATED_BACKFILL_KEY}
        )
    ).scalar()
    if done:
        return
    await conn.execute(
        text(
            """
            UPDATE azure_service_principals AS sp
            SET elevated_access = TRUE
            WHERE EXISTS (
                    SELECT 1 FROM sp_submit_requests AS req
                    WHERE req.status = 'approved'
                      AND req.subscription_id IS NOT NULL
                      AND btrim(req.subscription_id) <> ''
                      AND lower(req.subscription_id) = lower(sp.subscription_id)
               )
               OR EXISTS (
                    SELECT 1 FROM provider_accounts AS acct
                    WHERE lower(acct.subscription_id) = lower(sp.subscription_id)
                      AND (
                        (lower(acct.resource_group) LIKE 'rg-%' AND lower(acct.resource_group) LIKE '%-kimi')
                        OR lower(acct.resource_name) LIKE '%-kimi-%'
                      )
               )
            """
        )
    )
    await conn.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES (:key, '1') ON CONFLICT (key) DO NOTHING"
        ),
        {"key": ELEVATED_BACKFILL_KEY},
    )


async def init_models() -> None:
    import app.models  # noqa: F401 - registers ORM models on Base.metadata before create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_account_columns(conn)
        await _ensure_azure_openai_key_columns(conn)
        await _ensure_submit_columns(conn)
        await _ensure_group_tag_indexes(conn)
        await _ensure_join_enrollee_group(conn)
        await _ensure_submit_indexes(conn)
        await _ensure_sp_elevated_access(conn)
