import csv
import io
import re
from datetime import datetime, timedelta, timezone

from app.repositories.account_group_repository import AccountGroupRepository
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.usage_repository import UsageRepository

_EXPORT_FIELDS = (
    "name",
    "endpoint",
    "endpoint_name",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "total_cost",
)

_RANGE_MAP = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}

# Azure Monitor TPM/RPM are 1-minute Totals. Our snapshots are PT1H sums, so
# an hour's equivalent rate is hourly_total / 60.
_MINUTES_PER_HOUR = 60


def throughput_rates(
    total_tokens: int,
    total_requests: int,
    start: datetime,
    end: datetime,
    hourly: list[dict],
) -> dict[str, float]:
    """Average and peak TPM/RPM from hourly Azure Monitor totals.

    Microsoft measures tokens-per-minute and requests-per-minute as the Total
    of Processed Inference Tokens / Azure OpenAI Requests over 1-minute
    windows, then reports min/avg/max of those windows.

    We only store hourly buckets, so:
    - hour rate = tokens_in_hour / 60  (and requests_in_hour / 60)
    - avg = mean of those hour rates over the full selected window, counting
      idle hours as zero, which equals window_total / window_minutes
    - peak = max hour rate, the closest quota comparison this grain allows
    """
    window_minutes = (end - start).total_seconds() / 60
    if window_minutes <= 0:
        window_minutes = _MINUTES_PER_HOUR
    peak_tokens = max((point["total_tokens"] for point in hourly), default=0)
    peak_requests = max((point["requests"] for point in hourly), default=0)
    return {
        "avg_tpm": total_tokens / window_minutes,
        "avg_rpm": total_requests / window_minutes,
        "peak_tpm": peak_tokens / _MINUTES_PER_HOUR,
        "peak_rpm": peak_requests / _MINUTES_PER_HOUR,
    }


