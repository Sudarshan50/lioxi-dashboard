import { Check, Copy, FileSpreadsheet, KeyRound, Play, Radio, RefreshCw, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import { formatCurrency, formatTokens } from "@/lib/format";
import { KimiDeployResult, KimiTestResult } from "@/types";

export default function DeployedKimiCard({
  item,
  email,
  busy,
  deploying = false,
  rotating,
  testing,
  deleting,
  addingNewApi,
  renamingNewApi,
  syncingSheet,
  refreshing,
  nextNewApiName,
  defaultPriority = 13,
  defaultWeight = 1,
  jsonPriority,
  jsonWeight,
  testResult,
  onRotate,
  onTest,
  onDelete,
  onAddNewApi,
  onSaveNewApi,
  onSyncSheet,
  onRefresh,
}: {
  item: KimiDeployResult;
  email?: string | null;
  busy: boolean;
  deploying?: boolean;
  rotating: boolean;
  testing: boolean;
  deleting: boolean;
  addingNewApi: boolean;
  renamingNewApi: boolean;
  syncingSheet: boolean;
  refreshing: boolean;
  nextNewApiName?: string | null;
  defaultPriority?: number;
  defaultWeight?: number;
  jsonPriority?: number;
  jsonWeight?: number;
  testResult?: KimiTestResult | null;
  onRotate: () => void;
  onTest: () => void;
  onDelete: () => void;
  onAddNewApi: (opts?: { name?: string; priority?: number; weight?: number }) => void;
  onSaveNewApi: (opts: { name: string; priority: number; weight: number }) => void;
  onSyncSheet: () => void;
  onRefresh: () => void;
}) {
  const live = item.ok && !item.removed;
  const grant = formatGrant(item);
  const endpoint = openaiEndpoint(item.azure_openai_endpoint);
  const canAddNewApi = Boolean((live || item.account_name) && !item.removed && !item.pending && !item.new_api_present);
  const seedPriority = item.new_api_priority ?? jsonPriority ?? defaultPriority;
  const seedWeight = item.new_api_weight ?? jsonWeight ?? defaultWeight;
  const [draftName, setDraftName] = useState(item.new_api_name || "");
  const [draftPriority, setDraftPriority] = useState(String(seedPriority));
  const [draftWeight, setDraftWeight] = useState(String(seedWeight));

  useEffect(() => {
    setDraftName(item.new_api_name || "");
    setDraftPriority(String(seedPriority));
    setDraftWeight(String(seedWeight));
  }, [
    item.new_api_channel_id,
    item.new_api_name,
    seedPriority,
    seedWeight,
  ]);

  const trimmedName = draftName.trim();
  const parsedPriority = clampInt(draftPriority, seedPriority, 0, 10000);
  const parsedWeight = clampInt(draftWeight, seedWeight, 1, 10000);
  const nameToSave = trimmedName || (item.new_api_name || "").trim();
  const nameChanged = nameToSave !== (item.new_api_name || "").trim();
  const priChanged = parsedPriority !== seedPriority;
  const wtChanged = parsedWeight !== seedWeight;
  const canSaveRouting = Boolean(
    item.new_api_present && nameToSave && (nameChanged || priChanged || wtChanged) && !item.pending && !item.removed
  );

  const showActions = !item.removed && !item.error && !item.pending;
  const canRefresh = !item.removed && !deploying;

  return (
    <Card className={`flex flex-col gap-0 !p-0 ${deploying ? "!border-accent/40 shadow-glow" : ""}`}>
      <div className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <p className="truncate text-base font-medium text-gray-100">{item.name || item.account_name || "Account"}</p>
            {item.owner_tag && (
              <Badge tone="info" className="max-w-[9rem] shrink-0 truncate" title="Name tag">
                {item.owner_tag}
              </Badge>
            )}
            {item.removed ? (
              <Badge tone="warning">deleted</Badge>
            ) : item.pending ? (
              <Badge tone="info" className={deploying ? "animate-pulse" : undefined}>
                {deploying ? "deploying" : "looking up"}
              </Badge>
            ) : item.ok ? (
              <Badge tone="success">deployed</Badge>
            ) : item.error ? (
              <Badge tone="error">failed</Badge>
            ) : (
              <Badge tone="neutral">not deployed</Badge>
            )}
            <NewApiBadge item={item} />
          </div>
          <p className="mt-1 truncate text-xs text-gray-500">{email || "No email"}</p>
        </div>

        {item.pending && (
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/70" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
            </span>
            {deploying ? "Deploying this Azure stack in parallel…" : "Looking up this subscription…"}
          </div>
        )}
        {item.error && <p className="break-words text-xs text-red-400">{item.error}</p>}
        {item.deleted_message && <p className="break-words text-xs text-amber-300">{item.deleted_message}</p>}
        {!live && !item.removed && !item.error && !item.pending && (
          <p className="text-xs text-gray-500">No FW-Kimi-K3 on this subscription yet.</p>
        )}

        {(live || item.removed || item.account_name || item.subscription_id) && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="min-w-0 sm:col-span-2">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Endpoint</p>
              {endpoint ? <CopyLine value={endpoint} /> : <p className="mt-0.5 text-xs text-gray-600">—</p>}
            </div>
            <div className="min-w-0 sm:col-span-2">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">NewAPI channel</p>
              {item.pending ? (
                <p className="mt-1 text-xs text-gray-100">{deploying ? "Waiting on Azure…" : "Looking up…"}</p>
              ) : item.new_api_present || canAddNewApi ? (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <input
                    value={draftName}
                    onChange={(event) => setDraftName(event.target.value)}
                    disabled={busy}
                    maxLength={128}
                    placeholder={item.new_api_present ? "Channel name" : nextNewApiName || "kimi-k3-500k-proxy-…"}
                    className="min-w-[12rem] flex-1 rounded-lg border border-white/[0.08] bg-surface px-2.5 py-1.5 font-mono text-xs text-gray-100 outline-none placeholder:text-gray-600 focus:border-accent disabled:opacity-60"
                  />
                  <label className="flex items-center gap-1 text-[11px] text-gray-500">
                    P
                    <input
                      type="number"
                      min={0}
                      max={10000}
                      value={draftPriority}
                      onChange={(event) => setDraftPriority(event.target.value)}
                      disabled={busy}
                      className="w-16 rounded-lg border border-white/[0.08] bg-surface px-2 py-1.5 font-mono text-xs text-gray-100 outline-none focus:border-accent disabled:opacity-60"
                    />
                  </label>
                  <label className="flex items-center gap-1 text-[11px] text-gray-500">
                    W
                    <input
                      type="number"
                      min={1}
                      max={10000}
                      value={draftWeight}
                      onChange={(event) => setDraftWeight(event.target.value)}
                      disabled={busy}
                      className="w-16 rounded-lg border border-white/[0.08] bg-surface px-2 py-1.5 font-mono text-xs text-gray-100 outline-none focus:border-accent disabled:opacity-60"
                    />
                  </label>
                  {item.new_api_present ? (
                    <Button
                      variant="secondary"
                      className="px-2.5 py-1.5 text-xs"
                      onClick={() => onSaveNewApi({ name: nameToSave, priority: parsedPriority, weight: parsedWeight })}
                      isLoading={renamingNewApi}
                      disabled={busy || !canSaveRouting}
                    >
                      {!renamingNewApi && <Pencil size={13} />}
                      Save
                    </Button>
                  ) : null}
                </div>
              ) : (
                <p className="mt-1 truncate text-xs text-gray-100">{item.new_api_error || "Not in NewAPI"}</p>
              )}
              {item.new_api_present && item.new_api_status_label && (
                <p className="mt-1 truncate text-[11px] text-gray-500">{item.new_api_status_label}</p>
              )}
            </div>
            <Meta label="Foundry" value={item.account_name} />
            <Meta label="Subscription" value={item.subscription_name || item.subscription_id} />
            <div>
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Credit grant</p>
              <p className="mt-0.5 text-sm tabular-nums text-gray-100">{grant.primary}</p>
              {grant.secondary && <p className="text-xs tabular-nums text-gray-500">{grant.secondary}</p>}
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-gray-500">TPM / RPM</p>
              <p className="mt-0.5 text-sm tabular-nums text-gray-100">
                {live && item.tpm != null ? formatTokens(item.tpm) : "—"} / {live ? item.rpm ?? "—" : "—"}
              </p>
            </div>
          </div>
        )}

        {testResult && (
          <div
            className={`rounded-lg border px-3 py-2 text-xs ${
              testResult.ok
                ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
                : "border-red-500/25 bg-red-500/10 text-red-300"
            }`}
          >
            {testResult.ok ? (
              <>
                <p className="font-medium">
                  Model replied{testResult.latency_ms != null ? ` · ${testResult.latency_ms}ms` : ""}
                </p>
                <p className="mt-1 break-words text-emerald-100/90">{testResult.reply || "(empty reply)"}</p>
              </>
            ) : (
              <p className="break-words">{testResult.error || "Test failed."}</p>
            )}
          </div>
        )}
      </div>

      {(showActions || canRefresh) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-white/[0.06] bg-black/20 px-4 py-3 sm:px-5">
          {canRefresh && (
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              onClick={onRefresh}
              isLoading={refreshing}
              disabled={busy && !refreshing}
              title="Re-check Azure inventory and NewAPI for this stack"
            >
              {!refreshing && <RefreshCw size={13} />}
              Refresh
            </Button>
          )}
          {live && (
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              onClick={onSyncSheet}
              isLoading={syncingSheet}
              disabled={busy}
              title="Write Email, Endpoint, TPM, Proxy_Name, and Pool to Sheet1"
            >
              {!syncingSheet && <FileSpreadsheet size={13} />}
              Sync to sheet
            </Button>
          )}
          {showActions && (
            <>
          <Button
            variant="secondary"
            className="px-3 py-1.5 text-xs"
            onClick={onTest}
            isLoading={testing}
            disabled={busy}
          >
            {!testing && <Play size={13} />}
            Test
          </Button>
          {canAddNewApi && (
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              onClick={() => onAddNewApi({ name: trimmedName || undefined, priority: parsedPriority, weight: parsedWeight })}
              isLoading={addingNewApi}
              disabled={busy}
            >
              {!addingNewApi && <Radio size={13} />}
              Add to NewAPI
            </Button>
          )}
          <Button
            variant="ghost"
            className="px-3 py-1.5 text-xs"
            onClick={onRotate}
            isLoading={rotating}
            disabled={busy}
          >
            {!rotating && <KeyRound size={13} />}
            Rotate keys
          </Button>
          {live && (
            <Button
              variant="danger"
              className="ml-auto px-3 py-1.5 text-xs"
              onClick={onDelete}
              isLoading={deleting}
              disabled={busy}
            >
              {!deleting && <Trash2 size={13} />}
              Delete
            </Button>
          )}
            </>
          )}
        </div>
      )}
    </Card>
  );
}

