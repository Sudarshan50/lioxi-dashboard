import { Check, Copy, Play, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import { formatCurrency, formatTokens } from "@/lib/format";
import { KimiDeployResult, KimiTestResult } from "@/types";

export default function DeployedKimiCard({
  item,
  email,
  busy,
  rotating,
  testing,
  deleting,
  testResult,
  onRotate,
  onTest,
  onDelete,
}: {
  item: KimiDeployResult;
  email?: string | null;
  busy: boolean;
  rotating: boolean;
  testing: boolean;
  deleting: boolean;
  testResult?: KimiTestResult | null;
  onRotate: () => void;
  onTest: () => void;
  onDelete: () => void;
}) {
  const live = item.ok && !item.removed;
  const grant = formatGrant(item);
  const endpoint = openaiEndpoint(item.azure_openai_endpoint);

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <p className="truncate font-medium text-gray-100">{item.name || item.account_name || "Account"}</p>
            {item.removed ? (
              <Badge tone="warning">deleted</Badge>
            ) : item.pending ? (
              <Badge tone="info">looking up</Badge>
            ) : item.ok ? (
              <Badge tone="success">deployed</Badge>
            ) : item.error ? (
              <Badge tone="error">failed</Badge>
            ) : (
              <Badge tone="neutral">not deployed</Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-gray-500">{email || "No email"}</p>
        </div>
        {!item.removed && !item.error && !item.pending && (
          <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
            <Button
              variant="secondary"
              className="px-2.5 py-1.5 text-xs"
              onClick={onRotate}
              isLoading={rotating}
              disabled={busy}
            >
              {!rotating && <RefreshCw size={13} />}
              Rotate keys
            </Button>
            <Button
              variant="secondary"
              className="px-2.5 py-1.5 text-xs"
              onClick={onTest}
              isLoading={testing}
              disabled={busy}
            >
              {!testing && <Play size={13} />}
              Test model
            </Button>
            {live && (
              <Button
                variant="danger"
                className="px-2.5 py-1.5 text-xs"
                onClick={onDelete}
                isLoading={deleting}
                disabled={busy}
              >
                {!deleting && <Trash2 size={13} />}
                Delete
              </Button>
            )}
          </div>
        )}
      </div>

      {item.pending && (
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-gray-500/40 border-t-gray-100" />
          Looking up this subscription…
        </div>
      )}
      {item.error && <p className="break-words text-xs text-red-400">{item.error}</p>}
      {item.deleted_message && <p className="break-words text-xs text-amber-300">{item.deleted_message}</p>}
      {!live && !item.removed && !item.error && !item.pending && (
        <p className="text-xs text-gray-500">No FW-Kimi-K3 on this subscription yet.</p>
      )}

      {(live || item.removed || item.account_name || item.subscription_id) && (
        <dl className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
          <Meta label="Foundry account" value={item.account_name} />
          <Meta label="Resource group" value={item.resource_group} />
          <Meta
            label="Deployment"
            value={[item.deployment_name || "FW-Kimi-K3", item.region].filter(Boolean).join(" · ")}
          />
          <Meta label="Subscription" value={item.subscription_name || item.subscription_id} />
          <div className="sm:col-span-2">
            <p className="text-gray-500">OpenAI endpoint</p>
            {endpoint ? (
              <CopyLine value={endpoint} />
            ) : (
              <p className="mt-0.5 text-gray-600">—</p>
            )}
          </div>
        </dl>
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

      {(live || item.credits_available) && (
        <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-white/[0.06] pt-3 text-xs">
          <div>
            <p className="text-gray-500">Credit grant</p>
            <p className="mt-0.5 tabular-nums text-gray-100">{grant.primary}</p>
            {grant.secondary && <p className="tabular-nums text-gray-500">{grant.secondary}</p>}
          </div>
          {live && (
            <div>
              <p className="text-gray-500">TPM / RPM</p>
              <p className="mt-0.5 tabular-nums text-gray-100">
                {item.tpm != null ? formatTokens(item.tpm) : "—"} / {item.rpm ?? "—"}
              </p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function Meta({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-gray-500">{label}</p>
      <p className={`mt-0.5 truncate ${mono ? "font-mono text-gray-400" : "text-gray-100"}`}>{value || "—"}</p>
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
