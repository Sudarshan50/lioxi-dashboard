import { useQueryClient } from "@tanstack/react-query";
import { Rocket, Upload } from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useState } from "react";

import DeployedKimiCard from "@/components/deploy/DeployedKimiCard";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import {
  invalidateAfterDeploy,
  streamKimiDeploy,
  useKimiAddNewApi,
  useKimiDeployStatus,
  useKimiInventory,
  useKimiNewApiPool,
  useKimiRenameNewApi,
  useKimiRegenerateKeys,
  useKimiRefreshInventory,
  useKimiSheetStatus,
  useKimiSheetSync,
  useKimiStoredAccounts,
  useKimiTestModel,
  useKimiUndeploy,
} from "@/hooks/useKimiDeploy";
import { canonicalOwner } from "@/lib/ownerTag";
import { AzureDeploySecret, parseAzureDeploySecretsArray, toKimiDeployPayload } from "@/lib/parseAzureCredentials";
import { KimiDeployProgressEvent, KimiDeployResult, KimiNewApiPool, KimiStoredAccount, KimiTestResult } from "@/types";

const PARALLEL_JOBS = 64;
const LEFTOVER_SECRETS_KEY = "kimi-deploy-secrets";

type DeployRunProgress = {
  total: number;
  done: number;
  phase: string;
  message: string;
  startedAt: number;
};

function parallelJobs(count: number) {
  return Math.max(1, Math.min(PARALLEL_JOBS, count));
}

try {
  sessionStorage.removeItem(LEFTOVER_SECRETS_KEY);
} catch {
  /* ignore quota / private mode */
}

