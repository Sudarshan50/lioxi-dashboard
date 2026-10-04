import { Check, Copy, Eye, EyeOff, ScrollText, Search, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import { useRevealAccountApiKey } from "@/hooks/useAccounts";
import {
  useGptAccounts,
  useGptAvailability,
  useGptJob,
  useGptLogs,
  useStartGptDeploy,
} from "@/hooks/useGptDeploy";
import { copyText } from "@/lib/copyText";
import { toastError, toastSuccess } from "@/lib/toast";
import { GptModelPlan } from "@/types";

function money(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function rate(value: number | null | undefined) {
  if (value == null) return "—";
  return value.toLocaleString();
}

function CopyValueRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    if (await copyText(value)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    }
  }

  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="shrink-0 text-gray-500">{label}</span>
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="truncate font-mono text-gray-300" title={value}>
          {value}
        </span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="text-gray-600 hover:text-gray-300"
          aria-label={`Copy ${label.toLowerCase()}`}
          title={`Copy ${label.toLowerCase()}`}
        >
          {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
        </button>
      </div>
    </div>
  );
}

function GptApiKeyRow({
  accountId,
  apiKey,
  onApiKey,
}: {
  accountId: number;
  apiKey: string | null;
  onApiKey: (key: string) => void;
}) {
  const reveal = useRevealAccountApiKey();
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setVisible(Boolean(apiKey));
    setError(null);
  }, [accountId, apiKey]);

  async function ensureKey(): Promise<string | null> {
    if (apiKey) return apiKey;
    try {
      const data = await reveal.mutateAsync(accountId);
      onApiKey(data.api_key);
      setError(null);
      return data.api_key;
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Could not load this API key.");
      return null;
    }
  }

  async function handleReveal() {
    if (visible) {
      setVisible(false);
      return;
    }
    const value = await ensureKey();
    if (value) setVisible(true);
  }

  async function handleCopy() {
    const value = await ensureKey();
    if (!value) return;
    if (await copyText(value)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    }
  }

  const display = visible && apiKey ? apiKey : "••••••••••••••••";
  const busy = reveal.isPending;

  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="shrink-0 text-gray-500">API key</span>
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-mono text-gray-300" title={visible && apiKey ? apiKey : undefined}>
            {display}
          </span>
          <button
            type="button"
            onClick={() => void handleReveal()}
            disabled={busy}
            className="text-gray-600 hover:text-gray-300 disabled:opacity-50"
            aria-label={visible ? "Hide API key" : "Reveal API key"}
            title={visible ? "Hide API key" : "Reveal API key"}
          >
            {busy ? <Spinner className="h-3 w-3" /> : visible ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
          <button
            type="button"
            onClick={() => void handleCopy()}
            disabled={busy}
            className="text-gray-600 hover:text-gray-300 disabled:opacity-50"
            aria-label="Copy API key"
            title="Copy API key"
          >
            {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
          </button>
        </div>
      </div>
      {error && <p className="break-words text-[11px] text-red-400">{error}</p>}
    </div>
  );
}

function modelLine(model: GptModelPlan) {
  if (!model.available) return model.reason || "Not available in this region.";
  const deployed = model.deployed ? ` · deployed ${model.deployed_sku || ""} ${model.deployed_capacity ?? ""}`.trim() : "";
  return `${model.sku} · ${rate(model.tpm)} TPM · ${rate(model.rpm)} RPM${deployed}`;
}

