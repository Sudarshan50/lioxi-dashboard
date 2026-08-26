import clsx from "clsx";
import { BellRing, FileDown, Play, Plus, Scissors, Search, Send, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import { AlertStateItem } from "@/types";
import {
  useAlertConfig,
  useAlertState,
  useAlertStatus,
  useRunAlertCheck,
  useSaveAlertConfig,
  useSendTestAlert,
  useSetAtCapManual,
  useSetPayableSettled,
} from "@/hooks/useAlerts";
import { formatCurrency } from "@/lib/format";
import { amountPayableUsd, brokerageUsd, downloadPayableCsv, payablePercentLabel } from "@/lib/payable";
import { matchesOwner, ownerLabel, uniqueOwners, UNTAGGED_OWNER } from "@/lib/ownerTag";

type AlertSort =
  | "percent"
  | "spend-desc"
  | "spend-asc"
  | "payable-desc"
  | "payable-asc"
  | "headroom-asc"
  | "headroom-desc";

function compareNullable(left: number | null | undefined, right: number | null | undefined, direction: "asc" | "desc") {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  return direction === "asc" ? left - right : right - left;
}

function sortAlertState(items: AlertStateItem[], sort: AlertSort): AlertStateItem[] {
  return [...items].sort((left, right) => {
    if (sort === "spend-desc") return compareNullable(left.spend_usd, right.spend_usd, "desc");
    if (sort === "spend-asc") return compareNullable(left.spend_usd, right.spend_usd, "asc");
    if (sort === "payable-desc") {
      return compareNullable(amountPayableUsd(left.spend_usd), amountPayableUsd(right.spend_usd), "desc");
    }
    if (sort === "payable-asc") {
      return compareNullable(amountPayableUsd(left.spend_usd), amountPayableUsd(right.spend_usd), "asc");
    }
    if (sort === "headroom-asc") return compareNullable(left.headroom_usd, right.headroom_usd, "asc");
    if (sort === "headroom-desc") return compareNullable(left.headroom_usd, right.headroom_usd, "desc");
    const active = Number(right.gateway_enabled) - Number(left.gateway_enabled);
    if (active !== 0) return active;
    return compareNullable(left.percent, right.percent, "desc");
  });
}

function isDisabledAtCap(item: AlertStateItem): boolean {
  return !item.gateway_enabled && item.exhausted;
}

function StatusPill({
  tone,
  children,
  title,
}: {
  tone: "red" | "amber" | "green" | "violet";
  children: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium",
        tone === "red" && "bg-red-500/15 text-red-300",
        tone === "amber" && "bg-amber-500/15 text-amber-300",
        tone === "green" && "bg-emerald-500/15 text-emerald-300",
        tone === "violet" && "bg-violet-500/15 text-violet-300"
      )}
    >
      {children}
    </span>
  );
}

function MetricTile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone: string }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2.5">
      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-500">{label}</p>
      <p className={clsx("mt-1 text-lg font-semibold tabular-nums tracking-tight", tone)}>{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-gray-600">{hint}</p>}
    </div>
  );
}

function normalizeSearchText(value: string): string {
  return value.toLowerCase().replace(/[_\-\s./]+/g, " ").trim();
}

function matchesAlertSearch(item: AlertStateItem, rawQuery: string): boolean {
  const needle = normalizeSearchText(rawQuery);
  if (!needle) return true;
  const haystack = normalizeSearchText(
    [item.name, item.new_api_name, item.owner_tag, item.endpoint, item.gateway, item.exhausted ? "at cap" : "", item.alert_level >= 100 ? "auto-disabled" : ""].filter(Boolean).join(" ")
  );
  if (haystack.includes(needle)) return true;
  return needle.split(/\s+/).filter(Boolean).every((token) => haystack.includes(token));
}