function NewApiBadge({ item }: { item: KimiDeployResult }) {
  if (item.pending) return null;
  if (item.new_api_present) {
    const label = item.new_api_status_label || "in NewAPI";
    const tone = item.new_api_status === 1 ? "success" : item.new_api_status === 3 ? "error" : "warning";
    return (
      <Badge tone={tone} title={item.new_api_name || "NewAPI channel"}>
        NewAPI {label}
      </Badge>
    );
  }
  if (item.new_api_error) {
    return (
      <Badge tone="error" title={item.new_api_error}>
        NewAPI failed
      </Badge>
    );
  }
  if (item.ok || item.account_name) {
    return (
      <Badge tone="warning" title="No matching channel on O1 NewAPI">
        not in NewAPI
      </Badge>
    );
  }
  return null;
}

function clampInt(raw: string, fallback: number, min: number, max: number) {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

function Meta({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] uppercase tracking-wide text-gray-500">{label}</p>
      <p className={`mt-0.5 truncate text-sm ${mono ? "font-mono text-gray-400" : "text-gray-100"}`}>{value || "—"}</p>
    </div>
  );
}

function openaiEndpoint(url?: string | null) {
  if (!url) return "";
  return url
    .replace(".cognitiveservices.azure.com", ".openai.azure.com")
    .replace(".services.ai.azure.com", ".openai.azure.com");
}

function formatGrant(item: KimiDeployResult) {
  const currency = item.credits_currency || "USD";
  if (item.credits_available && item.credits_limit != null) {
    return {
      primary: formatCurrency(item.credits_limit, currency),
      secondary: item.credits_remaining != null ? `${formatCurrency(item.credits_remaining, currency)} left` : null,
    };
  }
  if (item.credits_available && item.credits_remaining != null) {
    return { primary: `${formatCurrency(item.credits_remaining, currency)} left`, secondary: null };
  }
  return { primary: "Unavailable", secondary: null };
}

function CopyLine({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      title="Copy OpenAI endpoint"
      className="mt-0.5 inline-flex max-w-full items-center gap-1.5 text-left font-mono text-gray-200 hover:text-white"
    >
      <span className="truncate">{value}</span>
      {copied ? <Check size={12} className="shrink-0 text-emerald-400" /> : <Copy size={12} className="shrink-0 text-gray-500" />}
    </button>
  );
}
