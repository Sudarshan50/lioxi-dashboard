import { Clock, Coins, Download, FileDown, Gauge, HandCoins, MessageSquare, Receipt, RefreshCw, Router, Timer, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import BreakdownBarChart from "@/components/charts/BreakdownBarChart";
import CostComparisonChart from "@/components/charts/CostComparisonChart";
import TpmByAccountChart from "@/components/charts/TpmByAccountChart";
import UsageAreaChart from "@/components/charts/UsageAreaChart";
import ExportCsvModal from "@/components/dashboard/ExportCsvModal";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import CurrencyToggle from "@/components/ui/CurrencyToggle";
import GatewayToggle, { GatewayView } from "@/components/ui/GatewayToggle";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import StatCard from "@/components/ui/StatCard";
import { useAccountGroups } from "@/hooks/useAccountGroups";
import { useAccounts, useSyncAccounts } from "@/hooks/useAccounts";
import {
  useBreakdownByAccount,
  useBreakdownByModel,
  useDashboardOverview,
  useDashboardTimeseries,
  useTimeseriesByAccount,
  useUsdInrRate,
} from "@/hooks/useDashboard";
import { useEstimateCurrency } from "@/hooks/useEstimateCurrency";
import { useModels } from "@/hooks/useModels";
import { convertUsd, formatCurrency, formatDateTime, formatEstimatedCost, formatRate, formatRelative, formatTokens } from "@/lib/format";
import { amountPayableUsd, downloadPayableCsv } from "@/lib/payable";
import { matchesOwner, rollupBreakdownByTag, rollupTpmByTag, uniqueOwners, UNTAGGED_OWNER } from "@/lib/ownerTag";

const RANGE_OPTIONS = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
];

const ALL = "all";

