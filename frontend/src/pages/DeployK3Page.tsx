import { Download, Rocket, Upload } from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useState } from "react";

import DeployedKimiCard from "@/components/deploy/DeployedKimiCard";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import {
  useKimiDeploy,
  useKimiDeployStatus,
  useKimiInventory,
  useKimiRegenerateKeys,
  useKimiTestModel,
  useKimiUndeploy,
} from "@/hooks/useKimiDeploy";
import { formatCurrency } from "@/lib/format";
import { parseAzureDeploySecretsArray, toKimiDeployPayload } from "@/lib/parseAzureCredentials";
import { KimiCreditSnapshot, KimiDeployResult, KimiSecretsRow, KimiTestResult } from "@/types";

const SECRETS_STORAGE_KEY = "kimi-deploy-secrets";
const PARALLEL_JOBS = 64;

function parallelJobs(count: number) {
  return Math.max(1, Math.min(PARALLEL_JOBS, count));
}

export default function DeployK3Page() {
  const status = useKimiDeployStatus();
  const deploy = useKimiDeploy();
  const regenerate = useKimiRegenerateKeys();
  const undeploy = useKimiUndeploy();
  const testModel = useKimiTestModel();
  const [jsonText, setJsonText] = useState(() => {
    try {
      return sessionStorage.getItem(SECRETS_STORAGE_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [results, setResults] = useState<KimiDeployResult[] | null>(null);
  const [deletingIndex, setDeletingIndex] = useState<number | "all" | null>(null);
  const [rotatingIndex, setRotatingIndex] = useState<number | "all" | null>(null);
  const [testingIndex, setTestingIndex] = useState<number | "all" | null>(null);
  const [testByIndex, setTestByIndex] = useState<Record<number, KimiTestResult>>({});
  const [dragging, setDragging] = useState(false);

  const parsed = useMemo(() => parseAzureDeploySecretsArray(jsonText), [jsonText]);
  const parseError = jsonText.trim() ? parsed.error : null;
  const deployPayload = useMemo(
    () => (jsonText.trim() && !parseError ? toKimiDeployPayload(parsed.accounts) : []),
    [jsonText, parseError, parsed.accounts]
  );
  const secretsReady = Boolean(jsonText.trim() && !parseError && parsed.accounts.length > 0);
  const inventory = useKimiInventory(deployPayload, secretsReady);
  const inventoryRows = useMemo(
    () =>
      deployPayload.map((account, index) => {
        const query = inventory.queries[index];
        const row = query?.data?.results[0];
        const pasted = parsed.accounts[index];
        if (row) {
          return {
            ...row,
            subscription_name:
              row.subscription_name || pasted?.subscriptionName || account.subscription_name || null,
          };
        }
        return {
          ok: false,
          name: pasted?.name || account.name,
          email: pasted?.accountHolder || null,
          subscription_id: pasted?.subscriptionId || account.AZURE_SUBSCRIPTION_ID,
          subscription_name: pasted?.subscriptionName || null,
          pending: Boolean(query?.isFetching || query?.isPending),
          error: query?.isError ? "Could not list deployed resources for this account." : null,
        } satisfies KimiDeployResult;
      }),
    [deployPayload, inventory.queries, parsed.accounts]
  );
  const displayed = results ?? inventoryRows;
  const busy = deploy.isPending || regenerate.isPending || undeploy.isPending || testModel.isPending;
  const liveResults = displayed.filter((item) => item.ok && !item.removed);
  const testableResults = displayed.filter((item) => !item.removed && !item.error && !item.pending);
  const pendingCount = displayed.filter((item) => item.pending).length;
  const canDeploy = Boolean(secretsReady && status.data?.ready);

  useEffect(() => {
    try {
      if (jsonText.trim()) sessionStorage.setItem(SECRETS_STORAGE_KEY, jsonText);
      else sessionStorage.removeItem(SECRETS_STORAGE_KEY);
    } catch {
      /* ignore quota / private mode */
    }
  }, [jsonText]);

  const payloadKey = deployPayload
    .map((account) => `${account.AZURE_CLIENT_ID}:${account.AZURE_SUBSCRIPTION_ID}`)
    .join("|");

  useEffect(() => {
    setResults(null);
    setTestByIndex({});
  }, [payloadKey]);

  function applySecrets(rows: Record<string, string>[], filename: string, message: string) {
    setJsonText(JSON.stringify(rows, null, 2));
    downloadJson(filename, rows);
    setNotice(message);
  }

  function holderFromPaste(item: KimiDeployResult) {
    const match =
      parsed.accounts.find((account) => account.subscriptionId && account.subscriptionId === item.subscription_id) ??
      parsed.accounts.find((account) => account.name === item.name);
    return match?.accountHolder;
  }

  async function readSecretsFile(file: File) {
    const text = await file.text();
    setJsonText(text);
    setError(null);
    setNotice(null);
  }

  async function handleDeploy() {
    setError(null);
    setNotice(null);
    const next = parseAzureDeploySecretsArray(jsonText);
    if (next.error) {
      setError(next.error);
      return;
    }
    try {
      const response = await deploy.mutateAsync({
        accounts: toKimiDeployPayload(next.accounts),
        jobs: parallelJobs(next.accounts.length),
      });
      setResults(
        response.results.map((item, index) => ({
          ...item,
          email: item.email || next.accounts[index]?.accountHolder || item.email,
          name: item.name || next.accounts[index]?.name || item.name,
        }))
      );
      await inventory.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Deploy failed."));
    }
  }

  function payloadForResult(item: KimiDeployResult): Record<string, string> | null {
    const next = parseAzureDeploySecretsArray(jsonText);
    if (next.error || next.accounts.length === 0) return null;
    const match =
      next.accounts.find((account) => account.subscriptionId && account.subscriptionId === item.subscription_id) ??
      next.accounts.find((account) => account.name === item.name);
    if (!match) return null;
    const row = toKimiDeployPayload([match])[0];
    if (item.account_name) row.account_name = item.account_name;
    if (item.resource_group) row.resource_group = item.resource_group;
    if (item.azure_openai_endpoint) row.azure_openai_endpoint = item.azure_openai_endpoint;
    if (item.deployment_name) row.deployment_name = item.deployment_name;
    return row;
  }

  async function rotateAccounts(items: { item: KimiDeployResult; index: number }[], bulk: boolean) {
    setError(null);
    setNotice(null);
    const payloads: { index: number; name: string; payload: Record<string, string> }[] = [];
    const missing: string[] = [];
    for (const { item, index } of items) {
      const payload = payloadForResult(item);
      if (!payload) missing.push(displayName(item));
      else payloads.push({ index, name: displayName(item), payload });
    }
    if (missing.length > 0) {
      setError(`Paste the matching secrets JSON to rotate keys for: ${missing.join(", ")}.`);
      return;
    }
    const confirmed = window.confirm(
      bulk
        ? `Rotate secrets for ${payloads.length} deployed account${payloads.length === 1 ? "" : "s"}? Old JSON files stop working.`
        : `Rotate the secret for ${payloads[0]?.name}? Old JSON files for this SP stop working.`
    );
    if (!confirmed) return;
    setRotatingIndex(bulk ? "all" : payloads[0].index);
    try {
      const response = await regenerate.mutateAsync({
        accounts: payloads.map((row) => row.payload),
        jobs: parallelJobs(payloads.length),
      });
      let rows = jsonText.trim() && !parseError ? toKimiDeployPayload(parsed.accounts) : [];
      const failed: string[] = [];
      payloads.forEach((entry, responseIndex) => {
        const row = response.results[responseIndex];
        const payload = row ? secretsRowToPayload(row) : null;
        if (row?.ok && payload) rows = mergeRotatedInto(rows, payload);
        else failed.push(row?.error ? `${entry.name}: ${row.error}` : entry.name);
      });
      const filename = rows.length === 1 ? `${rows[0].name || "secrets"}.json` : "rotated_keys.json";
      applySecrets(
        rows,
        filename,
        failed.length > 0
          ? `Rotated ${response.ok_count}, failed ${response.fail_count}.`
          : `Rotated ${response.ok_count} secret${response.ok_count === 1 ? "" : "s"}.`
      );
      if (failed.length > 0) setError(failed.join("; "));
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not rotate keys."));
    } finally {
      setRotatingIndex(null);
    }
  }

  async function handleDeleteAll() {
    setError(null);
    setNotice(null);
    const items = displayed.map((item, index) => ({ item, index })).filter(({ item }) => item.ok && !item.removed);
    const payloads: Record<string, string>[] = [];
    const missing: string[] = [];
    for (const { item } of items) {
      const payload = payloadForResult(item);
      if (!payload) missing.push(displayName(item));
      else payloads.push(payload);
    }
    if (missing.length > 0) {
      setError(`Paste the matching secrets JSON to delete: ${missing.join(", ")}.`);
      return;
    }
    if (!window.confirm(`Delete FW-Kimi-K3 from ${payloads.length} subscription${payloads.length === 1 ? "" : "s"}?`)) {
      return;
    }
    setDeletingIndex("all");
    try {
      const response = await undeploy.mutateAsync({ accounts: payloads, jobs: parallelJobs(payloads.length) });
      const failed = response.results.filter((row) => !row.ok);
      setNotice(
        failed.length > 0
          ? `Deleted ${response.ok_count}, failed ${response.fail_count}.`
          : `Deleted ${response.ok_count} deployment${response.ok_count === 1 ? "" : "s"}.`
      );
      if (failed.length > 0) {
        setError(failed.map((row) => `${row.name ?? "account"}: ${row.error}`).join("; "));
      }
      setResults(
        displayed.map((item) => {
          const row = response.results.find(
            (entry) =>
              (entry.subscription_id && entry.subscription_id === item.subscription_id) || entry.name === item.name
          );
          if (!row?.ok) return item;
          return {
            ...item,
            removed: true,
            deleted_resources: row.deleted ?? [],
            deleted_message: row.message,
            azure_openai_endpoint: null,
            api_key: null,
          };
        })
      );
      await inventory.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not delete deployed resources."));
    } finally {
      setDeletingIndex(null);
    }
  }

  async function handleDeleteOne(item: KimiDeployResult, index: number) {
    setError(null);
    setNotice(null);
    const payload = payloadForResult(item);
    if (!payload) {
      setError("Paste the matching secrets JSON so this row can be deleted.");
      return;
    }
    if (!window.confirm(`Delete ${resourceLabel(item)}?`)) return;
    setDeletingIndex(index);
    try {
      const response = await undeploy.mutateAsync({ accounts: [payload], jobs: 1 });
      const row = response.results[0];
      if (!row?.ok) {
        setError(row?.error ?? "Delete failed.");
        return;
      }
      setNotice(row.message || `Deleted ${resourceLabel(item)}.`);
      setResults(
        displayed.map((entry, entryIndex) =>
          entryIndex === index
            ? {
                ...entry,
                removed: true,
                deleted_resources: row.deleted ?? [],
                deleted_message: row.message,
                azure_openai_endpoint: null,
                api_key: null,
              }
            : entry
        )
      );
      await inventory.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not delete this resource."));
    } finally {
      setDeletingIndex(null);
    }
  }

  async function handleTest(items: { item: KimiDeployResult; index: number }[], bulk: boolean) {
    setError(null);
    setNotice(null);
    const payloads: { index: number; name: string; payload: Record<string, string> }[] = [];
    const missing: string[] = [];
    for (const { item, index } of items) {
      const payload = payloadForResult(item);
      if (!payload) missing.push(displayName(item));
      else payloads.push({ index, name: displayName(item), payload });
    }
    if (missing.length > 0) {
      setError(`Paste the matching secrets JSON to test: ${missing.join(", ")}.`);
      return;
    }
    setTestingIndex(bulk ? "all" : payloads[0].index);
    try {
      const response = await testModel.mutateAsync({ accounts: payloads.map((row) => row.payload) });
      setTestByIndex((prev) => {
        const next = { ...prev };
        payloads.forEach((entry, responseIndex) => {
          const row = response.results[responseIndex];
          if (row) next[entry.index] = row;
        });
        return next;
      });
      const failed = response.results.filter((row) => !row.ok);
      setNotice(
        failed.length > 0
          ? `Tested ${response.ok_count} live, ${response.fail_count} failed.`
          : response.ok_count === 1
            ? "Model replied."
            : `${response.ok_count} models replied.`
      );
      if (failed.length > 0) {
        setError(failed.map((row) => `${row.name ?? "account"}: ${row.error}`).join("; "));
      }
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not test the model."));
    } finally {
      setTestingIndex(null);
    }
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void readSecretsFile(file);
  }

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void readSecretsFile(file);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="gradient-title text-2xl font-semibold tracking-tight">Deploy Kimi K3</h1>
          <p className="mt-1 text-sm text-gray-500">Paste a secrets JSON, deploy FW-Kimi-K3, then rotate keys or delete from the cards below.</p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:shrink-0">
          <Button onClick={() => void handleDeploy()} isLoading={deploy.isPending} disabled={busy || !canDeploy} className="w-full sm:w-auto">
            {!deploy.isPending && <Rocket size={16} />}
            {deploy.isPending
              ? `Deploying ${parsed.accounts.length}…`
              : parsed.accounts.length
                ? `Deploy ${parsed.accounts.length}`
                : "Deploy"}
          </Button>
        </div>
      </div>

      {status.isLoading && <Spinner />}
      {status.isError && <Banner tone="error">{statusApiError(status.error)}</Banner>}
      {status.data && !status.data.ready && <Banner tone="error">{status.data.message}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}
      {notice && <Banner tone="success">{notice}</Banner>}

      <Card className="flex flex-col gap-3 !p-4 sm:!p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-200">Secrets</h2>
          <div className="flex items-center gap-2">
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-white/[0.08] bg-surface-raised/80 px-3 py-1.5 text-xs font-medium text-gray-200 hover:border-accent/40">
              <Upload size={13} />
              Open file
              <input type="file" accept="application/json,.json" className="hidden" onChange={onFile} disabled={busy} />
            </label>
            {jsonText.trim() && !parseError && (
              <Button
                variant="ghost"
                className="px-2.5 py-1.5 text-xs"
                onClick={() =>
                  downloadJson(
                    parsed.accounts[0]?.name ? `${parsed.accounts[0].name}.json` : "secrets.json",
                    toKimiDeployPayload(parsed.accounts)
                  )
                }
                disabled={busy}
              >
                <Download size={13} />
              </Button>
            )}
          </div>
        </div>
        <label
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`block rounded-xl border border-dashed px-1 py-1 transition-colors ${
            dragging ? "border-accent/60 bg-accent/5" : "border-white/[0.08] bg-surface/60"
          }`}
        >
          <textarea
            value={jsonText}
            onChange={(e) => {
              setJsonText(e.target.value);
              setError(null);
            }}
            rows={8}
            spellCheck={false}
            disabled={busy}
            placeholder="Drop or paste secrets JSON"
            className="w-full resize-y bg-transparent px-3 py-2.5 font-mono text-xs leading-relaxed text-gray-100 outline-none placeholder:text-gray-600 disabled:opacity-60"
          />
        </label>
        {parseError && <p className="text-xs text-red-400">{parseError}</p>}
        {!parseError && parsed.accounts.length > 0 && (
          <ul className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            {parsed.accounts.map((account, index) => (
              <li
                key={`${account.name}-${account.subscriptionId}`}
                className="rounded-xl border border-white/[0.06] bg-white/[0.03] px-3.5 py-3"
              >
                <p className="truncate text-sm font-medium text-gray-100">{account.name}</p>
                <p className="truncate text-xs text-gray-500">{account.accountHolder || "No email"}</p>
                <p className="mt-1 truncate text-xs text-gray-400">
                  {account.subscriptionName || "Azure subscription"}
                </p>
                <div className="mt-2 text-xs">
                  <CreditLine
                    snapshot={creditSnapshotFromDeploy(displayed[index] ?? inventory.queries[index]?.data?.results[0])}
                    loading={Boolean(inventory.queries[index]?.isFetching && !inventory.queries[index]?.data)}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
        {deploy.isPending && (
          <p className="text-xs text-gray-500">Accounts deploy in parallel. Each one can take a few minutes — keep this tab open.</p>
        )}
      </Card>

      {secretsReady && (
        <section className="flex flex-col gap-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-gray-200">Deployed</h2>
              <p className="text-xs text-gray-500">
                {pendingCount > 0
                  ? `Looking up ${pendingCount} subscription${pendingCount === 1 ? "" : "s"} in parallel…`
                  : `${liveResults.length} live${
                      displayed.filter((item) => !item.ok && item.error).length
                        ? ` · ${displayed.filter((item) => !item.ok && item.error).length} failed`
                        : ""
                    }${
                      displayed.filter((item) => item.removed).length
                        ? ` · ${displayed.filter((item) => item.removed).length} deleted`
                        : ""
                    }`}
              </p>
            </div>
            {testableResults.length > 0 && (
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  onClick={() =>
                    void handleTest(
                      displayed
                        .map((item, index) => ({ item, index }))
                        .filter(({ item }) => !item.removed && !item.error && !item.pending),
                      true
                    )
                  }
                  isLoading={testModel.isPending && testingIndex === "all"}
                  disabled={busy}
                >
                  Test all
                </Button>
                {liveResults.length > 0 && (
                  <>
                    <Button
                      variant="secondary"
                      className="px-3 py-1.5 text-xs"
                      onClick={() =>
                        void rotateAccounts(
                          displayed.map((item, index) => ({ item, index })).filter(({ item }) => item.ok && !item.removed),
                          true
                        )
                      }
                      isLoading={regenerate.isPending && rotatingIndex === "all"}
                      disabled={busy}
                    >
                      Rotate all
                    </Button>
                    <Button
                      variant="danger"
                      className="px-3 py-1.5 text-xs"
                      onClick={() => void handleDeleteAll()}
                      isLoading={undeploy.isPending && deletingIndex === "all"}
                      disabled={busy}
                    >
                      Delete all
                    </Button>
                  </>
                )}
              </div>
            )}
          </div>
          {displayed.length === 0 ? (
            <p className="text-sm text-gray-500">No FW-Kimi-K3 found on these subscriptions yet.</p>
          ) : (
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {displayed.map((item, index) => (
                <DeployedKimiCard
                  key={`${item.subscription_id ?? item.name ?? "row"}-${index}`}
                  item={item}
                  email={item.email || holderFromPaste(item)}
                  busy={busy}
                  rotating={regenerate.isPending && rotatingIndex === index}
                  testing={testModel.isPending && (testingIndex === index || testingIndex === "all")}
                  deleting={undeploy.isPending && deletingIndex === index}
                  testResult={testByIndex[index]}
                  onRotate={() => void rotateAccounts([{ item, index }], false)}
                  onTest={() => void handleTest([{ item, index }], false)}
                  onDelete={() => void handleDeleteOne(item, index)}
                />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function CreditLine({ snapshot, loading }: { snapshot?: KimiCreditSnapshot; loading?: boolean }) {
  if (loading && !snapshot) return <p className="text-gray-500">Looking up grant…</p>;
  if (!snapshot) return <p className="text-gray-600">Credit grant —</p>;
  const currency = snapshot.credits_currency || "USD";
  if (snapshot.credits_available && snapshot.credits_limit != null) {
    return (
      <p className="tabular-nums text-gray-200">
        {formatCurrency(snapshot.credits_limit, currency)} grant
        {snapshot.credits_remaining != null ? ` · ${formatCurrency(snapshot.credits_remaining, currency)} left` : ""}
      </p>
    );
  }
  if (snapshot.credits_available && snapshot.credits_remaining != null) {
    return <p className="tabular-nums text-gray-200">{formatCurrency(snapshot.credits_remaining, currency)} left</p>;
  }
  return <p className="text-gray-500">{snapshot.error || "Grant unavailable"}</p>;
}

function creditSnapshotFromDeploy(item?: KimiDeployResult | null): KimiCreditSnapshot | undefined {
  if (!item || item.pending) return undefined;
  if (!item.credits_available && !item.credits_limit && !item.credits_remaining) {
    return item.error ? { ok: false, name: item.name, error: item.error } : undefined;
  }
  return {
    ok: Boolean(item.credits_available),
    name: item.name,
    subscription_id: item.subscription_id,
    subscription_name: item.subscription_name,
    credits_limit: item.credits_limit,
    credits_remaining: item.credits_remaining,
    credits_used: item.credits_used,
    credits_currency: item.credits_currency,
    credits_label: item.credits_label,
    credits_available: Boolean(item.credits_available),
  };
}

function mergeRotatedInto(rows: Record<string, string>[], payload: Record<string, string>) {
  const next = rows.map((row) => ({ ...row }));
  const index = next.findIndex(
    (row) =>
      (payload.AZURE_SUBSCRIPTION_ID && row.AZURE_SUBSCRIPTION_ID === payload.AZURE_SUBSCRIPTION_ID) ||
      (payload.name && row.name === payload.name)
  );
  if (index >= 0) next[index] = { ...next[index], ...payload };
  else next.push(payload);
  return next;
}

function displayName(item: KimiDeployResult) {
  return item.name || item.account_name || item.email || "account";
}

function resourceLabel(item: KimiDeployResult) {
  if (item.account_name && item.resource_group) return `${item.account_name} (${item.resource_group})`;
  if (item.account_name) return item.account_name;
  return item.name || "this deployment";
}

function secretsRowToPayload(row: KimiSecretsRow): Record<string, string> | null {
  if (!row.AZURE_TENANT_ID || !row.AZURE_CLIENT_ID || !row.AZURE_CLIENT_SECRET || !row.AZURE_SUBSCRIPTION_ID) {
    return null;
  }
  const payload: Record<string, string> = {
    name: row.name || "account",
    AZURE_TENANT_ID: row.AZURE_TENANT_ID,
    AZURE_CLIENT_ID: row.AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET: row.AZURE_CLIENT_SECRET,
    AZURE_SUBSCRIPTION_ID: row.AZURE_SUBSCRIPTION_ID,
  };
  if (row.account_holder) payload.account_holder = row.account_holder;
  if (row.subscription_name) payload.subscription_name = row.subscription_name;
  return payload;
}

function statusApiError(error: unknown): string {
  const err = error as { code?: string; response?: { status?: number } } | undefined;
  if (err?.code === "ERR_NETWORK" || err?.code === "ECONNREFUSED") {
    return "The backend is not running. From this repo run docker compose up -d --build.";
  }
  if (err?.response?.status === 404) {
    return "This backend build does not include /api/kimi-deploy. Rebuild with docker compose up -d --build.";
  }
  if (err?.response?.status === 401) return "Your session expired. Sign in again.";
  return "Could not reach the deploy API.";
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([`${JSON.stringify(data, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".json") ? filename : `${filename}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function apiErrorMessage(err: { response?: { data?: { detail?: unknown } }; message?: string }, fallback: string) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          const msg = (item as { msg: unknown }).msg;
          if (typeof msg === "string") return msg;
        }
        return JSON.stringify(item);
      })
      .filter(Boolean)
      .join("; ");
  }
  return err?.message ?? fallback;
}