export default function DeployGptPage() {
  const [params, setParams] = useSearchParams();
  const requested = Number(params.get("account") || "");
  const accounts = useGptAccounts();
  const job = useGptJob();
  const start = useStartGptDeploy();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number | null>(Number.isFinite(requested) && requested > 0 ? requested : null);
  const [picked, setPicked] = useState<number[]>([]);
  const [checked, setChecked] = useState<string[]>([]);
  const [logScope, setLogScope] = useState<"account" | "all">("account");
  const [apiKey, setApiKey] = useState<string | null>(null);

  useEffect(() => {
    if (selected != null || !accounts.data?.length) return;
    const live = accounts.data.find((row) => row.new_api_status === 1 && !row.blocked);
    setSelected((live ?? accounts.data[0]).id);
  }, [accounts.data, selected]);

  const availability = useGptAvailability(selected);
  const logs = useGptLogs(logScope === "account" ? selected : null, Boolean(job.data?.running));

  const loadedAccount = availability.data?.account_id;
  useEffect(() => {
    if (loadedAccount == null) return;
    const names = (availability.data?.models ?? [])
      .filter((model) => model.available && !model.deployed)
      .map((model) => model.name);
    setChecked(names);
  }, [loadedAccount]);

  useEffect(() => {
    setApiKey(null);
  }, [selected]);

  const plan = availability.data;
  const remaining = (plan?.models ?? []).filter((model) => model.available && !model.deployed);
  const channelDeployed = Boolean(accounts.data?.find((row) => row.id === selected)?.gpt_deployed);
  const stackDeployed =
    channelDeployed ||
    ((plan?.models.length ?? 0) > 0 && (plan?.models.some((model) => model.deployed) ?? false) && remaining.length === 0);

  useEffect(() => {
    const deployed = new Set((accounts.data ?? []).filter((row) => row.gpt_deployed).map((row) => row.id));
    if (deployed.size === 0) return;
    setPicked((current) => {
      const next = current.filter((id) => !deployed.has(id));
      return next.length === current.length ? current : next;
    });
  }, [accounts.data]);

  const wasRunning = useRef(false);
  useEffect(() => {
    if (job.data?.running) {
      wasRunning.current = true;
      return;
    }
    if (!wasRunning.current) return;
    wasRunning.current = false;
    void availability.refetch();
    void accounts.refetch();
    void logs.refetch();
  }, [availability, job.data?.running, logs]);

  const visibleAccounts = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (accounts.data ?? []).filter((row) => {
      if (!needle) return true;
      return [row.name, row.owner_tag, row.resource_name, row.new_api_name, row.location]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [accounts.data, query]);

  const busy = Boolean(job.data?.running) || start.isPending;
  const eligibleVisible = visibleAccounts.filter((row) => !row.blocked && !row.gpt_deployed);

  function choose(id: number) {
    setSelected(id);
    setParams({ account: String(id) }, { replace: true });
  }

  function toggleAccount(id: number) {
    setPicked((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  function selectVisible() {
    setPicked((current) => {
      const next = new Set(current);
      for (const row of eligibleVisible) {
        if (row.gpt_deployed) continue;
        next.add(row.id);
      }
      return [...next];
    });
  }

  function toggle(name: string) {
    setChecked((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]));
  }

  async function deploy(ids: number[], stack: boolean) {
    if (!ids.length) return;
    if (ids.length > 1) setLogScope("all");
    try {
      const started = await start.mutateAsync({
        account_ids: ids,
        stack,
        models: stack ? [] : checked,
      });
      const skipped = started.skipped?.length ?? 0;
      const who = started.total === 1 ? started.account_name : `${started.total} accounts`;
      toastSuccess(
        `GPT deploy started for ${who}${skipped ? ` · ${skipped} skipped` : ""}`,
        "gpt-deploy"
      );
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Could not start the deploy.";
      toastError(typeof detail === "string" ? detail : "Could not start the deploy.", { toastId: "gpt-deploy" });
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-gray-50">Deploy GPT</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-400">
          10k accounts only. Each model is deployed at the highest TPM and RPM Azure still has, then added to an O1
          channel in the gpt-stack group. Spend from that channel is counted on the same account, so the existing stop
          limit disables the Kimi and GPT channels together.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Card className="h-fit">
          <label className="text-xs font-medium text-gray-400" htmlFor="gpt-account-search">
            10k accounts
          </label>
          <div className="relative mt-2">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              id="gpt-account-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Name, tag, or channel"
              className="w-full rounded-lg border border-surface-border bg-black/30 py-2 pl-8 pr-3 text-sm text-gray-100 outline-none placeholder:text-gray-600 focus:border-accent"
            />
          </div>
          <div className="mt-2 flex items-center justify-between gap-2 text-[11px]">
            <span className="text-gray-500">{picked.length} selected</span>
            <span className="flex gap-2">
              <button type="button" className="text-indigo-300 hover:text-indigo-100" onClick={selectVisible} disabled={busy}>
                Select visible
              </button>
              <button type="button" className="text-gray-400 hover:text-gray-200" onClick={() => setPicked([])} disabled={busy}>
                Clear
              </button>
            </span>
          </div>
          <div className="mt-2 max-h-[28rem] space-y-1 overflow-auto">
            {accounts.isLoading ? (
              <Spinner />
            ) : visibleAccounts.length === 0 ? (
              <p className="text-sm text-gray-500">No 10k accounts match.</p>
            ) : (
              visibleAccounts.map((row) => {
                const active = row.id === selected;
                const done = row.gpt_deployed;
                const live = row.new_api_status === 1 && !row.blocked;
                return (
                  <div
                    key={row.id}
                    className={`flex items-start gap-2 rounded-lg px-2 py-2 ${
                      active ? "bg-accent/15 text-indigo-100" : "text-gray-300"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      aria-label={`Deploy GPT on ${row.name}`}
                      checked={picked.includes(row.id)}
                      disabled={busy || row.blocked || done}
                      onChange={() => toggleAccount(row.id)}
                    />
                    <button type="button" onClick={() => choose(row.id)} className="min-w-0 flex-1 text-left">
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium">{row.name}</span>
                        <span className={`text-[10px] ${done || live ? "text-emerald-300" : "text-gray-500"}`}>
                          {row.blocked ? "blocked" : done ? "deployed" : live ? "live" : "off"}
                        </span>
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-gray-500">
                        {money(row.spend_usd)} / {money(row.stop_at_usd)} · {row.new_api_name || "no channel"}
                      </span>
                    </button>
                  </div>
                );
              })
            )}
          </div>
          <Button className="mt-3 w-full" disabled={busy || picked.length === 0} onClick={() => void deploy(picked, true)}>
            <Sparkles size={15} />
            Deploy GPT{picked.length ? ` · ${picked.length}` : ""}
          </Button>
          <p className="mt-2 text-[11px] leading-relaxed text-gray-500">
            Deploys the full GPT stack on every selected account, several at a time.
          </p>
        </Card>

        <Card>
          {!selected ? (
            <p className="text-sm text-gray-400">Select a 10k account.</p>
          ) : availability.isLoading ? (
            <Spinner />
          ) : availability.isError ? (
            <p className="text-sm text-red-300">Could not read Azure model availability for this account.</p>
          ) : plan && !plan.eligible ? (
            <p className="text-sm text-amber-200">{plan.eligibility_error}</p>
          ) : plan ? (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <p className="text-base font-semibold text-gray-50">{plan.account_name}</p>
                  <p className="text-xs text-gray-400">
                    {plan.location} · NewAPI {plan.new_api_name || "—"} · spend {money(plan.spend_usd)} · stop{" "}
                    {money(plan.stop_at_usd)}
                  </p>
                </div>
                {stackDeployed ? (
                  <div className="flex w-full min-w-0 flex-col gap-1 sm:max-w-md">
                    {plan.endpoint ? <CopyValueRow label="Endpoint" value={plan.endpoint} /> : null}
                    <GptApiKeyRow accountId={plan.account_id} apiKey={apiKey} onApiKey={setApiKey} />
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      disabled={busy || selected == null || checked.length === 0}
                      onClick={() => selected != null && void deploy([selected], false)}
                    >
                      Deploy models
                    </Button>
                    <Button
                      disabled={busy || selected == null || remaining.length === 0}
                      onClick={() => selected != null && void deploy([selected], true)}
                    >
                      <Sparkles size={15} />
                      Deploy GPT stack
                    </Button>
                  </div>
                )}
              </div>
              {job.data?.running && (
                <p className="text-xs text-indigo-200">
                  Deploying {job.data.done}/{job.data.total}
                  {job.data.current ? ` · ${job.data.current}` : ""} — progress is in the log below.
                </p>
              )}
              <div className="divide-y divide-white/[0.06] overflow-hidden rounded-xl border border-white/[0.06]">
                {plan.models.map((model) => (
                  <label
                    key={model.name}
                    className={`flex items-start gap-3 px-3 py-2.5 ${model.available || model.deployed ? "" : "opacity-60"} ${stackDeployed ? "" : "cursor-pointer"}`}
                  >
                    {stackDeployed ? null : (
                      <input
                        type="checkbox"
                        className="mt-1"
                        disabled={!model.available || model.deployed || busy}
                        checked={checked.includes(model.name)}
                        onChange={() => toggle(model.name)}
                      />
                    )}
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-gray-100">{model.name}</span>
                        {model.version && <span className="text-[11px] text-gray-500">{model.version}</span>}
                        {model.deployed && (
                          <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-200">
                            on Azure
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block text-xs text-gray-400">{modelLine(model)}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
          ) : null}
        </Card>
      </div>

      <Card>
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-gray-100">
            <ScrollText size={16} />
            Deploy log
          </div>
          <div className="flex rounded-lg border border-white/[0.08] p-0.5 text-xs">
            <button
              type="button"
              className={`rounded-md px-2 py-1 ${logScope === "account" ? "bg-white/10 text-gray-100" : "text-gray-400"}`}
              onClick={() => setLogScope("account")}
            >
              This account
            </button>
            <button
              type="button"
              className={`rounded-md px-2 py-1 ${logScope === "all" ? "bg-white/10 text-gray-100" : "text-gray-400"}`}
              onClick={() => setLogScope("all")}
            >
              All
            </button>
          </div>
        </div>
        <div className="max-h-80 space-y-1.5 overflow-auto font-mono text-[11px] leading-relaxed">
          {logs.isLoading ? (
            <Spinner />
          ) : (logs.data?.items.length ?? 0) === 0 ? (
            <p className="font-sans text-sm text-gray-500">No deploy logs yet.</p>
          ) : (
            logs.data?.items.map((row) => (
              <p key={row.id} className={row.level === "error" ? "text-red-300" : "text-gray-300"}>
                <span className="text-gray-500">
                  {row.created_at ? new Date(row.created_at).toLocaleString() : ""} {row.account_name} {row.action}
                </span>{" "}
                {row.message}
              </p>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}
