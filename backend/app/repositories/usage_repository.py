from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_snapshot import CostSnapshot
from app.models.model_catalog import MonitoredModel
from app.models.provider_account import ProviderAccount
from app.models.registered_model import RegisteredModel
from app.models.usage_snapshot import UsageSnapshot

_SNAPSHOT_UPDATE_FIELDS = (
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "total_tokens",
    "request_count",
    "estimated_cost_usd",
)


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_snapshot(self, **fields) -> None:
        stmt = insert(UsageSnapshot).values(**fields)
        update_fields = {name: getattr(stmt.excluded, name) for name in _SNAPSHOT_UPDATE_FIELDS}
        stmt = stmt.on_conflict_do_update(index_elements=["monitored_model_id", "bucket_start"], set_=update_fields)
        await self._session.execute(stmt)
        await self._session.commit()

    async def upsert_cost_snapshot(self, **fields) -> None:
        stmt = insert(CostSnapshot).values(**fields)
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider_account_id", "usage_date"],
            set_={"actual_cost_usd": stmt.excluded.actual_cost_usd, "currency": stmt.excluded.currency},
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_overview(
        self, start: datetime, end: datetime, account_ids: list[int] | None, model_id: int | None
    ) -> dict:
        query = select(
            func.coalesce(func.sum(UsageSnapshot.total_tokens), 0),
            func.coalesce(func.sum(UsageSnapshot.prompt_tokens), 0),
            func.coalesce(func.sum(UsageSnapshot.completion_tokens), 0),
            func.coalesce(func.sum(UsageSnapshot.cached_tokens), 0),
            func.coalesce(func.sum(UsageSnapshot.request_count), 0),
            func.coalesce(func.sum(UsageSnapshot.estimated_cost_usd), 0.0),
        ).where(UsageSnapshot.bucket_start >= start, UsageSnapshot.bucket_start < end)
        query = _apply_scope(query, account_ids, model_id)
        total_tokens, prompt_tokens, completion_tokens, cached_tokens, requests, estimated_cost = (
            await self._session.execute(query)
        ).one()

        actual_cost, actual_cost_currency = await self._get_actual_cost(start, end, account_ids)

        return {
            "total_tokens": int(total_tokens),
            "total_prompt_tokens": int(prompt_tokens),
            "total_completion_tokens": int(completion_tokens),
            "total_cached_tokens": int(cached_tokens),
            "total_requests": int(requests),
            "estimated_cost_usd": float(estimated_cost),
            "actual_cost": actual_cost,
            "actual_cost_currency": actual_cost_currency,
        }

    async def _get_actual_cost(
        self, start: datetime, end: datetime, account_ids: list[int] | None
    ) -> tuple[float, str]:
        """Azure Cost Management reports in each account's own billing currency, which
        is not necessarily USD. Summing amounts across different currencies would be
        meaningless, so when multiple currencies are present in scope we only report
        the one contributing the largest amount rather than silently mixing them.
        """
        query = select(CostSnapshot.currency, func.sum(CostSnapshot.actual_cost_usd)).where(
            CostSnapshot.usage_date >= start.date(), CostSnapshot.usage_date < end.date()
        )
        if account_ids is not None:
            query = query.where(CostSnapshot.provider_account_id.in_(account_ids))
        query = query.group_by(CostSnapshot.currency)

        rows = (await self._session.execute(query)).all()
        if not rows:
            return 0.0, "USD"
        currency, amount = max(rows, key=lambda row: row[1])
        return float(amount), currency

    async def get_actual_cost_by_account(
        self, start: datetime, end: datetime, account_ids: list[int] | None
    ) -> dict[int, tuple[float, str]]:
        query = select(
            CostSnapshot.provider_account_id,
            CostSnapshot.currency,
            func.coalesce(func.sum(CostSnapshot.actual_cost_usd), 0.0),
        ).where(CostSnapshot.usage_date >= start.date(), CostSnapshot.usage_date < end.date())
        if account_ids is not None:
            query = query.where(CostSnapshot.provider_account_id.in_(account_ids))
        query = query.group_by(CostSnapshot.provider_account_id, CostSnapshot.currency)
        by_account: dict[int, tuple[float, str]] = {}
        for account_id, currency, amount in (await self._session.execute(query)).all():
            current = by_account.get(account_id)
            if current is None or float(amount) > current[0]:
                by_account[account_id] = (float(amount), currency or "USD")
        return by_account

    async def get_timeseries(
        self, start: datetime, end: datetime, account_ids: list[int] | None, model_id: int | None
    ) -> list[dict]:
        query = select(
            UsageSnapshot.bucket_start,
            func.sum(UsageSnapshot.prompt_tokens),
            func.sum(UsageSnapshot.completion_tokens),
            func.sum(UsageSnapshot.total_tokens),
            func.sum(UsageSnapshot.request_count),
            func.sum(UsageSnapshot.estimated_cost_usd),
        ).where(UsageSnapshot.bucket_start >= start, UsageSnapshot.bucket_start < end)
        query = _apply_scope(query, account_ids, model_id)
        query = query.group_by(UsageSnapshot.bucket_start).order_by(UsageSnapshot.bucket_start)

        rows = (await self._session.execute(query)).all()
        return [
            {
                "bucket": bucket.isoformat(),
                "prompt_tokens": int(prompt),
                "completion_tokens": int(completion),
                "total_tokens": int(total),
                "requests": int(requests),
                "estimated_cost_usd": float(cost),
            }
            for bucket, prompt, completion, total, requests, cost in rows
        ]

    async def get_breakdown_by_account(
        self, start: datetime, end: datetime, model_id: int | None = None, account_ids: list[int] | None = None
    ) -> list[dict]:
        join_condition = (
            (UsageSnapshot.provider_account_id == ProviderAccount.id)
            & (UsageSnapshot.bucket_start >= start)
            & (UsageSnapshot.bucket_start < end)
        )
        if model_id is not None:
            join_condition = join_condition & _registered_model_match(model_id)
        query = (
            select(
                ProviderAccount.id,
                ProviderAccount.name,
                func.coalesce(func.sum(UsageSnapshot.total_tokens), 0),
                func.coalesce(func.sum(UsageSnapshot.request_count), 0),
                func.coalesce(func.sum(UsageSnapshot.estimated_cost_usd), 0.0),
            )
            .select_from(ProviderAccount)
            .outerjoin(UsageSnapshot, join_condition)
        )
        if account_ids is not None:
            query = query.where(ProviderAccount.id.in_(account_ids))
        query = query.group_by(ProviderAccount.id, ProviderAccount.name).order_by(ProviderAccount.name)
        return _to_breakdown(await self._session.execute(query))

    async def get_breakdown_by_model(
        self, start: datetime, end: datetime, account_ids: list[int] | None, model_id: int | None = None
    ) -> list[dict]:
        """Aggregates by registered model, not by individual account deployment -
        the same model deployed under five accounts is one bar, not five
        identically-labelled ones.
        """
        query = (
            select(
                RegisteredModel.id,
                RegisteredModel.name,
                func.coalesce(func.sum(UsageSnapshot.total_tokens), 0),
                func.coalesce(func.sum(UsageSnapshot.request_count), 0),
                func.coalesce(func.sum(UsageSnapshot.estimated_cost_usd), 0.0),
            )
            .select_from(RegisteredModel)
            .outerjoin(MonitoredModel, MonitoredModel.registered_model_id == RegisteredModel.id)
            .outerjoin(
                UsageSnapshot,
                (UsageSnapshot.monitored_model_id == MonitoredModel.id)
                & (UsageSnapshot.bucket_start >= start)
                & (UsageSnapshot.bucket_start < end),
            )
        )
        if account_ids is not None:
            query = query.where(MonitoredModel.provider_account_id.in_(account_ids))
        if model_id is not None:
            query = query.where(RegisteredModel.id == model_id)
        query = query.group_by(RegisteredModel.id, RegisteredModel.name).order_by(RegisteredModel.name)
        return _to_breakdown(await self._session.execute(query))

    async def get_breakdown_by_monitored_model(
        self, start: datetime, end: datetime, account_ids: list[int] | None
    ) -> list[dict]:
        query = (
            select(
                MonitoredModel.id,
                RegisteredModel.name,
                func.coalesce(func.sum(UsageSnapshot.total_tokens), 0),
                func.coalesce(func.sum(UsageSnapshot.request_count), 0),
                func.coalesce(func.sum(UsageSnapshot.estimated_cost_usd), 0.0),
                MonitoredModel.provider_account_id,
            )
            .select_from(MonitoredModel)
            .join(RegisteredModel, MonitoredModel.registered_model_id == RegisteredModel.id)
            .outerjoin(
                UsageSnapshot,
                (UsageSnapshot.monitored_model_id == MonitoredModel.id)
                & (UsageSnapshot.bucket_start >= start)
                & (UsageSnapshot.bucket_start < end),
            )
        )
        if account_ids is not None:
            query = query.where(MonitoredModel.provider_account_id.in_(account_ids))
        query = query.group_by(MonitoredModel.id, RegisteredModel.name, MonitoredModel.provider_account_id).order_by(
            RegisteredModel.name
        )
        rows = (await self._session.execute(query)).all()
        return [
            {
                "id": row_id,
                "name": name,
                "total_tokens": int(tokens),
                "requests": int(requests),
                "estimated_cost_usd": float(cost),
                "provider_account_id": account_id,
            }
            for row_id, name, tokens, requests, cost, account_id in rows
        ]

    async def get_timeseries_by_account(
        self, start: datetime, end: datetime, account_ids: list[int] | None, model_id: int | None
    ) -> list[dict]:
        query = (
            select(
                UsageSnapshot.bucket_start,
                ProviderAccount.id,
                ProviderAccount.name,
                func.coalesce(func.sum(UsageSnapshot.total_tokens), 0),
            )
            .select_from(UsageSnapshot)
            .join(ProviderAccount, ProviderAccount.id == UsageSnapshot.provider_account_id)
            .where(UsageSnapshot.bucket_start >= start, UsageSnapshot.bucket_start < end)
        )
        query = _apply_scope(query, account_ids, model_id)
        query = query.group_by(UsageSnapshot.bucket_start, ProviderAccount.id, ProviderAccount.name)
        query = query.order_by(UsageSnapshot.bucket_start, ProviderAccount.name)
        rows = (await self._session.execute(query)).all()
        return [
            {
                "bucket": bucket.isoformat(),
                "account_id": account_id,
                "account_name": name,
                "tpm": float(tokens) / 60,
            }
            for bucket, account_id, name, tokens in rows
        ]

    async def get_export_rows(
        self, start: datetime, end: datetime, account_ids: list[int] | None, model_id: int | None
    ) -> list[dict]:
        """One row per account deployment: name, endpoint, model, tokens, cost."""
        query = (
            select(
                ProviderAccount.name,
                ProviderAccount.endpoint,
                MonitoredModel.deployment_name,
                RegisteredModel.name,
                func.coalesce(func.sum(UsageSnapshot.prompt_tokens), 0),
                func.coalesce(func.sum(UsageSnapshot.completion_tokens), 0),
                func.coalesce(func.sum(UsageSnapshot.total_tokens), 0),
                func.coalesce(func.sum(UsageSnapshot.estimated_cost_usd), 0.0),
            )
            .select_from(ProviderAccount)
            .outerjoin(MonitoredModel, MonitoredModel.provider_account_id == ProviderAccount.id)
            .outerjoin(RegisteredModel, RegisteredModel.id == MonitoredModel.registered_model_id)
            .outerjoin(
                UsageSnapshot,
                (UsageSnapshot.monitored_model_id == MonitoredModel.id)
                & (UsageSnapshot.bucket_start >= start)
                & (UsageSnapshot.bucket_start < end),
            )
        )
        if account_ids is not None:
            query = query.where(ProviderAccount.id.in_(account_ids))
        if model_id is not None:
            query = query.where(RegisteredModel.id == model_id)
        query = query.group_by(
            ProviderAccount.id,
            ProviderAccount.name,
            ProviderAccount.endpoint,
            MonitoredModel.deployment_name,
            RegisteredModel.name,
        ).order_by(ProviderAccount.name, MonitoredModel.deployment_name)
        rows = (await self._session.execute(query)).all()
        return [
            {
                "name": account_name,
                "endpoint": endpoint or "",
                "endpoint_name": deployment_name or "",
                "model": model_name or "",
                "input_tokens": int(prompt),
                "output_tokens": int(completion),
                "total_tokens": int(total),
                "total_cost": round(float(cost), 6),
            }
            for account_name, endpoint, deployment_name, model_name, prompt, completion, total, cost in rows
        ]


def _registered_model_match(registered_model_id: int):
    return UsageSnapshot.monitored_model_id.in_(
        select(MonitoredModel.id).where(MonitoredModel.registered_model_id == registered_model_id)
    )


def _apply_scope(query, account_ids: list[int] | None, model_id: int | None):
    if account_ids is not None:
        query = query.where(UsageSnapshot.provider_account_id.in_(account_ids))
    if model_id is not None:
        query = query.where(_registered_model_match(model_id))
    return query


def _to_breakdown(result) -> list[dict]:
    return [
        {"id": row_id, "name": name, "total_tokens": int(tokens), "requests": int(requests), "estimated_cost_usd": float(cost)}
        for row_id, name, tokens, requests, cost in result.all()
    ]