export default function DeployK3Page() {
  const queryClient = useQueryClient();
  const status = useKimiDeployStatus();
  const stored = useKimiStoredAccounts();
  const regenerate = useKimiRegenerateKeys();
  const undeploy = useKimiUndeploy();
  const testModel = useKimiTestModel();
  const addNewApi = useKimiAddNewApi();
  const renameNewApi = useKimiRenameNewApi();
  const sheetStatus = useKimiSheetStatus();
  const sheetSync = useKimiSheetSync();
  const refreshInventory = useKimiRefreshInventory();
  const [jsonText, setJsonText] = useState("");
  const [jsonLocked, setJsonLocked] = useState(false);
  const [loadedAccounts, setLoadedAccounts] = useState<AzureDeploySecret[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [results, setResults] = useState<KimiDeployResult[] | null>(null);
  const [deletingIndex, setDeletingIndex] = useState<number | "all" | null>(null);
  const [rotatingIndex, setRotatingIndex] = useState<number | "all" | null>(null);
  const [testingIndex, setTestingIndex] = useState<number | "all" | null>(null);
  const [addingNewApiIndex, setAddingNewApiIndex] = useState<number | "all" | null>(null);
  const [renamingNewApiIndex, setRenamingNewApiIndex] = useState<number | null>(null);
  const [syncingSheetIndex, setSyncingSheetIndex] = useState<number | "all" | null>(null);
  const [refreshingIndex, setRefreshingIndex] = useState<number | null>(null);
  const [testByIndex, setTestByIndex] = useState<Record<number, KimiTestResult>>({});
  const [dragging, setDragging] = useState(false);
  const [newApiPriority, setNewApiPriority] = useState(13);
  const [newApiWeight, setNewApiWeight] = useState(1);
  const [deploying, setDeploying] = useState(false);
  const [deployProgress, setDeployProgress] = useState<DeployRunProgress | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!deploying) return;
    const id = window.setInterval(() => setNow(Date.now()), 400);
    return () => window.clearInterval(id);
  }, [deploying]);

  const parsed = useMemo(() => parseAzureDeploySecretsArray(jsonText), [jsonText]);
  const parseError = jsonText.trim() ? parsed.error : null;
  const storedSecrets = useMemo(
    () => (stored.data?.accounts ?? []).map(storedToSecret),
    [stored.data]
  );
  const sessionActive = jsonLocked && loadedAccounts.length > 0;
  const workingAccounts = sessionActive ? loadedAccounts : storedSecrets;
  const deployPayload = useMemo(
    () =>
      toKimiDeployPayload(workingAccounts).map((row) => {
        if (!row.AZURE_CLIENT_SECRET) return row;
        const { AZURE_CLIENT_SECRET: _secret, ...rest } = row;
        return rest;
      }),
    [workingAccounts]
  );
  const showDeployed = workingAccounts.length > 0 || Boolean(results);
  const inventory = useKimiInventory(deployPayload, showDeployed && !deploying);
  const newApiPool = useKimiNewApiPool(showDeployed);
  const inventoryRows = useMemo(
    () =>
      deployPayload.map((account, index) => {
        const query = inventory.queries[index];
        const row = query?.data?.results[0];
        const pasted = workingAccounts[index];
        if (row) {
          return {
            ...row,
            subscription_name:
              row.subscription_name || pasted?.subscriptionName || account.subscription_name || null,
            owner_tag: row.owner_tag || pasted?.personAssociated || null,
          };
        }
        return {
          ok: false,
          name: pasted?.name || account.name,
          email: pasted?.accountHolder || null,
          subscription_id: pasted?.subscriptionId || account.AZURE_SUBSCRIPTION_ID,
          subscription_name: pasted?.subscriptionName || null,
          owner_tag: pasted?.personAssociated || null,
          pending: Boolean(query?.isFetching || query?.isPending),
          error: query?.isError ? "Could not list deployed resources for this account." : null,
        } satisfies KimiDeployResult;
      }),
    [deployPayload, inventory.queries, workingAccounts]
  );
  const displayed = useMemo(
    () => (results ?? inventoryRows).map((item) => mergeNewApi(item, newApiPool.data)),
    [inventoryRows, newApiPool.data, results]
  );
  const busy =
    deploying ||
    regenerate.isPending ||
    undeploy.isPending ||
    testModel.isPending ||
    addNewApi.isPending ||
    renameNewApi.isPending ||
    sheetSync.isPending;
  const liveResults = displayed.filter((item) => item.ok && !item.removed);
  const testableResults = displayed.filter((item) => !item.removed && !item.error && !item.pending);
  const pendingCount = displayed.filter((item) => item.pending).length;
  const pendingParse = Boolean(jsonText.trim() && !parseError && parsed.accounts.length > 0);
  const canDeploy = Boolean(status.data?.ready && (sessionActive || pendingParse));

  function commitSecrets(): AzureDeploySecret[] | null {
    if (jsonLocked && loadedAccounts.length > 0) return loadedAccounts;
    const next = parseAzureDeploySecretsArray(jsonText);
    if (next.error) {
      setError(next.error);
      return null;
    }
    if (next.accounts.length === 0) {
      setError("Paste a secrets JSON array first.");
      return null;
    }
    const accounts = next.accounts.map((account) => ({
      ...account,
      personAssociated: account.personAssociated ? canonicalOwner(account.personAssociated) : undefined,
    }));
    setLoadedAccounts(accounts);
    setJsonLocked(true);
    setResults(null);
    setTestByIndex({});
    setDeployProgress(null);
    setError(null);
    setNotice(null);
    return accounts;
  }

  function secretsForActions(): AzureDeploySecret[] | null {
    if (loadedAccounts.length > 0) return loadedAccounts;
    if (storedSecrets.length > 0) return storedSecrets;
    if (jsonText.trim()) return commitSecrets();
    setError("Paste the matching secrets JSON first.");
    return null;
  }

  function holderFromPaste(item: KimiDeployResult) {
    const match =
      workingAccounts.find((account) => account.subscriptionId && account.subscriptionId === item.subscription_id) ??
      workingAccounts.find((account) => account.name === item.name);
    return match?.accountHolder;
  }

  async function readSecretsFile(file: File) {
    if (jsonLocked || busy) return;
    const text = await file.text();
    setJsonText(text);
    setError(null);
    setNotice(null);
  }

  function applyDeployEvent(event: KimiDeployProgressEvent, accounts: AzureDeploySecret[]) {
    if (event.type === "start") {
      setDeployProgress((prev) => ({
        total: event.total ?? accounts.length,
        done: 0,
        phase: event.phase || "azure",
        message: event.message || `Deploying ${accounts.length} Azure stacks in parallel`,
        startedAt: prev?.startedAt ?? Date.now(),
      }));
      return;
    }
    if (event.type === "account" && event.result && event.index != null) {
      const pasted = accounts[event.index];
      const result = event.result;
      const index = event.index;
      setResults((prev) => {
        const next = [...(prev ?? [])];
        while (next.length <= index) next.push({ ok: false, pending: true });
        next[index] = {
          ...result,
          ok: Boolean(result.ok),
          pending: false,
          email: result.email || pasted?.accountHolder || result.email,
          name: result.name || pasted?.name || result.name,
          owner_tag: result.owner_tag || pasted?.personAssociated || result.owner_tag,
        };
        return next;
      });
      setDeployProgress((prev) => ({
        total: event.total ?? prev?.total ?? accounts.length,
        done: event.done ?? prev?.done ?? 0,
        phase: event.phase || prev?.phase || "azure",
        message: `${event.done ?? 0} of ${event.total ?? accounts.length} Azure stacks finished`,
        startedAt: prev?.startedAt ?? Date.now(),
      }));
      return;
    }
    if (event.type === "phase") {
      setDeployProgress((prev) => ({
        total: event.total ?? prev?.total ?? accounts.length,
        done: prev?.done ?? 0,
        phase: event.phase || prev?.phase || "portal",
        message: event.message || "Working…",
        startedAt: prev?.startedAt ?? Date.now(),
      }));
      return;
    }
    if (event.type === "done" && event.results) {
      const mapped = event.results.map((item, index) => ({
        ...item,
        ok: Boolean(item.ok),
        pending: false,
        email: item.email || accounts[index]?.accountHolder || item.email,
        name: item.name || accounts[index]?.name || item.name,
        owner_tag: item.owner_tag || accounts[index]?.personAssociated || item.owner_tag,
      }));
      setResults(mapped);
      const parts = deploySummary(mapped);
      setNotice(parts.join(" "));
      setDeployProgress((prev) => ({
        total: mapped.length,
        done: mapped.length,
        phase: "done",
        message: parts.join(" "),
        startedAt: prev?.startedAt ?? Date.now(),
      }));
      return;
    }
    if (event.type === "error") {
      setError(event.detail || "Deploy failed.");
    }
  }

  async function handleDeploy() {
    setError(null);
    setNotice(null);
    const accounts = commitSecrets();
    if (!accounts) return;
    const startedAt = Date.now();
    setNow(startedAt);
    setDeploying(true);
    setDeployProgress({
      total: accounts.length,
      done: 0,
      phase: "azure",
      message: `Starting ${accounts.length} parallel Azure deploy${accounts.length === 1 ? "" : "s"}…`,
      startedAt,
    });
    setResults(
      accounts.map((account) => ({
        ok: false,
        pending: true,
        name: account.name,
        email: account.accountHolder || null,
        subscription_id: account.subscriptionId,
        subscription_name: account.subscriptionName || null,
        owner_tag: account.personAssociated || null,
      }))
    );
    try {
      await streamKimiDeploy(
        {
          accounts: toKimiDeployPayload(accounts),
          jobs: parallelJobs(accounts.length),
          new_api_priority: newApiPriority,
          new_api_weight: newApiWeight,
        },
        (event) => applyDeployEvent(event, accounts)
      );
      invalidateAfterDeploy(queryClient);
      await stored.refetch();
      await inventory.refetch();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Deploy failed.";
      setError(message);
      setResults((prev) =>
        prev?.map((item) => (item.pending ? { ...item, pending: false, error: item.error || "Deploy stopped." } : item)) ?? null
      );
    } finally {
      setDeploying(false);
    }
  }

  function payloadForResult(item: KimiDeployResult, accounts = workingAccounts): Record<string, string> | null {
    if (accounts.length === 0) return null;
    const match =
      accounts.find((account) => account.subscriptionId && account.subscriptionId === item.subscription_id) ??
      accounts.find((account) => account.name === item.name);
    if (!match) return null;
    const row = toKimiDeployPayload([match])[0];
    if (item.account_name) row.account_name = item.account_name;
    if (item.resource_group) row.resource_group = item.resource_group;
    if (item.azure_openai_endpoint) row.azure_openai_endpoint = item.azure_openai_endpoint;
    if (item.deployment_name) row.deployment_name = item.deployment_name;
    return row;
  }

  async function handleAddNewApi(
    items: { item: KimiDeployResult; index: number; name?: string; priority?: number; weight?: number }[],
    bulk: boolean
  ) {
    setError(null);
    setNotice(null);
    const accounts = secretsForActions();
    if (!accounts) return;
    const payloads: { index: number; name: string; payload: Record<string, string> }[] = [];
    const missing: string[] = [];
    for (const { item, index, name, priority, weight } of items) {
      const payload = payloadForResult(item, accounts);
      if (!payload) missing.push(displayName(item));
      else {
        if (name?.trim()) payload.new_api_name = name.trim();
        const jsonPriority = payload.new_api_priority;
        const jsonWeight = payload.new_api_weight;
        if (priority != null && (jsonPriority == null || String(priority) !== jsonPriority)) {
          payload.new_api_priority = String(priority);
        }
        if (weight != null && (jsonWeight == null || String(weight) !== jsonWeight)) {
          payload.new_api_weight = String(weight);
        }
        payloads.push({ index, name: displayName(item), payload });
      }
    }
    if (missing.length > 0) {
      setError(`Need the matching account to add NewAPI for: ${missing.join(", ")}.`);
      return;
    }
    setAddingNewApiIndex(bulk ? "all" : payloads[0].index);
    try {
      const response = await addNewApi.mutateAsync({
        accounts: payloads.map((row) => row.payload),
        priority: newApiPriority,
        weight: newApiWeight,
      });
      setResults(
        displayed.map((item, index) => {
          const payloadIndex = payloads.findIndex((row) => row.index === index);
          if (payloadIndex < 0) return item;
          const row = response.results[payloadIndex];
          return row ? { ...item, ...newApiFields(row) } : item;
        })
      );
      const added = response.results.filter((row) => row.new_api_created).length;
      const already = response.results.filter((row) => row.new_api_present && !row.new_api_created).length;
      const failed = response.results.filter((row) => row.new_api_error);
      setNotice(
        [
          added ? (added === 1 ? "Added 1 NewAPI channel." : `Added ${added} NewAPI channels.`) : "",
          already ? (already === 1 ? "1 was already in NewAPI." : `${already} were already in NewAPI.`) : "",
        ]
          .filter(Boolean)
          .join(" ") || "NewAPI update finished."
      );
      if (failed.length > 0) {
        setError(failed.map((row) => `${row.name ?? "account"}: ${row.new_api_error}`).join("; "));
      }
      await inventory.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not add NewAPI channels."));
    } finally {
      setAddingNewApiIndex(null);
    }
  }

  async function handleSaveNewApi(
    item: KimiDeployResult,
    index: number,
    patch: { name: string; priority: number; weight: number }
  ) {
    setError(null);
    setNotice(null);
    const trimmed = patch.name.trim();
    if (!trimmed) {
      setError("Enter a NewAPI channel name.");
      return;
    }
    setRenamingNewApiIndex(index);
    try {
      const row = await renameNewApi.mutateAsync({
        name: trimmed,
        priority: patch.priority,
        weight: patch.weight,
        channel_id: item.new_api_channel_id,
        subscription_id: item.subscription_id || "",
        account_name: item.account_name || "",
        azure_openai_endpoint: item.azure_openai_endpoint || "",
      });
      setResults(
        displayed.map((current, currentIndex) =>
          currentIndex === index ? { ...current, ...newApiFields(row) } : current
        )
      );
      setNotice(
        `Updated NewAPI channel ${row.new_api_name || trimmed} (p${row.new_api_priority ?? patch.priority} · w${row.new_api_weight ?? patch.weight}).`
      );
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not update the NewAPI channel."));
    } finally {
      setRenamingNewApiIndex(null);
    }
  }

  async function handleSyncSheet(items: { item: KimiDeployResult; index: number }[], bulk: boolean) {
    setError(null);
    setNotice(null);
    if (!items.length) return;
    setSyncingSheetIndex(bulk ? "all" : items[0].index);
    try {
      const response = await sheetSync.mutateAsync({
        results: items.map(({ item }) => ({
          ...item,
          ok: true,
          email: item.email || holderFromPaste(item) || item.email,
        })),
      });
      const count = response.synced;
      setNotice(
        count === 1
          ? "Synced 1 row to the inventory sheet."
          : `Synced ${count} rows to the inventory sheet.`
      );
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not sync to the Google Sheet."));
    } finally {
      setSyncingSheetIndex(null);
    }
  }

  async function handleRefreshOne(item: KimiDeployResult, index: number) {
    setError(null);
    setNotice(null);
    const accounts = secretsForActions();
    if (!accounts) return;
    const payload = payloadForResult(item, accounts) || deployPayload[index];
    if (!payload) {
      setError(`Need the matching account to refresh ${displayName(item)}.`);
      return;
    }
    setRefreshingIndex(index);
    try {
      const response = await refreshInventory.mutateAsync({ accounts: [payload] });
      const row = response.results[0];
      if (!row) {
        setError("Could not refresh this account.");
        return;
      }
      setResults((prev) => {
        if (!prev) return prev;
        return prev.map((current, currentIndex) =>
          currentIndex === index ? { ...current, ...row, pending: false, removed: false } : current
        );
      });
      setNotice(`Refreshed ${displayName(row) || displayName(item)}.`);
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not refresh this account."));
    } finally {
      setRefreshingIndex(null);
    }
  }

  async function rotateAccounts(items: { item: KimiDeployResult; index: number }[], bulk: boolean) {
    setError(null);
    setNotice(null);
    const accounts = secretsForActions();
    if (!accounts) return;
    const payloads: { index: number; name: string; payload: Record<string, string> }[] = [];
    const missing: string[] = [];
    for (const { item, index } of items) {
      const payload = payloadForResult(item, accounts);
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
      const failed: string[] = [];
      payloads.forEach((entry, responseIndex) => {
        const row = response.results[responseIndex];
        if (!row?.ok) failed.push(row?.error ? `${entry.name}: ${row.error}` : entry.name);
      });
      const rotatedIndexes = new Set(
        payloads
          .filter((_, responseIndex) => response.results[responseIndex]?.ok)
          .map((entry) => entry.index)
      );
      if (rotatedIndexes.size > 0) {
        setLoadedAccounts((prev) =>
          prev.map((account, accountIndex) => {
            if (!rotatedIndexes.has(accountIndex) || account.clientSecret === undefined) return account;
            const { clientSecret: _dropped, ...rest } = account;
            return rest;
          })
        );
      }
      void stored.refetch();
      setNotice(
        failed.length > 0
          ? `Rotated ${response.ok_count}, failed ${response.fail_count}. The new secret is stored for later actions.`
          : `Rotated ${response.ok_count} secret${response.ok_count === 1 ? "" : "s"}. The new secret is stored for later actions.`
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
    const accounts = secretsForActions();
    if (!accounts) return;
    const items = displayed.map((item, index) => ({ item, index })).filter(({ item }) => item.ok && !item.removed);
    const payloads: Record<string, string>[] = [];
    const missing: string[] = [];
    for (const { item } of items) {
      const payload = payloadForResult(item, accounts);
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
    const accounts = secretsForActions();
    if (!accounts) return;
    const payload = payloadForResult(item, accounts);
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
    const accounts = secretsForActions();
    if (!accounts) return;
    const payloads: { index: number; name: string; payload: Record<string, string> }[] = [];
    const missing: string[] = [];
    for (const { item, index } of items) {
      const payload = payloadForResult(item, accounts);
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
    if (jsonLocked || busy) return;
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
          <p className="mt-1 text-sm text-gray-500">
            Paste or drop the secrets JSON, edit it, then click Deploy. Nothing is sent from this box until then.
            Reloading clears the JSON; already-deployed stacks stay listed below.
          </p>
        </div>
        <div className="flex w-full shrink-0 flex-col items-stretch gap-2 sm:w-auto sm:items-end">
          <Button onClick={() => void handleDeploy()} isLoading={deploying} disabled={busy || !canDeploy} className="sm:w-auto">
            {!deploying && <Rocket size={16} />}
            {deploying
              ? deployProgress
                ? `Deploying ${deployProgress.done}/${deployProgress.total}…`
                : `Deploying ${loadedAccounts.length || parsed.accounts.length}…`
              : (jsonLocked ? loadedAccounts.length : parsed.accounts.length)
                ? `Deploy ${jsonLocked ? loadedAccounts.length : parsed.accounts.length}`
                : "Deploy"}
          </Button>
          {newApiPool.data?.auth_expired && (
            <p className="text-[11px] text-red-400">O1 portal token expired — NewAPI create/update will fail until it is updated.</p>
          )}
        </div>
      </div>

      {status.isLoading && <Spinner />}
      {status.isError && <Banner tone="error">{statusApiError(status.error)}</Banner>}
      {status.data && !status.data.ready && <Banner tone="error">{status.data.message}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}
      {notice && <Banner tone="success">{notice}</Banner>}
      {deployProgress && deploying && (
        <DeployProgressBar
          progress={deployProgress}
          elapsedMs={Math.max(0, now - deployProgress.startedAt)}
          failed={(results ?? []).filter((item) => !item.pending && !item.ok).length}
        />
      )}

      <Card className="flex flex-col gap-3 !p-4 sm:!p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-200">Secrets</h2>
          <div className="flex items-center gap-2">
            <label className={`inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-surface-raised/80 px-3 py-1.5 text-xs font-medium text-gray-200 hover:border-accent/40 ${jsonLocked || busy ? "pointer-events-none opacity-50" : "cursor-pointer"}`}>
              <Upload size={13} />
              Open file
              <input type="file" accept="application/json,.json" className="hidden" onChange={onFile} disabled={busy || jsonLocked} />
            </label>
            {(jsonText.trim() || jsonLocked || results) && (
              <Button
                variant="ghost"
                className="px-2.5 py-1.5 text-xs"
                onClick={() => {
                  setJsonText("");
                  setJsonLocked(false);
                  setLoadedAccounts([]);
                  setResults(null);
                  setTestByIndex({});
                  setDeployProgress(null);
                  setNotice(null);
                  setError(null);
                }}
                disabled={busy}
              >
                Clear
              </Button>
            )}
          </div>
        </div>
        <label
          onDragEnter={(event) => {
            event.preventDefault();
            if (!jsonLocked && !busy) setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={jsonLocked ? undefined : onDrop}
          className={`block rounded-xl border border-dashed px-1 py-1 transition-colors ${
            dragging ? "border-accent/60 bg-accent/5" : "border-white/[0.08] bg-surface/60"
          }`}
        >
          <textarea
            value={jsonText}
            onChange={(e) => {
              if (jsonLocked) return;
              setJsonText(e.target.value);
              setError(null);
            }}
            rows={8}
            spellCheck={false}
            disabled={busy || jsonLocked}
            placeholder="Paste or drop a JSON array of service principals. It stays here until you click Deploy."
            className="w-full resize-y bg-transparent px-3 py-2.5 font-mono text-xs leading-relaxed text-gray-100 outline-none placeholder:text-gray-600 disabled:opacity-60"
          />
        </label>
        {parseError && !jsonLocked && <p className="text-xs text-red-400">{parseError}</p>}
        {!jsonLocked && pendingParse && (
          <p className="text-xs text-gray-500">
            {parsed.accounts.length === 1
              ? "1 account in this JSON. Click Deploy to start — nothing is fetched until then."
              : `${parsed.accounts.length} accounts in this JSON. Click Deploy to start — nothing is fetched until then.`}
          </p>
        )}
        {jsonLocked && (
          <p className="text-xs text-gray-500">
            JSON is locked for this run. Clear to edit again
            {deploying ? " after it finishes." : "."}
          </p>
        )}
        {deploying && (
          <p className="text-xs text-gray-500">
            {loadedAccounts.length} Azure stack{loadedAccounts.length === 1 ? "" : "s"} running in parallel
            {loadedAccounts.length > 1 ? ` (${parallelJobs(loadedAccounts.length)} workers)` : ""}. Keep this tab
            open — cards flip from deploying to done as each tenant finishes.
          </p>
        )}
      </Card>

      {showDeployed && (
        <section className="flex flex-col gap-3">
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-gray-200">Deployed</h2>
                <p className="text-xs text-gray-500">
                  {deploying && pendingCount > 0
                    ? `Deploying ${pendingCount} of ${displayed.length} in parallel…`
                    : pendingCount > 0
                      ? `Looking up ${pendingCount} subscription${pendingCount === 1 ? "" : "s"} in parallel…`
                      : `${liveResults.length} live${
                          displayed.filter((item) => !item.ok && item.error).length
                            ? ` · ${displayed.filter((item) => !item.ok && item.error).length} failed`
                            : ""
                        }${
                          displayed.filter((item) => item.new_api_present).length
                            ? ` · ${displayed.filter((item) => item.new_api_present).length} in NewAPI`
                            : ""
                        }${
                          displayed.filter((item) => item.removed).length
                            ? ` · ${displayed.filter((item) => item.removed).length} deleted`
                            : ""
                        }`}
                </p>
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-[11px] text-gray-500">
                  P
                  <input
                    type="number"
                    min={0}
                    max={10000}
                    value={newApiPriority}
                    onChange={(event) => setNewApiPriority(Number(event.target.value) || 0)}
                    disabled={busy}
                    className="w-16 rounded-lg border border-white/[0.08] bg-surface px-2 py-1.5 text-sm text-gray-100 outline-none focus:border-accent"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-gray-500">
                  W
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    value={newApiWeight}
                    onChange={(event) => setNewApiWeight(Math.max(1, Number(event.target.value) || 1))}
                    disabled={busy}
                    className="w-16 rounded-lg border border-white/[0.08] bg-surface px-2 py-1.5 text-sm text-gray-100 outline-none focus:border-accent"
                  />
                </label>
                {newApiPool.data?.next_name && !newApiPool.data.auth_expired && (
                  <p className="pb-2 text-[11px] text-gray-500">
                    Next <span className="font-mono text-gray-400">{newApiPool.data.next_name}</span>
                  </p>
                )}
              </div>
            </div>
            {testableResults.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                {liveResults.length > 0 && (
                  <Button
                    variant="secondary"
                    className="px-3 py-1.5 text-xs"
                    onClick={() =>
                      void handleSyncSheet(
                        displayed
                          .map((item, index) => ({ item, index }))
                          .filter(({ item }) => item.ok && !item.removed),
                        true
                      )
                    }
                    isLoading={sheetSync.isPending && syncingSheetIndex === "all"}
                    disabled={busy}
                    title={
                      sheetStatus.data?.configured === false
                        ? "Google Sheet is not configured"
                        : "Write live stacks to Sheet1"
                    }
                  >
                    Sync all to sheet
                  </Button>
                )}
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
                {liveResults.some((item) => !item.new_api_present) && (
                  <Button
                    variant="secondary"
                    className="px-3 py-1.5 text-xs"
                    onClick={() =>
                      void handleAddNewApi(
                        displayed
                          .map((item, index) => ({ item, index }))
                          .filter(({ item }) => item.ok && !item.removed && !item.new_api_present),
                        true
                      )
                    }
                    isLoading={addNewApi.isPending && addingNewApiIndex === "all"}
                    disabled={busy}
                  >
                    Add missing to NewAPI
                  </Button>
                )}
                {liveResults.length > 0 && (
                  <>
                    <Button
                      variant="ghost"
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
                      className="ml-auto px-3 py-1.5 text-xs"
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
            <div className="grid grid-cols-1 gap-3">
              {displayed.map((item, index) => (
                <DeployedKimiCard
                  key={`${item.subscription_id ?? item.name ?? "row"}-${index}`}
                  item={item}
                  email={item.email || holderFromPaste(item)}
                  busy={busy || refreshingIndex === index}
                  deploying={deploying && Boolean(item.pending)}
                  rotating={regenerate.isPending && rotatingIndex === index}
                  testing={testModel.isPending && (testingIndex === index || testingIndex === "all")}
                  deleting={undeploy.isPending && deletingIndex === index}
                  addingNewApi={addNewApi.isPending && (addingNewApiIndex === index || addingNewApiIndex === "all")}
                  renamingNewApi={renameNewApi.isPending && renamingNewApiIndex === index}
                  syncingSheet={sheetSync.isPending && (syncingSheetIndex === index || syncingSheetIndex === "all")}
                  refreshing={refreshingIndex === index}
                  nextNewApiName={newApiPool.data?.next_name}
                  defaultPriority={newApiPriority}
                  defaultWeight={newApiWeight}
                  jsonPriority={workingAccounts[index]?.priority}
                  jsonWeight={workingAccounts[index]?.weight}
                  testResult={testByIndex[index]}
                  onRotate={() => void rotateAccounts([{ item, index }], false)}
                  onTest={() => void handleTest([{ item, index }], false)}
                  onDelete={() => void handleDeleteOne(item, index)}
                  onAddNewApi={(opts) => void handleAddNewApi([{ item, index, ...opts }], false)}
                  onSaveNewApi={(patch) => void handleSaveNewApi(item, index, patch)}
                  onSyncSheet={() => void handleSyncSheet([{ item, index }], false)}
                  onRefresh={() => void handleRefreshOne(item, index)}
                />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function deploySummary(mapped: KimiDeployResult[]) {
  const created = mapped.filter((item) => item.ok).length;
  const added = mapped.filter((item) => item.new_api_created).length;
  const already = mapped.filter((item) => item.new_api_present && !item.new_api_created).length;
  const newApiFailed = mapped.filter((item) => item.new_api_error).length;
  const parts = [
    created === 1
      ? "Deployed 1 stack and added it to Accounts."
      : created
        ? `Deployed ${created} stacks and added them to Accounts.`
        : "Deploy finished. No stacks were created.",
  ];
  if (added) parts.push(added === 1 ? "Added 1 NewAPI channel." : `Added ${added} NewAPI channels.`);
  if (already) parts.push(already === 1 ? "1 was already in NewAPI." : `${already} were already in NewAPI.`);
  if (newApiFailed) parts.push(newApiFailed === 1 ? "1 NewAPI add failed." : `${newApiFailed} NewAPI adds failed.`);
  return parts;
}

function formatElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function deployPercent(progress: DeployRunProgress) {
  if (progress.phase === "done") return 100;
  const azure = progress.total > 0 ? progress.done / progress.total : 0;
  if (progress.phase === "azure") return Math.min(82, Math.max(3, Math.round(azure * 82)));
  if (progress.phase === "portal") return 88;
  if (progress.phase === "newapi") return 94;
  return Math.min(99, Math.max(3, Math.round(azure * 100)));
}

function phaseCaption(phase: string) {
  if (phase === "azure") return "Azure stacks";
  if (phase === "portal") return "Portal accounts";
  if (phase === "newapi") return "O1 NewAPI";
  if (phase === "done") return "Done";
  return phase;
}

function DeployProgressBar({
  progress,
  elapsedMs,
  failed,
}: {
  progress: DeployRunProgress;
  elapsedMs: number;
  failed: number;
}) {
  const percent = deployPercent(progress);
  const remaining = Math.max(0, progress.total - progress.done);
  return (
    <div className="overflow-hidden rounded-2xl border border-accent/25 bg-accent/[0.07] px-4 py-3 shadow-glow">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-100">{progress.message}</p>
          <p className="mt-0.5 text-xs text-gray-400">
            {phaseCaption(progress.phase)}
            {progress.phase === "azure"
              ? ` · ${progress.done}/${progress.total} finished${remaining ? ` · ${remaining} still running` : ""}`
              : ""}
            {failed ? ` · ${failed} failed` : ""}
          </p>
        </div>
        <div className="flex items-baseline gap-3 tabular-nums">
          <span className="text-lg font-semibold text-gray-100">{percent}%</span>
          <span className="text-xs text-gray-500">{formatElapsed(elapsedMs)}</span>
        </div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/[0.08]">
        <div
          className="relative h-full overflow-hidden rounded-full bg-accent-gradient transition-[width] duration-700 ease-out"
          style={{ width: `${percent}%` }}
        >
          <span className="absolute inset-y-0 left-0 w-2/3 animate-bar-shimmer bg-gradient-to-r from-transparent via-white/35 to-transparent" />
        </div>
      </div>
    </div>
  );
}

const KIMI_500K_PROXY_RE = /^kimi-k3-500k-proxy-(\d+)$/i;

function kimi500kIndex(name?: string | null) {
  const match = (name || "").trim().match(KIMI_500K_PROXY_RE);
  return match ? Number(match[1]) : -1;
}

function mergeNewApi(item: KimiDeployResult, pool?: KimiNewApiPool): KimiDeployResult {
  if (item.new_api_present || !pool?.channels?.length) return item;
  const hosts = new Set([resourceHost(item.account_name), resourceHost(item.azure_openai_endpoint)].filter(Boolean));
  const matches = pool.channels.filter(
    (row) => row.resource_name && hosts.has(row.resource_name) && KIMI_500K_PROXY_RE.test((row.name || "").trim())
  );
  if (matches.length === 0) return item;
  matches.sort((left, right) => {
    const enabledDelta = Number(right.status === 1) - Number(left.status === 1);
    if (enabledDelta !== 0) return enabledDelta;
    return kimi500kIndex(right.name) - kimi500kIndex(left.name);
  });
  const channel = matches[0];
  return {
    ...item,
    new_api_present: true,
    new_api_channel_id: channel.id,
    new_api_name: channel.name,
    new_api_status: channel.status,
    new_api_status_label: channel.status_label,
    new_api_priority: channel.priority,
    new_api_weight: channel.weight,
    new_api_error: null,
  };
}

function newApiFields(row: KimiDeployResult): Partial<KimiDeployResult> {
  return {
    new_api_present: row.new_api_present,
    new_api_created: row.new_api_created,
    new_api_channel_id: row.new_api_channel_id,
    new_api_name: row.new_api_name,
    new_api_status: row.new_api_status,
    new_api_status_label: row.new_api_status_label,
    new_api_priority: row.new_api_priority,
    new_api_weight: row.new_api_weight,
    new_api_error: row.new_api_error,
  };
}

function resourceHost(value?: string | null) {
  if (!value) return "";
  const raw = value.replace(/^https?:\/\//i, "").split("/")[0];
  return raw.split(".")[0].toLowerCase();
}

function storedToSecret(row: KimiStoredAccount): AzureDeploySecret {
  return {
    name: row.name || row.account_holder || "account",
    accountHolder: row.account_holder || undefined,
    tenantId: row.AZURE_TENANT_ID || "",
    clientId: row.AZURE_CLIENT_ID || "",
    subscriptionId: row.AZURE_SUBSCRIPTION_ID,
    subscriptionName: row.subscription_name || undefined,
    personAssociated: row.owner_tag || undefined,
  };
}

function displayName(item: KimiDeployResult) {
  return item.name || item.account_name || item.email || "account";
}

function resourceLabel(item: KimiDeployResult) {
  if (item.account_name && item.resource_group) return `${item.account_name} (${item.resource_group})`;
  if (item.account_name) return item.account_name;
  return item.name || "this deployment";
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