class DashboardService:
    def __init__(
        self,
        usage_repository: UsageRepository,
        account_repository: AccountRepository,
        model_repository: ModelRepository,
        account_group_repository: AccountGroupRepository,
    ) -> None:
        self._usage_repository = usage_repository
        self._account_repository = account_repository
        self._model_repository = model_repository
        self._account_group_repository = account_group_repository

    async def get_overview(
        self,
        range_key: str,
        account_id: int | None,
        model_id: int | None,
        group_id: int | None = None,
        gateway: str | None = None,
    ) -> dict:
        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id, gateway)
        overview = await self._usage_repository.get_overview(start, end, account_ids, model_id)
        hourly = await self._usage_repository.get_timeseries(start, end, account_ids, model_id)
        overview.update(
            throughput_rates(
                overview["total_tokens"],
                overview["total_requests"],
                start,
                end,
                hourly,
            )
        )
        if account_ids is None:
            overview["accounts_count"] = await self._account_repository.count()
            overview["models_count"] = await self._model_repository.count()
        else:
            valid_ids = {i for i in account_ids if i > 0}
            overview["accounts_count"] = len(valid_ids)
            models = await self._model_repository.list_all()
            overview["models_count"] = sum(1 for model in models if model.provider_account_id in valid_ids)
        overview["estimated_cost"] = float(overview.get("estimated_cost_usd") or 0)
        overview["estimated_cost_currency"] = "USD"
        total, o1, o2 = await self._sum_new_api_cost(account_ids)
        if gateway == "O1":
            total = o1
        elif gateway == "O2":
            total = o2
        overview["new_api_cost"] = total
        overview["new_api_cost_o1"] = o1
        overview["new_api_cost_o2"] = o2
        overview["new_api_cost_currency"] = "USD"
        if model_id is not None:
            overview["actual_cost"] = None
        return overview

    async def _sum_new_api_cost(self, account_ids: list[int] | None) -> tuple[float, float, float]:
        accounts = await self._account_repository.list_all()
        scoped = [a for a in accounts if account_ids is None or a.id in account_ids]
        return (
            sum(a.new_api_cost_usd or 0 for a in scoped),
            sum(a.new_api_cost_o1_usd or 0 for a in scoped),
            sum(a.new_api_cost_o2_usd or 0 for a in scoped),
        )

    async def get_timeseries(
        self,
        range_key: str,
        account_id: int | None,
        model_id: int | None,
        group_id: int | None = None,
        gateway: str | None = None,
    ) -> list[dict]:
        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id, gateway)
        return await self._usage_repository.get_timeseries(start, end, account_ids, model_id)

    async def get_timeseries_by_account(
        self,
        range_key: str,
        account_id: int | None,
        model_id: int | None,
        group_id: int | None = None,
        gateway: str | None = None,
    ) -> list[dict]:
        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id, gateway)
        return await self._usage_repository.get_timeseries_by_account(start, end, account_ids, model_id)

    async def get_breakdown_by_account(
        self,
        range_key: str,
        model_id: int | None = None,
        account_id: int | None = None,
        group_id: int | None = None,
        gateway: str | None = None,
    ) -> list[dict]:
        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id, gateway)
        items = await self._usage_repository.get_breakdown_by_account(start, end, model_id, account_ids)
        minutes = max((end - start).total_seconds() / 60, 60)
        billed = await self._usage_repository.get_actual_cost_by_account(start, end, account_ids)
        accounts = await self._account_repository.list_all()
        new_api_by_account = {
            account.id: (account.new_api_cost_usd or 0, account.new_api_cost_o1_usd or 0, account.new_api_cost_o2_usd or 0)
            for account in accounts
        }
        credits_by_account = {
            account.id: (account.credits_limit, account.credits_currency or "USD") for account in accounts
        }
        for item in items:
            item["avg_tpm"] = item["total_tokens"] / minutes
            amount, currency = billed.get(item["id"], (0.0, "USD"))
            if model_id is not None:
                item["actual_cost"] = None
                item["actual_cost_currency"] = currency
            else:
                item["actual_cost"] = amount
                item["actual_cost_currency"] = currency
            total, o1, o2 = new_api_by_account.get(item["id"], (0.0, 0.0, 0.0))
            item["new_api_cost"] = total
            item["new_api_cost_o1"] = o1
            item["new_api_cost_o2"] = o2
            limit, limit_currency = credits_by_account.get(item["id"], (None, "USD"))
            item["credits_limit"] = limit
            item["credits_currency"] = limit_currency
            item["estimated_cost"] = float(item.get("estimated_cost_usd") or 0)
            item["currency"] = "USD"
        return items

    async def get_breakdown_by_model(
        self,
        range_key: str,
        account_id: int | None,
        group_id: int | None = None,
        model_id: int | None = None,
        gateway: str | None = None,
    ) -> list[dict]:
        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id, gateway)
        items = await self._usage_repository.get_breakdown_by_model(start, end, account_ids, model_id)
        _mark_estimated_usd(items)
        return items

    async def get_breakdown_by_monitored_model(
        self, range_key: str, account_id: int | None, group_id: int | None = None
    ) -> list[dict]:
        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id)
        items = await self._usage_repository.get_breakdown_by_monitored_model(start, end, account_ids)
        _mark_estimated_usd(items)
        for item in items:
            item["actual_cost"] = None
            item["actual_cost_currency"] = None
            item["new_api_cost"] = None
        return items

    async def export_csv(
        self,
        range_key: str,
        account_id: int | None = None,
        model_id: int | None = None,
        group_id: int | None = None,
    ) -> tuple[str, bytes]:
        from app.services.alert_service import consumed_percent

        start, end = self._resolve_range(range_key)
        account_ids = await self._resolve_account_ids(account_id, group_id)
        deployment_rows = await self._usage_repository.get_export_rows(start, end, account_ids, model_id)
        breakdown = {
            item["id"]: item
            for item in await self._usage_repository.get_breakdown_by_account(start, end, model_id, account_ids)
        }
        billed = await self._usage_repository.get_actual_cost_by_account(start, end, account_ids)
        _, billed_currency = await self._usage_repository._get_actual_cost(start, end, account_ids)
        accounts = [
            account
            for account in await self._account_repository.list_all()
            if account_ids is None or account.id in account_ids
        ]
        accounts.sort(key=lambda account: account.name.lower())

        # Input/output tokens per account, aggregated from the deployment rows
        # (the account breakdown only carries the combined total).
        io_by_account: dict[str, list[int]] = {}
        for row in deployment_rows:
            entry = io_by_account.setdefault(row["name"], [0, 0])
            entry[0] += row["input_tokens"]
            entry[1] += row["output_tokens"]

        group_label = "all"
        if group_id is not None:
            group = await self._account_group_repository.get(group_id)
            if group is not None:
                group_label = group.name

        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer)

        writer.writerow(
            [
                f"Usage export · scope: {group_label} · range: {range_key} ({start.isoformat()} to {end.isoformat()})"
                " · tokens/estimated/actual are in-range · newapi_*_usd_lifetime is gateway lifetime quota"
            ]
        )
        writer.writerow([])
        writer.writerow(["ACCOUNT SUMMARY"])
        writer.writerow(
            [
                "account",
                "endpoint",
                "gateway",
                "gateway_status",
                "o1_status",
                "o2_status",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "requests",
                "estimated_cost_usd",
                f"actual_cost_{billed_currency.lower()}",
                "actual_cost_currency",
                "newapi_o1_usd_lifetime",
                "newapi_o2_usd_lifetime",
                "newapi_total_usd_lifetime",
                "azure_credits_remaining",
                "azure_credits_limit",
                "credits_currency",
                "azure_credits_consumed_pct",
                "alert_announced_pct",
            ]
        )
        totals = {
            "input": 0,
            "output": 0,
            "tokens": 0,
            "requests": 0,
            "est": 0.0,
            "actual": 0.0,
            "o1": 0.0,
            "o2": 0.0,
            "newapi": 0.0,
            "remaining": 0.0,
            "limit": 0.0,
            "consumed": 0.0,
        }
        for account in accounts:
            usage = breakdown.get(account.id, {})
            input_tokens, output_tokens = io_by_account.get(account.name, [0, 0])
            o1 = account.new_api_cost_o1_usd or 0.0
            o2 = account.new_api_cost_o2_usd or 0.0
            new_api_total = account.new_api_cost_usd or 0.0
            actual, actual_currency = billed.get(account.id, (0.0, billed_currency))
            percent = consumed_percent(account)
            totals["input"] += input_tokens
            totals["output"] += output_tokens
            totals["tokens"] += usage.get("total_tokens", 0)
            totals["requests"] += usage.get("requests", 0)
            totals["est"] += usage.get("estimated_cost_usd", 0.0)
            totals["actual"] += actual
            totals["o1"] += o1
            totals["o2"] += o2
            totals["newapi"] += new_api_total
            if (account.credits_currency or billed_currency) == billed_currency:
                totals["remaining"] += account.credits_remaining or 0.0
                totals["limit"] += account.credits_limit or 0.0
            if percent is not None and account.credits_limit:
                totals["consumed"] += max((account.credits_limit or 0) - (account.credits_remaining or 0), 0.0)
            writer.writerow(
                [
                    account.name,
                    account.endpoint or "",
                    account.new_api_gateway or "",
                    _status_text(account.new_api_status),
                    _status_text(account.new_api_status_o1),
                    _status_text(account.new_api_status_o2),
                    input_tokens,
                    output_tokens,
                    usage.get("total_tokens", 0),
                    usage.get("requests", 0),
                    round(usage.get("estimated_cost_usd", 0.0), 2),
                    round(actual, 2),
                    actual_currency,
                    round(o1, 2),
                    round(o2, 2),
                    round(new_api_total, 2),
                    round(account.credits_remaining, 2) if account.credits_remaining is not None else "",
                    round(account.credits_limit, 2) if account.credits_limit is not None else "",
                    account.credits_currency or "",
                    round(percent, 1) if percent is not None else "",
                    account.new_api_alert_level or "",
                ]
            )
        combined_percent = (
            round(totals["consumed"] / totals["limit"] * 100, 1) if totals["limit"] > 0 else ""
        )
        writer.writerow(
            [
                "TOTAL",
                "",
                "",
                "",
                "",
                "",
                totals["input"],
                totals["output"],
                totals["tokens"],
                totals["requests"],
                round(totals["est"], 2),
                round(totals["actual"], 2),
                billed_currency,
                round(totals["o1"], 2),
                round(totals["o2"], 2),
                round(totals["newapi"], 2),
                round(totals["remaining"], 2),
                round(totals["limit"], 2),
                "",
                combined_percent,
                "",
            ]
        )

        writer.writerow([])
        writer.writerow(["PER-DEPLOYMENT USAGE"])
        writer.writerow(
            ["account", "endpoint", "deployment", "model", "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"]
        )
        model_totals = [0, 0, 0, 0.0]
        for row in deployment_rows:
            model_totals[0] += row["input_tokens"]
            model_totals[1] += row["output_tokens"]
            model_totals[2] += row["total_tokens"]
            model_totals[3] += row["total_cost"]
            writer.writerow([row[field] for field in _EXPORT_FIELDS])
        writer.writerow(
            ["TOTAL", "", "", "", model_totals[0], model_totals[1], model_totals[2], round(model_totals[3], 2)]
        )

        filename = _csv_filename(group_label, range_key)
        return filename, buffer.getvalue().encode("utf-8")

    async def _resolve_account_ids(
        self, account_id: int | None, group_id: int | None, gateway: str | None = None
    ) -> list[int] | None:
        """A group filter takes precedence over a single-account filter since
        picking a group implies "every account in it", which a lone account_id
        can't express. An optional gateway filter (O1/O2) narrows further to
        accounts matched on that NewAPI portal.
        """
        ids: list[int] | None = None
        if group_id is not None:
            ids = await self._account_group_repository.member_account_ids(group_id)
        elif account_id is not None:
            ids = [account_id]
        if gateway:
            accounts = await self._account_repository.list_all()
            gateway_ids = {a.id for a in accounts if _in_gateway_scope(a.new_api_gateway, gateway)}
            ids = [i for i in ids if i in gateway_ids] if ids is not None else sorted(gateway_ids)
            if not ids:
                # Sentinel that matches no rows so an empty scope returns zeros
                # instead of falling back to "all accounts".
                ids = [-1]
        return ids

    @staticmethod
    def _resolve_range(range_key: str) -> tuple[datetime, datetime]:
        """Hour-aligned, exclusive end so TPM/RPM only use complete PT1H buckets.
        The in-progress hour is dropped; Azure Monitor 1-minute TPM cannot be
        reconstructed from a partial hour.
        """
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - _RANGE_MAP.get(range_key, _RANGE_MAP["7d"])
        return start, end


def _mark_estimated_usd(items: list[dict]) -> None:
    for item in items:
        item["estimated_cost"] = float(item.get("estimated_cost_usd") or 0)
        item["currency"] = "USD"


def _status_text(status: int | None) -> str:
    return {None: "", 1: "enabled", 2: "disabled", 3: "auto-disabled"}.get(status, str(status))


def _in_gateway_scope(account_gateway: str | None, scope: str) -> bool:
    """Gateway scopes for dashboard filtering.

    Membership scopes (an account can satisfy both): "O1", "O2".
    Disjoint scopes (each account satisfies exactly one, so token/cost series
    built from them sum to the combined total): "O1x" (O1 only), "O2x"
    (O2 only), "BOTH" (matched on both portals).
    """
    labels = {part for part in (account_gateway or "").split("+") if part}
    if scope == "O1x":
        return labels == {"O1"}
    if scope == "O2x":
        return labels == {"O2"}
    if scope == "BOTH":
        return {"O1", "O2"} <= labels
    return scope in labels


def _csv_filename(group_label: str, range_key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", group_label).strip("-._") or "all"
    day = datetime.now(timezone.utc).date().isoformat()
    return f"usage_{safe}_{range_key}_{day}.csv"