export default function OverviewPage() {
  const [range, setRange] = useState("7d");
  const [accountFilter, setAccountFilter] = useState<string>(ALL);
  const [modelFilter, setModelFilter] = useState<string>(ALL);
  const [groupFilter, setGroupFilter] = useState<string>(ALL);
  const [ownerFilter, setOwnerFilter] = useState<string>(ALL);

  const { data: accounts, isLoading: accountsLoading, isError: accountsError } = useAccounts();
  const { data: models, isLoading: modelsLoading, isError: modelsError } = useModels();
  const { data: groups, isLoading: groupsLoading, isError: groupsError } = useAccountGroups();
  const { syncAccounts, progress, isSyncing } = useSyncAccounts();
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [isExportOpen, setIsExportOpen] = useState(false);
  const { currency: estimateCurrency, setCurrency: setEstimateCurrency } = useEstimateCurrency();
  const [gatewayView, setGatewayView] = useState<GatewayView>("ALL");
  const fx = useUsdInrRate();
  const usdInr = fx.data?.usd_inr ?? 87;
  const fxLabel = fx.data?.is_fallback ? "fallback ₹87" : fx.data?.source === "config" ? "configured rate" : "live rate";

  const accountId = accountFilter === ALL ? null : Number(accountFilter);
  const modelId = modelFilter === ALL ? null : Number(modelFilter);
  const groupId = groupFilter === ALL ? null : Number(groupFilter);
  const owner = ownerFilter === ALL ? null : ownerFilter;
  const selectedGroup = (groups ?? []).find((g) => g.id === groupId);
  const ownerOptions = useMemo(() => uniqueOwners(accounts ?? []), [accounts]);

  const modelsForFilter = useMemo(() => {
    const memberIds = groupId ? new Set(selectedGroup?.accounts.map((account) => account.id) ?? []) : null;
    const scoped = (models ?? []).filter((model) => {
      if (!model.model_name || model.registered_model_id == null) return false;
      if (accountId && model.provider_account_id !== accountId) return false;
      if (memberIds && !memberIds.has(model.provider_account_id)) return false;
      if (owner) {
        const account = (accounts ?? []).find((item) => item.id === model.provider_account_id);
        if (!matchesOwner(account?.owner_tag, owner)) return false;
      }
      return true;
    });
    const unique = new Map<number, (typeof scoped)[number]>();
    for (const model of scoped) {
      if (!unique.has(model.registered_model_id)) unique.set(model.registered_model_id, model);
    }
    return [...unique.values()].sort((a, b) => a.model_name.localeCompare(b.model_name, undefined, { sensitivity: "base" }));
  }, [accounts, models, accountId, groupId, owner, selectedGroup]);

  function handleAccountChange(value: string) {
    setAccountFilter(value);
    setModelFilter(ALL);
    if (value !== ALL) setGroupFilter(ALL);
  }

  function handleGroupChange(value: string) {
    setGroupFilter(value);
    setModelFilter(ALL);
    if (value !== ALL) setAccountFilter(ALL);
  }

  const overview = useDashboardOverview({ range, accountId, modelId, groupId, owner });
  const timeseries = useDashboardTimeseries({ range, accountId, modelId, groupId, owner });
  const tpmByAccount = useTimeseriesByAccount({ range, accountId, modelId, groupId, owner });
  const byAccount = useBreakdownByAccount(range, modelId, accountId, groupId, undefined, owner);
  const byModel = useBreakdownByModel(range, accountId, groupId, modelId, undefined, owner);
  const byAccountRows = useMemo(() => {
    const rows = byAccount.data ?? [];
    if (accountId || !accounts) return rows;
    return rollupBreakdownByTag(rows, accounts);
  }, [accountId, accounts, byAccount.data]);
  const tpmRows = useMemo(() => {
    const rows = tpmByAccount.data ?? [];
    if (accountId || !accounts) return rows;
    return rollupTpmByTag(rows, accounts);
  }, [accountId, accounts, tpmByAccount.data]);
  const breakdownHint = accountId ? "per account" : "combined by tag (name from endpoint map)";
  const dashboardError = overview.isError || timeseries.isError || tpmByAccount.isError || byAccount.isError || byModel.isError;
  const actualCurrency = overview.data?.actual_cost_currency || "INR";
  const tpmByAccountBars = useMemo(
    () =>
      [...byAccountRows]
        .filter((item) => (item.avg_tpm ?? 0) > 0)
        .sort((a, b) => (b.avg_tpm ?? 0) - (a.avg_tpm ?? 0)),
    [byAccountRows]
  );
  const estimatedSpendBars = useMemo(
    () =>
      [...byAccountRows]
        .filter((item) => (item.estimated_cost ?? item.estimated_cost_usd ?? 0) > 0)
        .map((item) => ({
          ...item,
          estimated_cost: convertUsd(item.estimated_cost_usd ?? item.estimated_cost ?? 0, estimateCurrency, usdInr),
        }))
        .sort((a, b) => (b.estimated_cost ?? 0) - (a.estimated_cost ?? 0)),
    [byAccountRows, estimateCurrency, usdInr]
  );
  const actualSpendBars = useMemo(
    () =>
      [...byAccountRows]
        .filter((item) => (item.actual_cost ?? 0) > 0)
        .sort((a, b) => (b.actual_cost ?? 0) - (a.actual_cost ?? 0)),
    [byAccountRows]
  );
  const pickNewApi = (item?: { new_api_cost?: number; new_api_cost_o1?: number; new_api_cost_o2?: number }) => {
    if (!item) return 0;
    if (gatewayView === "O1") return item.new_api_cost_o1 ?? 0;
    if (gatewayView === "O2") return item.new_api_cost_o2 ?? 0;
    return item.new_api_cost ?? 0;
  };
  const gatewayHint = gatewayView === "ALL" ? "O1 + O2 combined" : `${gatewayView} portal only`;
  const newApiSpendBars = useMemo(
    () =>
      [...byAccountRows]
        .filter((item) => pickNewApi(item) > 0)
        .map((item) => ({ ...item, new_api_cost: convertUsd(pickNewApi(item), estimateCurrency, usdInr) }))
        .sort((a, b) => (b.new_api_cost ?? 0) - (a.new_api_cost ?? 0)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [byAccountRows, estimateCurrency, usdInr, gatewayView]
  );
  const costComparisonData = useMemo(
    () => byAccountRows.map((item) => ({ ...item, new_api_cost: pickNewApi(item) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [byAccountRows, gatewayView]
  );
  const estimatedModelBars = useMemo(
    () =>
      [...(byModel.data ?? [])]
        .filter((item) => (item.estimated_cost ?? item.estimated_cost_usd ?? 0) > 0)
        .map((item) => ({
          ...item,
          estimated_cost: convertUsd(item.estimated_cost_usd ?? item.estimated_cost ?? 0, estimateCurrency, usdInr),
        }))
        .sort((a, b) => (b.estimated_cost ?? 0) - (a.estimated_cost ?? 0)),
    [byModel.data, estimateCurrency, usdInr]
  );
  const estimateHint =
    estimateCurrency === "INR"
      ? `From token prices · converted at ₹${usdInr.toFixed(2)} / $1 (${fxLabel})`
      : "From registered token prices · USD";
  const estimateChartLabel = estimateCurrency === "INR" ? "₹" : "USD";

  const activeFilterCount = (accountId ? 1 : 0) + (modelId ? 1 : 0) + (groupId ? 1 : 0) + (owner ? 1 : 0);
  const selectedAccount = (accounts ?? []).find((account) => account.id === accountId);
  const selectedModel = (models ?? []).find((model) => model.registered_model_id === modelId);
  const ownerLabel = owner === UNTAGGED_OWNER ? "Untagged" : owner;
  const scopeHint = [ownerLabel, selectedGroup?.name, selectedAccount?.name, selectedModel?.model_name]
    .filter(Boolean)
    .join(" · ");

  const accountsForFilter = useMemo(() => {
    let rows = accounts ?? [];
    if (groupId) {
      const memberIds = new Set(selectedGroup?.accounts.map((account) => account.id) ?? []);
      rows = rows.filter((account) => memberIds.has(account.id));
    }
    if (owner) rows = rows.filter((account) => matchesOwner(account.owner_tag, owner));
    return rows;
  }, [accounts, groupId, owner, selectedGroup]);

  const scopedAccounts = useMemo(() => {
    let rows = accounts ?? [];
    if (accountId) rows = rows.filter((account) => account.id === accountId);
    else if (groupId) {
      const memberIds = new Set(selectedGroup?.accounts.map((account) => account.id) ?? []);
      rows = rows.filter((account) => memberIds.has(account.id));
    }
    if (owner) rows = rows.filter((account) => matchesOwner(account.owner_tag, owner));
    return rows;
  }, [accounts, accountId, groupId, owner, selectedGroup]);

  const lastSyncedAt = useMemo(() => {
    let latest: string | null = null;
    for (const account of scopedAccounts) {
      if (!account.last_synced_at) continue;
      if (!latest || new Date(account.last_synced_at) > new Date(latest)) latest = account.last_synced_at;
    }
    return latest;
  }, [scopedAccounts]);

  const payableRows = useMemo(
    () =>
      scopedAccounts.map((account) => ({
        name: account.name,
        owner: account.owner_tag,
        newApiName: account.new_api_name,
        endpoint: account.endpoint,
        spendO1Usd: account.new_api_cost_o1_usd,
        spendO2Usd: account.new_api_cost_o2_usd,
        spendUsd: account.new_api_cost_usd,
        settled: Boolean(account.payable_settled),
      })),
    [scopedAccounts]
  );
  const payableGrandTotal = useMemo(
    () =>
      payableRows.reduce((sum, row) => {
        const spend =
          gatewayView === "O1" ? row.spendO1Usd : gatewayView === "O2" ? row.spendO2Usd : row.spendUsd;
        return sum + amountPayableUsd(spend);
      }, 0),
    [payableRows, gatewayView]
  );

  async function handleSync() {
    setSyncMessage(null);
    setSyncError(null);
    const ids = scopedAccounts.map((account) => account.id);
    if (ids.length === 0) {
      setSyncError("No accounts to sync.");
      return;
    }
    try {
      const result = await syncAccounts(ids);
      const failedNames = (result.failed ?? []).map((item) => item.name).filter((name): name is string => Boolean(name));
      if (result.synced > 0) {
        setSyncMessage(
          ids.length === 1 ? "Synced." : `Synced ${result.synced} account${result.synced === 1 ? "" : "s"}.`
        );
      }
      if (failedNames.length > 0) {
        setSyncError(`Sync failed for ${failedNames.join(", ")}.`);
      } else if (result.failed.length > 0) {
        setSyncError("Sync failed for one or more accounts.");
      }
    } catch (err: any) {
      setSyncError(err?.response?.data?.detail ?? "Sync failed.");
    }
  }

  return (
    <div className="flex flex-col gap-5 sm:gap-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="gradient-title text-2xl font-semibold tracking-tight">Overview</h1>
          <p className="mt-1 text-sm text-gray-500">Token usage and estimated spend across all monitored models</p>
        </div>
        <div className="flex w-full min-w-0 flex-col gap-2 sm:flex-row sm:items-center lg:w-auto lg:justify-end">
          <div className="flex w-full min-w-0 items-center gap-2 rounded-full border border-surface-border bg-surface px-3 py-1.5 text-xs text-gray-400 sm:w-auto">
            <Clock size={13} className="shrink-0 text-gray-500" />
            {accountsLoading ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <span className="min-w-0 truncate">
                {lastSyncedAt ? (
                  <>
                    <span className="text-gray-200">{formatRelative(lastSyncedAt)}</span>
                    <span className="hidden text-gray-600 sm:inline"> · {formatDateTime(lastSyncedAt)}</span>
                  </>
                ) : (
                  "Never synced"
                )}
              </span>
            )}
          </div>
          {accounts && accounts.length > 0 && (
            <>
              <Button variant="secondary" onClick={() => setIsExportOpen(true)} className="w-full shrink-0 sm:w-auto">
                <FileDown size={16} />
                Export CSV
              </Button>
              <Button
                variant="secondary"
                onClick={() => downloadPayableCsv(payableRows, "amount-payable.csv")}
                disabled={scopedAccounts.length === 0}
                className="w-full shrink-0 sm:w-auto"
              >
                <HandCoins size={16} />
                Export payable
              </Button>
              <Button
                variant="secondary"
                onClick={handleSync}
                isLoading={isSyncing}
                disabled={scopedAccounts.length === 0}
                className="w-full shrink-0 tabular-nums sm:w-auto"
              >
                {!isSyncing && <RefreshCw size={16} />}
                {isSyncing && progress
                  ? `${scopedAccounts.length === 1 ? "Sync" : "Sync all"} ${progress.current}/${progress.total}`
                  : accountId
                    ? "Sync"
                    : "Sync all"}
              </Button>
            </>
          )}
        </div>
      </div>

      {syncMessage && <Banner tone="success">{syncMessage}</Banner>}
      {syncError && <Banner tone="error">{syncError}</Banner>}
      {(accountsError || modelsError || groupsError) && (
        <Banner tone="error">Could not load accounts, models, or groups. Try refreshing the page.</Banner>
      )}
      {dashboardError && <Banner tone="error">Could not load dashboard data. Try refreshing the page.</Banner>}

      <Card>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Select label="Time range" value={range} onChange={(e) => setRange(e.target.value)}>
            {RANGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
          <Select label="Tag" value={ownerFilter} onChange={(e) => { setOwnerFilter(e.target.value); setAccountFilter(ALL); setModelFilter(ALL); }}>
            <option value={ALL}>All tags</option>
            {ownerOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
            {(accounts ?? []).some((account) => !(account.owner_tag ?? "").trim()) && (
              <option value={UNTAGGED_OWNER}>Untagged</option>
            )}
          </Select>
          <Select label="Group" value={groupFilter} onChange={(e) => handleGroupChange(e.target.value)} disabled={groupsLoading}>
            <option value={ALL}>{groupsLoading ? "Loading groups..." : "All groups"}</option>
            {namedOptions(groups).map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </Select>
          <Select label="Account" value={accountFilter} onChange={(e) => handleAccountChange(e.target.value)} disabled={accountsLoading}>
            <option value={ALL}>{accountsLoading ? "Loading accounts..." : "All accounts"}</option>
            {namedOptions(owner || groupId ? accountsForFilter : accounts).map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </Select>
          <Select label="Model" value={modelFilter} onChange={(e) => setModelFilter(e.target.value)} disabled={modelsLoading}>
            <option value={ALL}>{modelsLoading ? "Loading models..." : "All models"}</option>
            {modelsForFilter.map((model) => (
              <option key={model.registered_model_id} value={model.registered_model_id}>
                {model.model_name}
              </option>
            ))}
          </Select>
        </div>
        {activeFilterCount > 0 && (
          <button
            onClick={() => {
              setAccountFilter(ALL);
              setModelFilter(ALL);
              setGroupFilter(ALL);
              setOwnerFilter(ALL);
            }}
            className="mt-3 text-xs font-medium text-accent hover:text-accent-hover"
          >
            Clear filters
          </button>
        )}
      </Card>

      {overview.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : overview.isError ? (
        <p className="py-6 text-center text-sm text-gray-500">Overview metrics could not be loaded.</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <StatCard
            label="Estimated cost"
            value={formatEstimatedCost(overview.data?.estimated_cost_usd ?? 0, estimateCurrency, usdInr)}
            icon={<Coins size={20} />}
            tone="indigo"
            hint={estimateHint}
            action={<CurrencyToggle value={estimateCurrency} onChange={setEstimateCurrency} />}
          />
          <StatCard
            label="Actual cost"
            value={
              overview.data?.actual_cost == null
                ? "—"
                : formatCurrency(overview.data.actual_cost, overview.data.actual_cost_currency || "USD")
            }
            icon={<Receipt size={20} />}
            tone="emerald"
            hint={
              modelId
                ? "Hidden while a model is selected — Azure bills at account level"
                : overview.data?.actual_cost_currency === "INR"
                  ? "Azure billed amount · rupees"
                  : `Azure billed amount · ${overview.data?.actual_cost_currency || "USD"}`
            }
          />
          <StatCard
            label="NewAPI spend"
            value={formatEstimatedCost(pickNewApi(overview.data), estimateCurrency, usdInr)}
            icon={<Router size={20} />}
            tone="violet"
            hint={`Gateway lifetime quota · ${gatewayHint}`}
            action={<GatewayToggle value={gatewayView} onChange={setGatewayView} />}
          />
          <StatCard
            label="Amount payable"
            value={formatEstimatedCost(payableGrandTotal, estimateCurrency, usdInr)}
            icon={<HandCoins size={20} />}
            tone="amber"
            hint={`NewAPI spend × 12% · ${gatewayHint}`}
          />
          <StatCard
            label="Input tokens"
            value={formatTokens(overview.data?.total_prompt_tokens ?? 0)}
            icon={<Download size={20} />}
            tone="sky"
            hint="Prompt tokens sent"
          />
          <StatCard
            label="Output tokens"
            value={formatTokens(overview.data?.total_completion_tokens ?? 0)}
            icon={<Upload size={20} />}
            tone="amber"
            hint="Completion tokens generated"
          />
          <StatCard
            label="Requests"
            value={formatTokens(overview.data?.total_requests ?? 0)}
            icon={<MessageSquare size={20} />}
            tone="emerald"
          />
          <StatCard
            label="Avg TPM"
            value={formatRate(overview.data?.avg_tpm ?? 0)}
            icon={<Gauge size={20} />}
            tone="violet"
            hint={`Peak ${formatRate(overview.data?.peak_tpm ?? 0)} · tokens/min over selected range`}
          />
          <StatCard
            label="Avg RPM"
            value={formatRate(overview.data?.avg_rpm ?? 0)}
            icon={<Timer size={20} />}
            tone="rose"
            hint={`Peak ${formatRate(overview.data?.peak_rpm ?? 0)} · requests/min over selected range`}
          />
        </div>
      )}

      <Card className="min-w-0 overflow-hidden">
        <h2 className="text-sm font-semibold text-gray-200">Token usage over time</h2>
        {scopeHint && <p className="mt-0.5 text-xs text-gray-500">{scopeHint}</p>}
        <div className="mt-4">
          {timeseries.isLoading ? (
            <div className="flex h-52 items-center justify-center sm:h-64 lg:h-72">
              <Spinner />
            </div>
          ) : timeseries.isError ? (
            <p className="py-16 text-center text-sm text-gray-500">Could not load usage over time.</p>
          ) : (timeseries.data ?? []).length > 0 ? (
            <UsageAreaChart data={timeseries.data ?? []} />
          ) : (
            <p className="py-16 text-center text-sm text-gray-500">No usage in this range</p>
          )}
        </div>
      </Card>

      <Card className="min-w-0 overflow-hidden">
        <h2 className="text-sm font-semibold text-gray-200">TPM by {accountId ? "account" : "tag"}</h2>
        <p className="mt-0.5 text-xs text-gray-500">
          Hourly tokens ÷ 60 {breakdownHint}{scopeHint ? ` · ${scopeHint}` : ""} · click a time to see every series
        </p>
        <div className="mt-4">
          {tpmByAccount.isLoading ? (
            <div className="flex h-52 items-center justify-center sm:h-64 lg:h-72">
              <Spinner />
            </div>
          ) : tpmByAccount.isError ? (
            <p className="py-16 text-center text-sm text-gray-500">Could not load TPM.</p>
          ) : (tpmByAccount.data ?? []).length > 0 ? (
            <TpmByAccountChart data={tpmRows} />
          ) : (
            <p className="py-16 text-center text-sm text-gray-500">No account TPM in this range</p>
          )}
        </div>
      </Card>

      <Card className="min-w-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-gray-200">
              Cost per {accountId ? "account" : "tag"} — Estimated vs Actual vs NewAPI
            </h2>
            <p className="mt-0.5 text-xs text-gray-500">
              All in {estimateCurrency}
              {estimateCurrency === "INR" ? ` at ₹${usdInr.toFixed(2)} / $1` : ` (₹ converted at ${usdInr.toFixed(2)})`} ·
              estimated and actual follow the selected range, NewAPI is gateway lifetime ({gatewayHint}), gold bar is the
              Azure credit grant (auto-stop cutoff)
              {scopeHint ? ` · ${scopeHint}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <GatewayToggle value={gatewayView} onChange={setGatewayView} />
            <CurrencyToggle value={estimateCurrency} onChange={setEstimateCurrency} />
          </div>
        </div>
        <div className="mt-4">
          {byAccount.isLoading ? (
            <div className="flex h-56 items-center justify-center sm:h-64">
              <Spinner />
            </div>
          ) : byAccount.isError ? (
            <p className="py-16 text-center text-sm text-gray-500">Could not load cost comparison.</p>
          ) : (
            <CostComparisonChart data={costComparisonData} currency={estimateCurrency} usdInr={usdInr} />
          )}
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-6">
        <Card className="min-w-0 overflow-hidden">
          <h2 className="text-sm font-semibold text-gray-200">Avg TPM by {accountId ? "account" : "tag"}</h2>
          {scopeHint && <p className="mt-0.5 text-xs text-gray-500">{scopeHint}</p>}
          <div className="mt-4">
            {byAccount.isLoading ? (
              <div className="flex h-56 items-center justify-center sm:h-64">
                <Spinner />
              </div>
            ) : byAccount.isError ? (
              <p className="py-16 text-center text-sm text-gray-500">Could not load account TPM.</p>
            ) : tpmByAccountBars.length > 0 ? (
              <BreakdownBarChart data={tpmByAccountBars} metric="avg_tpm" />
            ) : (
              <p className="py-16 text-center text-sm text-gray-500">No account TPM in this range</p>
            )}
          </div>
        </Card>
        <Card className="min-w-0 overflow-hidden">
          <h2 className="text-sm font-semibold text-gray-200">
            Estimated spend by {accountId ? "account" : "tag"} ({estimateChartLabel})
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            {estimateHint}
            {scopeHint ? ` · ${scopeHint}` : ""}
          </p>
          <div className="mt-4">
            {byAccount.isLoading ? (
              <div className="flex h-56 items-center justify-center sm:h-64">
                <Spinner />
              </div>
            ) : byAccount.isError ? (
              <p className="py-16 text-center text-sm text-gray-500">Could not load account spend.</p>
            ) : estimatedSpendBars.length > 0 ? (
              <BreakdownBarChart data={estimatedSpendBars} metric="estimated_cost" currency={estimateCurrency} />
            ) : (
              <p className="py-16 text-center text-sm text-gray-500">No account spend in this range</p>
            )}
          </div>
        </Card>
        <Card className="min-w-0 overflow-hidden">
          <h2 className="text-sm font-semibold text-gray-200">
            Actual spend by {accountId ? "account" : "tag"}{actualCurrency === "INR" ? " (₹)" : ` (${actualCurrency})`}
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Azure billed amount{actualCurrency === "INR" ? ", kept in rupees" : ""}
            {scopeHint ? ` · ${scopeHint}` : ""}
          </p>
          <div className="mt-4">
            {byAccount.isLoading ? (
              <div className="flex h-56 items-center justify-center sm:h-64">
                <Spinner />
              </div>
            ) : byAccount.isError ? (
              <p className="py-16 text-center text-sm text-gray-500">Could not load billed spend.</p>
            ) : actualSpendBars.length > 0 ? (
              <BreakdownBarChart data={actualSpendBars} metric="actual_cost" currency={actualCurrency} />
            ) : (
              <p className="py-16 text-center text-sm text-gray-500">No billed spend in this range</p>
            )}
          </div>
        </Card>
        <Card className="min-w-0 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-gray-200">NewAPI spend by {accountId ? "account" : "tag"} ({estimateChartLabel})</h2>
            <GatewayToggle value={gatewayView} onChange={setGatewayView} />
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            Gateway lifetime quota converted to money · {gatewayHint}
            {scopeHint ? ` · ${scopeHint}` : ""}
          </p>
          <div className="mt-4">
            {byAccount.isLoading ? (
              <div className="flex h-56 items-center justify-center sm:h-64">
                <Spinner />
              </div>
            ) : byAccount.isError ? (
              <p className="py-16 text-center text-sm text-gray-500">Could not load NewAPI spend.</p>
            ) : newApiSpendBars.length > 0 ? (
              <BreakdownBarChart data={newApiSpendBars} metric="new_api_cost" currency={estimateCurrency} />
            ) : (
              <p className="py-16 text-center text-sm text-gray-500">No NewAPI spend recorded yet — run a sync</p>
            )}
          </div>
        </Card>
        <Card className="min-w-0 overflow-hidden">
          <h2 className="text-sm font-semibold text-gray-200">
            Estimated spend by model ({estimateChartLabel})
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            {estimateHint}
            {scopeHint ? ` · ${scopeHint}` : ""}
          </p>
          <div className="mt-4">
            {byModel.isLoading ? (
              <div className="flex h-56 items-center justify-center sm:h-64">
                <Spinner />
              </div>
            ) : byModel.isError ? (
              <p className="py-16 text-center text-sm text-gray-500">Could not load model spend.</p>
            ) : estimatedModelBars.length > 0 ? (
              <BreakdownBarChart data={estimatedModelBars} metric="estimated_cost" currency={estimateCurrency} />
            ) : (
              <p className="py-16 text-center text-sm text-gray-500">No model spend in this range</p>
            )}
          </div>
        </Card>
      </div>
      <ExportCsvModal
        isOpen={isExportOpen}
        onClose={() => setIsExportOpen(false)}
        groups={namedOptions(groups)}
        owners={ownerOptions}
        hasUntagged={(accounts ?? []).some((account) => !(account.owner_tag ?? "").trim())}
        defaultRange={range}
        defaultGroupId={groupFilter}
        defaultOwner={ownerFilter}
      />
    </div>
  );
}

function namedOptions<T extends { name?: string | null }>(items: T[] | undefined): T[] {
  return (items ?? [])
    .filter((item) => Boolean(item.name?.trim()))
    .sort((left, right) => (left.name ?? "").localeCompare(right.name ?? "", undefined, { sensitivity: "base" }));
}