export default function AlertsPage() {
  const status = useAlertStatus();
  const config = useAlertConfig();
  const state = useAlertState();
  const saveConfig = useSaveAlertConfig();
  const sendTest = useSendTestAlert();
  const runCheck = useRunAlertCheck();
  const setPayableSettled = useSetPayableSettled();
  const setAtCapManual = useSetAtCapManual();

  const [enabled, setEnabled] = useState(true);
  const [thresholds, setThresholds] = useState<number[]>([75, 95]);
  const [rearmMargin, setRearmMargin] = useState("5");
  const [overspendBuffer, setOverspendBuffer] = useState("250");
  const [syncInterval, setSyncInterval] = useState("5");
  const [azureSyncInterval, setAzureSyncInterval] = useState("30");
  const [alertSort, setAlertSort] = useState<AlertSort>("percent");
  const [search, setSearch] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [newThreshold, setNewThreshold] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tagPayableOpen, setTagPayableOpen] = useState(false);
  const [brokerageClicks, setBrokerageClicks] = useState(0);
  const [brokerageLeaving, setBrokerageLeaving] = useState(false);
  const showBrokerage = brokerageClicks >= 3 || brokerageLeaving;
  const payablePct = payablePercentLabel();

  useEffect(() => {
    if (!config.data) return;
    setEnabled(config.data.enabled);
    setThresholds(config.data.thresholds);
    setRearmMargin(String(config.data.rearm_margin));
    setOverspendBuffer(String(config.data.overspend_buffer_usd ?? 250));
    setSyncInterval(String(config.data.sync_interval_minutes ?? 5));
    setAzureSyncInterval(String(config.data.azure_sync_interval_minutes ?? 30));
  }, [config.data]);

  const dirty =
    config.data != null &&
    (enabled !== config.data.enabled ||
      thresholds.join(",") !== config.data.thresholds.join(",") ||
      Number(rearmMargin) !== config.data.rearm_margin ||
      Number(overspendBuffer) !== config.data.overspend_buffer_usd ||
      Number(syncInterval) !== config.data.sync_interval_minutes ||
      Number(azureSyncInterval) !== config.data.azure_sync_interval_minutes);

  function addThreshold() {
    const value = Math.round(Number(newThreshold));
    if (!Number.isFinite(value) || value < 1 || value > 99) {
      setError("Thresholds must be between 1 and 99 percent — auto-disable uses grant + buffer.");
      return;
    }
    setError(null);
    setThresholds((prev) => (prev.includes(value) ? prev : [...prev, value].sort((a, b) => a - b)));
    setNewThreshold("");
  }

  async function handleSave() {
    setMessage(null);
    setError(null);
    if (thresholds.length === 0) {
      setError("Keep at least one threshold.");
      return;
    }
    const margin = Number(rearmMargin);
    if (!Number.isFinite(margin) || margin < 0 || margin > 100) {
      setError("Re-arm margin must be between 0 and 100 points.");
      return;
    }
    const buffer = Number(overspendBuffer);
    if (!Number.isFinite(buffer) || buffer < 0 || buffer > 10000) {
      setError("Overspend buffer must be between $0 and $10,000.");
      return;
    }
    const interval = Math.round(Number(syncInterval));
    if (!Number.isFinite(interval) || interval < 1 || interval > 180) {
      setError("NewAPI sync interval must be between 1 and 180 minutes.");
      return;
    }
    const azureInterval = Math.round(Number(azureSyncInterval));
    if (!Number.isFinite(azureInterval) || azureInterval < 5 || azureInterval > 180) {
      setError("Azure sync interval must be between 5 and 180 minutes.");
      return;
    }
    try {
      await saveConfig.mutateAsync({
        enabled,
        thresholds,
        rearm_margin: margin,
        overspend_buffer_usd: buffer,
        sync_interval_minutes: interval,
        azure_sync_interval_minutes: azureInterval,
      });
      setMessage("Alert rules saved.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not save alert rules.");
    }
  }

  async function handleTest() {
    setMessage(null);
    setError(null);
    try {
      await sendTest.mutateAsync();
      setMessage("Test message sent to the Telegram group.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not send the test message.");
    }
  }

  const owner = ownerFilter === "all" ? null : ownerFilter;
  const ownerOptions = useMemo(() => uniqueOwners(state.data ?? []), [state.data]);
  const visibleAlertState = useMemo(
    () =>
      (state.data ?? []).filter(
        (item) => matchesOwner(item.owner_tag, owner) && matchesAlertSearch(item, search)
      ),
    [owner, search, state.data]
  );
  const sortedAlertState = useMemo(
    () => sortAlertState(visibleAlertState, alertSort),
    [alertSort, visibleAlertState]
  );
  const payableTotals = useMemo(() => {
    const spend = visibleAlertState.reduce((sum, item) => sum + (item.spend_usd || 0), 0);
    const payable = visibleAlertState.reduce((sum, item) => sum + amountPayableUsd(item.spend_usd), 0);
    const unsettled = visibleAlertState
      .filter((item) => isDisabledAtCap(item) && !item.payable_settled)
      .reduce((sum, item) => sum + amountPayableUsd(item.spend_usd), 0);
    const settled = visibleAlertState
      .filter((item) => item.payable_settled)
      .reduce((sum, item) => sum + amountPayableUsd(item.spend_usd), 0);
    const active = visibleAlertState.filter((item) => item.gateway_enabled).length;
    const forecast = visibleAlertState.reduce((sum, item) => sum + amountPayableUsd(item.credits_limit), 0);
    const brokerage = visibleAlertState.reduce(
      (sum, item) => sum + brokerageUsd(item.credits_limit, amountPayableUsd(item.spend_usd)),
      0
    );
    return { spend, payable, unsettled, settled, active, inactive: visibleAlertState.length - active, forecast, brokerage };
  }, [visibleAlertState]);
  const payableByTag = useMemo(() => {
    const grouped = new Map<
      string,
      { spend: number; payable: number; forecast: number; brokerage: number; count: number; active: number; inactive: number }
    >();
    for (const item of visibleAlertState) {
      const tag = ownerLabel(item.owner_tag);
      const current = grouped.get(tag) ?? {
        spend: 0,
        payable: 0,
        forecast: 0,
        brokerage: 0,
        count: 0,
        active: 0,
        inactive: 0,
      };
      current.spend += item.spend_usd || 0;
      current.payable += amountPayableUsd(item.spend_usd);
      current.forecast += amountPayableUsd(item.credits_limit);
      current.brokerage += brokerageUsd(item.credits_limit, amountPayableUsd(item.spend_usd));
      current.count += 1;
      if (item.gateway_enabled) current.active += 1;
      else current.inactive += 1;
      grouped.set(tag, current);
    }
    return [...grouped.entries()].sort((left, right) => right[1].payable - left[1].payable);
  }, [visibleAlertState]);

  async function handleRunCheck() {
    setMessage(null);
    setError(null);
    try {
      const result = await runCheck.mutateAsync();
      if (result.skipped) setMessage(`Check skipped: ${result.skipped}.`);
      else setMessage(result.sent > 0 ? `Check complete — ${result.sent} alert${result.sent === 1 ? "" : "s"} sent.` : "Check complete — no new thresholds crossed.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Alert check failed.");
    }
  }

  async function handleMarkAtCap(item: AlertStateItem, atCap: boolean) {
    setMessage(null);
    setError(null);
    try {
      await setAtCapManual.mutateAsync({ id: item.id, atCap });
      setMessage(atCap ? `${item.name} tagged at cap.` : `${item.name} at-cap tag removed.`);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not update at-cap tag.");
    }
  }

  async function handleMarkPaid(item: AlertStateItem, settled: boolean) {
    setMessage(null);
    setError(null);
    try {
      await setPayableSettled.mutateAsync({ id: item.id, settled });
      setMessage(settled ? `${item.name} marked as paid.` : `${item.name} marked unpaid.`);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not update paid status.");
    }
  }

  return (
    <div className="flex flex-col gap-5 sm:gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="gradient-title text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="mt-1 text-sm text-gray-500">
            Telegram alerts when NewAPI spend crosses 75% / 95% of the Azure credit grant · channels
            auto-disable when spend reaches grant + your overspend buffer
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {status.data && (
            <>
              <Badge tone={status.data.telegram_configured ? "success" : "error"}>
                {status.data.telegram_configured ? "bot connected" : "bot not configured"}
              </Badge>
              <Badge tone={status.data.alerts_enabled ? "success" : "warning"}>
                {status.data.alerts_enabled ? "alerts on" : "alerts paused"}
              </Badge>
              <Badge tone="neutral">{status.data.admin_count} admin{status.data.admin_count === 1 ? "" : "s"}</Badge>
            </>
          )}
        </div>
      </div>

      {message && <Banner tone="success">{message}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-6">
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-200">Alert rules</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              Thresholds are NewAPI lifetime spend ÷ Azure credit grant. Each fires once per account and re-arms
              after spend drops below it by the re-arm margin (e.g. a grant top-up). Channels auto-disable when NewAPI
              spend reaches grant + the overspend buffer — Azure remaining is not used because it lags. NewAPI
              sync pulls spend and channel status, then Telegram alerts fire in that same cycle as soon as a
              threshold or cap is crossed. Azure token/cost sync is separate and slower.
            </p>
          </div>

          <button
            type="button"
            onClick={() => setEnabled((v) => !v)}
            className="flex w-fit items-center gap-2.5 rounded-xl border border-surface-border bg-surface px-3 py-2 text-sm"
          >
            <span
              className={`relative h-5 w-9 rounded-full transition-colors ${enabled ? "bg-emerald-500/80" : "bg-gray-600/60"}`}
            >
              <span
                className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${enabled ? "translate-x-4" : ""}`}
              />
            </span>
            <span className={enabled ? "text-emerald-300" : "text-gray-400"}>
              {enabled ? "Alerts enabled" : "Alerts paused"}
            </span>
          </button>

          <div>
            <p className="mb-1.5 text-xs font-medium text-gray-400">Thresholds (% of grant, NewAPI spend)</p>
            <div className="flex flex-wrap items-center gap-1.5">
              {thresholds.map((threshold) => (
                <span
                  key={threshold}
                  className="inline-flex items-center gap-1 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-2 py-1 text-xs font-medium text-indigo-200"
                >
                  {threshold}%
                  <button
                    type="button"
                    onClick={() => setThresholds((prev) => prev.filter((t) => t !== threshold))}
                    className="text-indigo-300/70 hover:text-red-300"
                    aria-label={`Remove ${threshold}% threshold`}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
              <div className="flex items-center gap-1">
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={newThreshold}
                  onChange={(e) => setNewThreshold(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addThreshold();
                  }}
                  placeholder="e.g. 90"
                  className="w-24 py-1 text-xs"
                />
                <Button variant="secondary" className="px-2 py-1 text-xs" onClick={addThreshold}>
                  <Plus size={13} /> Add
                </Button>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div className="w-32">
              <Input
                label="Re-arm margin (pts)"
                type="number"
                min={0}
                max={100}
                value={rearmMargin}
                onChange={(e) => setRearmMargin(e.target.value)}
              />
            </div>
            <div className="w-40">
              <Input
                label="Overspend buffer (USD)"
                type="number"
                min={0}
                max={10000}
                step={50}
                value={overspendBuffer}
                onChange={(e) => setOverspendBuffer(e.target.value)}
              />
            </div>
            <div className="w-40">
              <Input
                label="NewAPI sync (minutes)"
                type="number"
                min={1}
                max={180}
                value={syncInterval}
                onChange={(e) => setSyncInterval(e.target.value)}
              />
            </div>
            <div className="w-40">
              <Input
                label="Azure sync (minutes)"
                type="number"
                min={5}
                max={180}
                value={azureSyncInterval}
                onChange={(e) => setAzureSyncInterval(e.target.value)}
              />
            </div>
            <Button onClick={handleSave} isLoading={saveConfig.isPending} disabled={!dirty || config.isLoading}>
              Save rules
            </Button>
          </div>
        </Card>

        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-200">Bot actions</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              The bot also answers /usage, /enable and /disable in the group for whitelisted admins.
            </p>
          </div>
          <div className="flex flex-col gap-2 text-xs text-gray-400">
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface px-3 py-2">
              <span>Telegram bot token</span>
              <Badge tone={status.data?.telegram_configured ? "success" : "error"}>
                {status.data?.telegram_configured ? "configured" : "missing"}
              </Badge>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface px-3 py-2">
              <span>Group chat</span>
              <Badge tone={status.data?.chat_id_set ? "success" : "error"}>
                {status.data?.chat_id_set ? "linked" : "not linked"}
              </Badge>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface px-3 py-2">
              <span>Whitelisted admins</span>
              <span className="text-gray-200">{status.data?.admin_count ?? "—"}</span>
            </div>
          </div>
          <div className="mt-auto flex flex-wrap gap-2">
            <Button variant="secondary" onClick={handleTest} isLoading={sendTest.isPending}>
              <Send size={15} /> Send test message
            </Button>
            <Button variant="secondary" onClick={handleRunCheck} isLoading={runCheck.isPending}>
              <Play size={15} /> Run check now
            </Button>
          </div>
        </Card>
      </div>

      <Card className="min-w-0 overflow-hidden !p-0 hover:border-white/[0.06] hover:shadow-card">
        <div className="flex flex-col gap-4 border-b border-white/[0.06] px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <BellRing size={15} className="text-indigo-300" />
                <h2 className="text-sm font-semibold text-gray-200">Account alert state</h2>
                {visibleAlertState.length > 0 && (
                  <span className="text-xs tabular-nums font-normal text-gray-500">
                    {visibleAlertState.length}
                  </span>
                )}
              </div>
              <p className="mt-1 max-w-2xl text-xs leading-relaxed text-gray-500">
                Payable is NewAPI spend × {payablePct}. Unsettled is disabled accounts at cap (auto or tagged) that are not yet
                marked paid.
              </p>
            </div>
            <div className="flex w-full min-w-0 flex-col gap-2 sm:flex-row sm:items-end lg:w-auto">
              <div className="min-w-0 flex-1 sm:w-64 sm:flex-none">
                <label htmlFor="alert-search" className="mb-1.5 block text-xs font-medium text-gray-400">
                  Search
                </label>
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                  <input
                    id="alert-search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Account, channel, or tag"
                    className="w-full rounded-lg border border-surface-border bg-black/30 py-2 pl-8 pr-9 text-sm text-gray-100 outline-none placeholder:text-gray-600 focus:border-accent"
                  />
                  {search && (
                    <button
                      type="button"
                      onClick={() => setSearch("")}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-gray-500 hover:text-gray-200"
                      aria-label="Clear search"
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
              </div>
              <div className="w-full sm:w-44">
                <Select id="alert-owner" label="Tag" value={ownerFilter} onChange={(e) => setOwnerFilter(e.target.value)}>
                  <option value="all">All tags</option>
                  {ownerOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                  {(state.data ?? []).some((item) => !(item.owner_tag ?? "").trim()) && (
                    <option value={UNTAGGED_OWNER}>Untagged</option>
                  )}
                </Select>
              </div>
              <div className="w-full sm:w-52">
                <Select
                  id="alert-sort"
                  label="Sort by"
                  value={alertSort}
                  onChange={(e) => setAlertSort(e.target.value as AlertSort)}
                >
                  <option value="percent">Of grant % (high)</option>
                  <option value="spend-desc">NewAPI spend (high)</option>
                  <option value="spend-asc">NewAPI spend (low)</option>
                  <option value="payable-desc">Payable (high)</option>
                  <option value="payable-asc">Payable (low)</option>
                  <option value="headroom-asc">Headroom (low)</option>
                  <option value="headroom-desc">Headroom (high)</option>
                </Select>
              </div>
              <Button
                variant="secondary"
                className="w-full shrink-0 sm:w-auto"
                disabled={payableByTag.length === 0}
                onClick={() => setTagPayableOpen(true)}
              >
                <Users size={15} />
                By member
              </Button>
              <Button
                variant="secondary"
                className="w-full shrink-0 sm:w-auto"
                disabled={visibleAlertState.length === 0}
                onClick={() =>
                  downloadPayableCsv(
                    visibleAlertState.map((item) => ({
                    name: item.name,
                    owner: item.owner_tag,
                    newApiName: item.new_api_name,
                      endpoint: item.endpoint,
                      spendUsd: item.spend_usd,
                      spendO1Usd: item.spend_o1_usd,
                      spendO2Usd: item.spend_o2_usd,
                      settled: Boolean(item.payable_settled),
                    })),
                    "amount-payable.csv",
                    { unsettled: payableTotals.unsettled, settled: payableTotals.settled }
                  )
                }
              >
                <FileDown size={15} />
                Export
              </Button>
            </div>
          </div>

          {(state.data ?? []).length > 0 && (
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
              <MetricTile label="NewAPI spend" value={formatCurrency(payableTotals.spend, "USD")} tone="text-violet-300" />
              <MetricTile label="Amount payable" value={formatCurrency(payableTotals.payable, "USD")} hint="visible rows" tone="text-amber-200" />
              <MetricTile
                label="Unsettled"
                value={formatCurrency(payableTotals.unsettled, "USD")}
                hint="disabled at cap, unpaid"
                tone="text-red-300"
              />
              <MetricTile
                label="Settled"
                value={formatCurrency(payableTotals.settled, "USD")}
                hint="marked paid"
                tone="text-emerald-300"
              />
            </div>
          )}
        </div>

        <div className="overflow-x-auto">
          {state.isLoading ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : state.isError ? (
            <p className="px-5 py-8 text-center text-sm text-gray-500">Could not load alert state.</p>
          ) : (state.data ?? []).length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-gray-500">No accounts with NewAPI data yet — run a sync.</p>
          ) : visibleAlertState.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-gray-500">
              {search.trim() ? "No accounts match that search." : "No accounts to show."}
            </p>
          ) : (
            <table className="w-full min-w-[920px] text-left text-xs">
              <thead>
                <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wider text-gray-500">
                  <th className="px-4 py-2.5 pr-3 font-medium sm:px-5">Account</th>
                  <th className="py-2.5 pr-3 text-right font-medium">
                    <button
                      type="button"
                      className={`hover:text-gray-200 ${alertSort.startsWith("spend") ? "text-indigo-300" : ""}`}
                      onClick={() => setAlertSort(alertSort === "spend-desc" ? "spend-asc" : "spend-desc")}
                    >
                      Spend
                    </button>
                  </th>
                  <th className="py-2.5 pr-3 text-right font-medium">
                    <button
                      type="button"
                      className={`hover:text-gray-200 ${alertSort.startsWith("payable") ? "text-indigo-300" : ""}`}
                      onClick={() => setAlertSort(alertSort === "payable-desc" ? "payable-asc" : "payable-desc")}
                    >
                      Payable
                    </button>
                  </th>
                  <th className="py-2.5 pr-3 text-right font-medium">Grant</th>
                  <th className="py-2.5 pr-3 text-right font-medium">
                    <button
                      type="button"
                      className={`hover:text-gray-200 ${alertSort.startsWith("headroom") ? "text-indigo-300" : ""}`}
                      onClick={() => setAlertSort(alertSort === "headroom-asc" ? "headroom-desc" : "headroom-asc")}
                    >
                      Headroom
                    </button>
                  </th>
                  <th className="py-2.5 pr-3 font-medium">Of grant</th>
                  <th className="px-4 py-2.5 font-medium sm:px-5">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedAlertState.map((item) => {
                  const channelLine = [item.new_api_name, item.gateway].filter(Boolean).join(" · ");
                  const pendingCap = setAtCapManual.isPending && setAtCapManual.variables?.id === item.id;
                  const pendingPaid = setPayableSettled.isPending && setPayableSettled.variables?.id === item.id;
                  return (
                    <tr key={item.id} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                      <td className="px-4 py-3 pr-3 sm:px-5">
                        <div className="min-w-[16rem] max-w-[22rem]">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-medium text-gray-100">{item.name}</span>
                            {item.owner_tag && <StatusPill tone="violet">{item.owner_tag}</StatusPill>}
                            {item.gateway && !item.gateway_enabled && <StatusPill tone="amber">disabled</StatusPill>}
                            {!item.gateway && <StatusPill tone="amber" title="No NewAPI channel matched">no NewAPI</StatusPill>}
                            {item.alert_level >= 100 && (
                              <StatusPill tone="red" title="Gateway auto-stopped when NewAPI spend reached grant + buffer">
                                auto-disabled
                              </StatusPill>
                            )}
                            {item.exhausted && (
                              <StatusPill
                                tone="red"
                                title={
                                  item.exhausted_reason === "manual"
                                    ? "Manually tagged at cap"
                                    : "Spend reached grant + buffer"
                                }
                              >
                                at cap
                              </StatusPill>
                            )}
                            {item.payable_settled && <StatusPill tone="green">settled</StatusPill>}
                          </div>
                          {channelLine && <p className="mt-0.5 truncate text-[11px] text-gray-500">{channelLine}</p>}
                        </div>
                      </td>
                      <td className="py-3 pr-3 text-right tabular-nums text-violet-300">
                        {formatCurrency(item.spend_usd, "USD")}
                      </td>
                      <td className="py-3 pr-3 text-right tabular-nums text-amber-200">
                        {formatCurrency(amountPayableUsd(item.spend_usd), "USD")}
                      </td>
                      <td className="py-3 pr-3 text-right tabular-nums text-gray-300">
                        {item.credits_limit != null ? formatCurrency(item.credits_limit, item.credits_currency || "USD") : "—"}
                      </td>
                      <td
                        className={`py-3 pr-3 text-right tabular-nums ${
                          item.headroom_usd != null && item.headroom_usd <= 0 ? "text-red-300" : "text-gray-300"
                        }`}
                      >
                        {item.headroom_usd != null ? formatCurrency(item.headroom_usd, "USD") : "—"}
                      </td>
                      <td className="py-3 pr-3">
                        {item.percent == null ? (
                          <span className="text-gray-600">n/a</span>
                        ) : (
                          <div className="flex min-w-[7.5rem] items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                              <div
                                className={clsx(
                                  "h-full rounded-full",
                                  item.exhausted || item.percent >= 95
                                    ? "bg-red-500"
                                    : item.percent >= 75
                                      ? "bg-amber-400"
                                      : "bg-emerald-400"
                                )}
                                style={{ width: `${Math.min(item.percent, 100)}%` }}
                              />
                            </div>
                            <span className="w-10 text-right tabular-nums text-gray-400">{item.percent.toFixed(0)}%</span>
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 sm:px-5">
                        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                          {item.gateway && !item.gateway_enabled && !item.exhausted && (
                            <button
                              type="button"
                              disabled={pendingCap}
                              onClick={() => handleMarkAtCap(item, true)}
                              className="text-[11px] font-medium text-indigo-300 hover:text-indigo-200 disabled:opacity-50"
                            >
                              Tag at cap
                            </button>
                          )}
                          {item.gateway && !item.gateway_enabled && item.at_cap_manual && item.exhausted_reason === "manual" && (
                            <button
                              type="button"
                              disabled={pendingCap}
                              onClick={() => handleMarkAtCap(item, false)}
                              className="text-[11px] font-medium text-gray-500 hover:text-gray-300 disabled:opacity-50"
                            >
                              Untag
                            </button>
                          )}
                          {item.payable_settled ? (
                            <button
                              type="button"
                              disabled={pendingPaid}
                              onClick={() => handleMarkPaid(item, false)}
                              className="text-[11px] font-medium text-gray-500 hover:text-gray-300 disabled:opacity-50"
                            >
                              Undo paid
                            </button>
                          ) : isDisabledAtCap(item) ? (
                            <button
                              type="button"
                              disabled={pendingPaid}
                              onClick={() => handleMarkPaid(item, true)}
                              className="text-[11px] font-medium text-emerald-300 hover:text-emerald-200 disabled:opacity-50"
                            >
                              Mark paid
                            </button>
                          ) : (
                            <span className="text-gray-700">—</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-white/[0.08] bg-black/20 text-xs font-medium">
                  <td className="px-4 py-3 pr-3 text-gray-200 sm:px-5">TOTAL</td>
                  <td className="py-3 pr-3 text-right tabular-nums text-violet-300">
                    {formatCurrency(payableTotals.spend, "USD")}
                  </td>
                  <td className="py-3 pr-3 text-right tabular-nums text-amber-200">
                    {formatCurrency(payableTotals.payable, "USD")}
                  </td>
                  <td colSpan={4} className="px-4 py-3 text-right text-[11px] font-normal text-gray-500 sm:px-5">
                    Unsettled {formatCurrency(payableTotals.unsettled, "USD")} · Settled{" "}
                    {formatCurrency(payableTotals.settled, "USD")}
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </Card>

      <Modal
        title={
          <>
            <button
              type="button"
              className="cursor-default select-none bg-transparent p-0 text-inherit font-inherit"
              onClick={() => {
                if (brokerageLeaving) return;
                setBrokerageClicks((count) => (count >= 3 ? count : count + 1));
              }}
            >
              Payable
            </button>{" "}
            by member
          </>
        }
        isOpen={tagPayableOpen}
        onClose={() => {
          setTagPayableOpen(false);
          setBrokerageLeaving(false);
          setBrokerageClicks((count) => (brokerageLeaving ? 0 : count >= 3 ? count : 0));
        }}
        widthClassName={showBrokerage ? "max-w-3xl" : "max-w-2xl"}
      >
        <p className="mb-4 text-xs text-gray-500">
          Combined NewAPI spend and amount payable (× {payablePct}) for the rows currently visible. Forecast is the credit grant
          × {payablePct} if every account spends its full grant.
        </p>
        {payableByTag.length === 0 ? (
          <p className="py-6 text-center text-sm text-gray-500">No accounts to total.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
            <table className={`w-full text-left text-xs ${showBrokerage ? "min-w-[38rem]" : "min-w-[32rem]"}`}>
              <thead>
                <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wider text-gray-500">
                  <th className="px-3 py-2 font-medium">Member</th>
                  <th className="px-3 py-2 text-right font-medium">Accounts</th>
                  <th className="px-3 py-2 text-right font-medium">Spend</th>
                  <th className="px-3 py-2 text-right font-medium">Payable</th>
                  <th className="px-3 py-2 text-right font-medium">
                    Forecast
                    <span className="mt-0.5 block text-[10px] font-normal normal-case tracking-normal text-gray-600">
                      grant × {payablePct}
                    </span>
                  </th>
                  {showBrokerage && (
                    <th
                      className={clsx("brokerage-col py-2 font-medium", brokerageLeaving && "brokerage-col-out")}
                      onAnimationEnd={(event) => {
                        if (!brokerageLeaving) return;
                        if (!String(event.animationName).includes("brokerage-out")) return;
                        setBrokerageClicks(0);
                        setBrokerageLeaving(false);
                      }}
                    >
                      <span className="inline-flex items-center justify-end gap-1">
                        Brokerage
                        <button
                          type="button"
                          className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] font-normal normal-case tracking-normal text-rose-400/70 transition hover:bg-rose-500/15 hover:text-rose-200"
                          onClick={() => {
                            if (!brokerageLeaving) setBrokerageLeaving(true);
                          }}
                          aria-label="Cut brokerage column"
                          title="Cut"
                        >
                          <Scissors size={10} />
                          cut
                        </button>
                      </span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {payableByTag.map(([tag, row]) => (
                  <tr key={tag} className="border-b border-white/[0.04] last:border-0">
                    <td className="px-3 py-2 font-medium text-gray-200">{tag}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-400">
                      {row.count}
                      <span className="ml-1 text-[10px] font-normal text-gray-500">
                        (<span className="text-emerald-400/80">{row.active}</span>
                        <span className="text-gray-600">/</span>
                        <span className="text-amber-400/80">{row.inactive}</span>)
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-violet-300">
                      {formatCurrency(row.spend, "USD")}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-amber-200">
                      {formatCurrency(row.payable, "USD")}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-sky-300">
                      {formatCurrency(row.forecast, "USD")}
                    </td>
                    {showBrokerage && (
                      <td
                        className={clsx(
                          "brokerage-col py-2 tabular-nums text-rose-300",
                          brokerageLeaving && "brokerage-col-out"
                        )}
                      >
                        {formatCurrency(row.brokerage, "USD")}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-white/[0.08] bg-black/20 text-xs font-medium">
                  <td className="px-3 py-2 text-gray-200">TOTAL</td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-400">
                    {visibleAlertState.length}
                    <span className="ml-1 text-[10px] font-normal text-gray-500">
                      (<span className="text-emerald-400/80">{payableTotals.active}</span>
                      <span className="text-gray-600">/</span>
                      <span className="text-amber-400/80">{payableTotals.inactive}</span>)
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-violet-300">
                    {formatCurrency(payableTotals.spend, "USD")}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-amber-200">
                    {formatCurrency(payableTotals.payable, "USD")}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-sky-300">
                    {formatCurrency(payableTotals.forecast, "USD")}
                  </td>
                  {showBrokerage && (
                    <td
                      className={clsx(
                        "brokerage-col py-2 tabular-nums text-rose-300",
                        brokerageLeaving && "brokerage-col-out"
                      )}
                    >
                      {formatCurrency(payableTotals.brokerage, "USD")}
                    </td>
                  )}
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Modal>
    </div>
  );
}
