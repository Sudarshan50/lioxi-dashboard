import { useQueryClient } from "@tanstack/react-query";
import { Rocket, Search, Trash2, Upload } from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

import DeployedKimiCard from "@/components/deploy/DeployedKimiCard";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import {
  invalidateAfterDeploy,
  startKimiDeployJob,
  useKimiAddNewApi,
  useKimiCapacity,
  useKimiContentFilter,
  useKimiDeployDefaults,
  useKimiDeployJob,
  useKimiDeployStatus,
  useSaveKimiDeployDefaults,
  useKimiDropStored,
  useKimiInventory,
  useKimiNewApiPool,
  useKimiRenameNewApi,
  useKimiRegenerateKeys,
  useKimiRefreshInventory,
  useKimiSheetStatus,
  useKimiSheetSync,
  useKimiScaleQuota,
  useKimiStoredAccounts,
  useKimiTestModel,
  useKimiUndeploy,
} from "@/hooks/useKimiDeploy";
import { canonicalOwner } from "@/lib/ownerTag";
import { hasQuotaUpdate, nextQuotaTierLabel, quotaTierLabel, quotaTierNumber } from "@/lib/tpmTier";
import { groupLabel, resolveGroup } from "@/lib/joinGroup";
import { toastDismiss, toastError, toastSuccess } from "@/lib/toast";
import { AzureDeploySecret, parseAzureDeploySecretsArray, toKimiDeployPayload } from "@/lib/parseAzureCredentials";
import { KimiDeployResult, KimiNewApiPool, KimiStoredAccount, KimiTestResult } from "@/types";

const PARALLEL_JOBS = 12;
const PAGE_SIZE = 10;
const LEFTOVER_SECRETS_KEY = "kimi-deploy-secrets";

type DeploySort = "name" | "name-desc" | "newest" | "oldest" | "tpm-desc" | "tpm-asc" | "tier-desc" | "tier-asc" | "email";
type DateFilter = "all" | "today" | "7d" | "30d" | "unknown";

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
  const dropStored = useKimiDropStored();
  const testModel = useKimiTestModel();
  const addNewApi = useKimiAddNewApi();
  const applyContentFilter = useKimiContentFilter();
  const renameNewApi = useKimiRenameNewApi();
  const sheetStatus = useKimiSheetStatus();
  const sheetSync = useKimiSheetSync();
  const scaleQuota = useKimiScaleQuota();
  const refreshInventory = useKimiRefreshInventory();
  const [jsonText, setJsonText] = useState("");
  const [jsonLocked, setJsonLocked] = useState(false);
  const [loadedAccounts, setLoadedAccounts] = useState<AzureDeploySecret[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [results, setResults] = useState<KimiDeployResult[] | null>(null);
  const [deletingIndex, setDeletingIndex] = useState<number | "all" | null>(null);
  const [droppingIndex, setDroppingIndex] = useState<number | "all" | null>(null);
  const [hiddenLeftovers, setHiddenLeftovers] = useState<string[]>([]);
  const [rotatingIndex, setRotatingIndex] = useState<number | "all" | null>(null);
  const [testingIndex, setTestingIndex] = useState<number | "all" | null>(null);
  const [addingNewApiIndex, setAddingNewApiIndex] = useState<number | "all" | null>(null);
  const [renamingNewApiIndex, setRenamingNewApiIndex] = useState<number | null>(null);
  const [syncingSheetIndex, setSyncingSheetIndex] = useState<number | "all" | null>(null);
  const [refreshingIndex, setRefreshingIndex] = useState<number | null>(null);
  const [upgradingTpmIndex, setUpgradingTpmIndex] = useState<number | "all" | null>(null);
  const [deployQuery, setDeployQuery] = useState("");
  const [deploySort, setDeploySort] = useState<DeploySort>("newest");
  const [tierFilter, setTierFilter] = useState<"all" | number>("all");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [page, setPage] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [testByIndex, setTestByIndex] = useState<Record<number, KimiTestResult>>({});
  const [dragging, setDragging] = useState(false);
  const [newApiPriority, setNewApiPriority] = useState(10);
  const [newApiWeight, setNewApiWeight] = useState(1);
  const [deploying, setDeploying] = useState(false);
  const deployDefaults = useKimiDeployDefaults();
  const saveDeployDefaults = useSaveKimiDeployDefaults();
  const serverJob = useKimiDeployJob();
  const watchedJobId = useRef<string | null>(null);
  const seededDefaults = useRef(false);

  useEffect(() => {
    if (!deployDefaults.data || seededDefaults.current) return;
    seededDefaults.current = true;
    setNewApiPriority(deployDefaults.data.priority);
    setNewApiWeight(deployDefaults.data.weight);
  }, [deployDefaults.data]);

  useEffect(() => {
    const job = serverJob.data;
    if (job?.running && job.job_id) {
      setDeploying(true);
      watchedJobId.current = job.job_id;
    }
  }, [serverJob.data]);

  useEffect(() => {
    const job = serverJob.data;
    if (!job || job.running || !deploying) return;
    if (!watchedJobId.current || job.job_id !== watchedJobId.current) return;
    if (job.error) setError(job.error);
    if (job.results?.length) applyJobResults(job.results, loadedAccounts);
    setDeploying(false);
    invalidateAfterDeploy(queryClient);
    void stored.refetch();
  }, [deploying, loadedAccounts, queryClient, serverJob.data, stored]);

  useEffect(() => {
    if (error) toastError(error, { toastId: "deploy-err", persist: true });
    else toastDismiss("deploy-err");
  }, [error]);
  useEffect(() => {
    if (notice) toastSuccess(notice, "deploy-ok");
    else toastDismiss("deploy-ok");
  }, [notice]);
  useEffect(() => {
    if (status.isError) {
      toastError(statusApiError(status.error), { toastId: "deploy-status", persist: true });
    } else if (status.data && !status.data.ready && status.data.message) {
      toastError(status.data.message, { toastId: "deploy-status", persist: true });
    } else {
      toastDismiss("deploy-status");
    }
  }, [status.isError, status.error, status.data]);

  const parsed = useMemo(() => parseAzureDeploySecretsArray(jsonText), [jsonText]);
  const parseError = jsonText.trim() ? parsed.error : null;
  const storedSecrets = useMemo(
    () => (stored.data?.accounts ?? []).map(storedToSecret),
    [stored.data]
  );
  const sessionActive = jsonLocked && loadedAccounts.length > 0;
  const workingAccounts = sessionActive ? loadedAccounts : storedSecrets;
  const listedAccounts = useMemo(
    () =>
      workingAccounts
        .map((account, index) => ({ account, index }))
        .filter(({ account }) => matchesAccountSearch(account, deployQuery))
        .filter(({ account }) => matchesAccountDateFilter(account, dateFilter))
        .sort((left, right) => compareAccounts(left.account, right.account, deploySort)),
    [dateFilter, deployQuery, deploySort, workingAccounts]
  );
  const showDeployed = workingAccounts.length > 0 || Boolean(results);
  const newApiPool = useKimiNewApiPool(showDeployed);
  const resultCards = useMemo(() => {
    if (!results) return null;
    return results
      .map((item, index) => ({ item: mergeNewApi(item, newApiPool.data), index }))
      .filter(({ item }) => !item.removed)
      .filter(({ item }) => matchesDeploySearch(item, deployQuery, holderFromPaste(item)))
      .filter(({ item }) => matchesTierFilter(item, tierFilter))
      .filter(({ item }) => matchesDateFilter(item, dateFilter))
      .sort((left, right) => compareDeployCards(left.item, right.item, deploySort));
  }, [dateFilter, deployQuery, deploySort, newApiPool.data, results, tierFilter, workingAccounts]);
  const listTotal = resultCards?.length ?? listedAccounts.length;
  const pageCount = Math.max(1, Math.ceil(listTotal / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageStart = safePage * PAGE_SIZE;
  const pageEntries = useMemo(
    () => (showAll ? listedAccounts : listedAccounts.slice(pageStart, pageStart + PAGE_SIZE)),
    [listedAccounts, pageStart, showAll]
  );
  const pageAccounts = pageEntries.map((entry) => entry.account);
  const pagePayload = useMemo(
    () =>
      toKimiDeployPayload(pageAccounts).map((row) => {
        if (!row.AZURE_CLIENT_SECRET) return row;
        const { AZURE_CLIENT_SECRET: _secret, ...rest } = row;
        return rest;
      }),
    [pageAccounts]
  );
  const inventory = useKimiInventory(pagePayload, showDeployed && !deploying && !results);
  const capacity = useKimiCapacity(showDeployed && !deploying);
  const inventoryRows = useMemo(
    () =>
      pagePayload.map((account, index) => {
        const query = inventory.queries[index];
        const row = query?.data?.results[0];
        const pasted = pageAccounts[index];
        const sourceIndex = pageEntries[index]?.index ?? index;
        if (row) {
          return {
            item: {
              ...row,
              subscription_name:
                row.subscription_name || pasted?.subscriptionName || account.subscription_name || null,
              owner_tag: row.owner_tag || pasted?.personAssociated || null,
            },
            index: sourceIndex,
          };
        }
        return {
          item: {
            ok: false,
            name: pasted?.name || account.name,
            email: looksLikeEmail(pasted?.accountHolder) ? pasted?.accountHolder : null,
            subscription_id: pasted?.subscriptionId || account.AZURE_SUBSCRIPTION_ID,
            subscription_name: pasted?.subscriptionName || null,
            owner_tag: pasted?.personAssociated || null,
            pending: Boolean(query?.isFetching || query?.isPending),
            error: query?.isError ? "Could not list deployed resources for this account." : null,
          } satisfies KimiDeployResult,
          index: sourceIndex,
        };
      }),
    [inventory.queries, pageAccounts, pageEntries, pagePayload]
  );
  const displayedCards = useMemo(() => {
    if (resultCards) {
      return showAll ? resultCards : resultCards.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);
    }
    return inventoryRows
      .map(({ item, index }) => ({
        item: mergeNewApi(item, newApiPool.data),
        index,
      }))
      .sort((left, right) => compareDeployCards(left.item, right.item, deploySort));
  }, [deploySort, inventoryRows, newApiPool.data, resultCards, safePage, showAll]);
  const displayed = useMemo(() => displayedCards.map((row) => row.item), [displayedCards]);
  const busy =
    deploying ||
    regenerate.isPending ||
    undeploy.isPending ||
    dropStored.isPending ||
    testModel.isPending ||
    addNewApi.isPending ||
    applyContentFilter.isPending ||
    renameNewApi.isPending ||
    sheetSync.isPending ||
    scaleQuota.isPending;
  const liveResults = displayed.filter((item) => item.ok && !item.removed);
  const upgradableResults = displayedCards.filter(({ item }) => item.ok && !item.removed && hasQuotaUpdate(item));
  const leftoverResults = results
    ? []
    : displayedCards.filter(
        ({ item }) =>
          !item.pending && !item.removed && !item.ok && !hiddenLeftovers.includes(leftoverKey(item))
      );
  const listedTiers = useMemo(() => {
    const nums = new Set<number>();
    for (const item of displayed) {
      if (!item.ok || item.removed) continue;
      const n = quotaTierNumber(item.account_tier);
      if (n != null) nums.add(n);
    }
    return [...nums].sort((a, b) => a - b);
  }, [displayed]);
  const visibleCards = useMemo(
    () =>
      displayedCards.filter(({ item }) => {
        if (item.removed) return false;
        if (results) return true;
        if (!item.ok) return false;
        return (
          matchesTierFilter(item, tierFilter) &&
          matchesDeploySearch(item, deployQuery, holderFromPaste(item))
        );
      }),
    [deployQuery, displayedCards, results, tierFilter, workingAccounts]
  );
  const progressItems = resultCards ?? displayedCards;
  const pendingCount = progressItems.filter(({ item }) => item.pending).length;
  const testableResults = displayed.filter((item) => item.ok && !item.removed && !item.pending);
  const pageFrom = listTotal === 0 ? 0 : safePage * PAGE_SIZE + 1;
  const pageTo = Math.min(listTotal, (safePage + 1) * PAGE_SIZE);
  const deployedSummary = `${liveResults.length} live${
    leftoverResults.length ? ` · ${leftoverResults.length} leftover` : ""
  }${
    displayed.filter((item) => item.new_api_present).length
      ? ` · ${displayed.filter((item) => item.new_api_present).length} in NewAPI`
      : ""
  }${
    listTotal > PAGE_SIZE
      ? showAll
        ? ` · all ${listTotal}`
        : ` · ${pageFrom}–${pageTo} of ${listTotal}`
      : ""
  }`;

  useEffect(() => {
    setPage(0);
  }, [dateFilter, deployQuery, deploySort, tierFilter]);
  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);
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

  function applyJobResults(jobResults: KimiDeployResult[], accounts: AzureDeploySecret[]) {
    const mapped = jobResults.map((item, index) => ({
      ...item,
      ok: Boolean(item.ok),
      pending: false,
      email: item.email || accounts[index]?.accountHolder || item.email,
      name: item.name || accounts[index]?.name || item.name,
      owner_tag: item.owner_tag || accounts[index]?.personAssociated || item.owner_tag,
    }));
    setResults(mapped);
    setNotice(deploySummary(mapped).join(" "));
  }

  async function handleDeploy() {
    setError(null);
    setNotice(null);
    const accounts = commitSecrets();
    if (!accounts) return;
    setDeploying(true);
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
      watchedJobId.current = null;
      const job = await startKimiDeployJob({
        accounts: toKimiDeployPayload(accounts),
        jobs: parallelJobs(accounts.length),
        new_api_priority: newApiPriority,
        new_api_weight: newApiWeight,
      });
      watchedJobId.current = job.job_id ?? null;
      await queryClient.invalidateQueries({ queryKey: ["kimi-deploy-job"] });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Could not queue deploy.";
      setError(message);
      setResults((prev) =>
        prev?.map((item) => (item.pending ? { ...item, pending: false, error: item.error || "Deploy was not queued." } : item)) ??
          null
      );
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
      setResults((prev) =>
        prev
          ? prev.map((item, index) => {
              const payloadIndex = payloads.findIndex((row) => row.index === index);
              if (payloadIndex < 0) return item;
              const row = response.results[payloadIndex];
              return row ? { ...item, ...newApiFields(row) } : item;
            })
          : prev
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
      setResults((prev) =>
        prev ? prev.map((current, currentIndex) => (currentIndex === index ? { ...current, ...newApiFields(row) } : current)) : prev
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
    const payload = payloadForResult(item, accounts);
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

  async function handleUpgradeTpm(items: { item: KimiDeployResult; index: number }[], bulk: boolean) {
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
      setError(`Need the matching account to upgrade TPM/RPM for: ${missing.join(", ")}.`);
      return;
    }
    const preview = items[0]?.item;
    const confirmed = window.confirm(
      bulk
        ? `Upgrade TPM/RPM to Azure quota on ${payloads.length} stack${payloads.length === 1 ? "" : "s"}? Sheet TPM will update after a successful scale.`
        : `Upgrade TPM/RPM for ${payloads[0]?.name} from ${formatQuotaPair(preview?.tpm, preview?.rpm)} to ${formatQuotaPair(preview?.tpm_available, preview?.rpm_available)}?`
    );
    if (!confirmed) return;
    setUpgradingTpmIndex(bulk ? "all" : payloads[0].index);
    try {
      const response = await scaleQuota.mutateAsync({
        accounts: payloads.map((row) => row.payload),
        jobs: parallelJobs(payloads.length),
      });
      setResults((prev) =>
        prev
          ? prev.map((item, index) => {
              const payloadIndex = payloads.findIndex((row) => row.index === index);
              if (payloadIndex < 0) return item;
              const row = response.results[payloadIndex];
              if (!row?.ok) return item;
              return { ...item, ...quotaFields(row), pending: false, error: null };
            })
          : prev
      );
      const failed = response.results.filter((row) => !row.ok);
      setNotice(
        failed.length > 0
          ? `Upgraded TPM/RPM on ${response.ok_count}, failed ${response.fail_count}. Sheet rows updated for the successes.`
          : response.ok_count === 1
            ? "Upgraded TPM/RPM and synced the new values to the sheet."
            : `Upgraded TPM/RPM on ${response.ok_count} stacks and synced the new values to the sheet.`
      );
      if (failed.length > 0) {
        setError(failed.map((row) => `${row.name ?? "account"}: ${row.error}`).join("; "));
      }
      await inventory.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not upgrade TPM/RPM."));
    } finally {
      setUpgradingTpmIndex(null);
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
    const items = displayedCards.filter(({ item }) => item.ok && !item.removed);
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
      setResults((prev) =>
        prev
          ? prev.map((item) => {
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
          : prev
      );
      await stored.refetch();
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
      setResults((prev) =>
        prev
          ? prev.map((entry, entryIndex) =>
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
          : prev
      );
      await stored.refetch();
      await inventory.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not delete this resource."));
    } finally {
      setDeletingIndex(null);
    }
  }

  async function handleDropLeftover(items: { item: KimiDeployResult; index: number }[], bulk: boolean) {
    setError(null);
    setNotice(null);
    const ids = [
      ...new Set(
        items
          .map(({ item }) => (item.subscription_id || "").trim())
          .filter(Boolean)
      ),
    ];
    if (ids.length === 0) {
      setError("Those leftover identities have no subscription id to remove.");
      return;
    }
    const confirmed = window.confirm(
      bulk
        ? `Remove ${ids.length} leftover identit${ids.length === 1 ? "y" : "ies"} with no live FW-Kimi-K3?`
        : `Remove ${displayName(items[0].item)} from Deploy K3? There is no live FW-Kimi-K3 on this subscription.`
    );
    if (!confirmed) return;
    setDroppingIndex(bulk ? "all" : items[0].index);
    try {
      const response = await dropStored.mutateAsync({ subscription_ids: ids });
      setHiddenLeftovers((prev) => [...new Set([...prev, ...ids.map((id) => id.toLowerCase()), ...items.map(({ item }) => leftoverKey(item))])]);
      setNotice(
        response.dropped === 1
          ? "Removed 1 leftover identity from Deploy K3."
          : `Removed ${response.dropped} leftover identities from Deploy K3.`
      );
      await stored.refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not remove leftover identities."));
    } finally {
      setDroppingIndex(null);
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

  async function handleApplyContentFilter() {
    const targets = displayedCards.filter(({ item }) => item.ok && !item.removed);
    const accounts = secretsForActions();
    if (!accounts) return;
    const payloads: Record<string, string>[] = [];
    const missing: string[] = [];
    for (const { item } of targets) {
      const payload = payloadForResult(item, accounts);
      if (!payload) missing.push(displayName(item));
      else payloads.push(payload);
    }
    if (missing.length > 0) {
      setError(`Need the matching elevated identity for: ${missing.join(", ")}.`);
      return;
    }
    if (payloads.length === 0) {
      setError("No elevated Deploy K3 stacks listed. Nothing to attach the content filter to.");
      return;
    }
    if (
      !window.confirm(
        `Apply the Lioxi custom content filter to ${payloads.length} elevated K3 stack${payloads.length === 1 ? "" : "s"} listed here? High on hate/sexual/violence/self-harm; jailbreak and protected material off.`
      )
    ) {
      return;
    }
    setError(null);
    setNotice(null);
    try {
      const response = await applyContentFilter.mutateAsync({
        accounts: payloads,
        jobs: parallelJobs(payloads.length),
      });
      const failed = response.results.filter((row) => !row.ok);
      setNotice(
        failed.length > 0
          ? `Content filter applied on ${response.ok_count}, failed ${response.fail_count}.`
          : `Content filter LioxiCustom applied on ${response.ok_count} stack${response.ok_count === 1 ? "" : "s"}.`
      );
      if (failed.length > 0) {
        setError(failed.map((row) => `${row.name ?? "account"}: ${row.error}`).join("; "));
      }
    } catch (err: any) {
      setError(apiErrorMessage(err, "Could not apply the content filter."));
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
              ? "Running on server…"
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
      {deploying && (
        <p className="rounded-xl border border-accent/25 bg-accent/[0.07] px-4 py-3 text-sm text-indigo-100">
          Deploy is running on the server
          {serverJob.data?.total ? ` (${serverJob.data.total} account${serverJob.data.total === 1 ? "" : "s"})` : ""}.
          You can leave this page — it will keep going.
        </p>
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
            Job submitted. The server is deploying
            {serverJob.data?.total ? ` ${serverJob.data.total} stack${serverJob.data.total === 1 ? "" : "s"}` : ""}.
            Closing this tab does not stop it.
          </p>
        )}
      </Card>

      {showDeployed && (
        <section className="flex flex-col gap-3">
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div className="min-w-0 sm:max-w-md sm:flex-1">
                <h2 className="text-sm font-semibold text-gray-200">Deployed</h2>
                {capacity.data && capacity.data.accounts > 0 ? (
                  <p className="text-xs text-gray-500">
                    {pendingCount > 0 ? null : `${deployedSummary} · `}
                    <span className="font-semibold text-amber-300">
                      {formatQuotaTokens(capacity.data.tpm)} TPM
                    </span>
                    {" · "}
                    <span className="font-semibold text-amber-300">
                      {formatQuotaTokens(capacity.data.rpm)} RPM
                    </span>
                  </p>
                ) : pendingCount === 0 ? (
                  <p className="text-xs text-gray-500">{deployedSummary}</p>
                ) : null}
                {pendingCount > 0 ? (
                  <LookupProgress done={progressItems.length - pendingCount} total={progressItems.length} />
                ) : null}
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
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  disabled={busy || saveDeployDefaults.isPending}
                  isLoading={saveDeployDefaults.isPending}
                  onClick={() => {
                    saveDeployDefaults.mutate(
                      { priority: newApiPriority, weight: newApiWeight },
                      {
                        onSuccess: () =>
                          toastSuccess(`Saved default priority ${newApiPriority} · weight ${newApiWeight}.`),
                        onError: (exc: unknown) => {
                          const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
                          toastError(typeof detail === "string" ? detail : "Could not save deploy defaults.");
                        },
                      }
                    );
                  }}
                >
                  Save defaults
                </Button>
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
                        displayedCards.filter(({ item }) => item.ok && !item.removed),
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
                {upgradableResults.length > 0 && (
                  <Button
                    variant="secondary"
                    className="px-3 py-1.5 text-xs"
                    onClick={() => void handleUpgradeTpm(upgradableResults, true)}
                    isLoading={scaleQuota.isPending && upgradingTpmIndex === "all"}
                    disabled={busy}
                    title="Scale FW-Kimi-K3 to the higher Azure TPM/RPM quota"
                  >
                    Upgrade TPM ({upgradableResults.length})
                  </Button>
                )}
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  onClick={() =>
                    void handleTest(
                      displayedCards.filter(({ item }) => item.ok && !item.removed && !item.pending),
                      true
                    )
                  }
                  isLoading={testModel.isPending && testingIndex === "all"}
                  disabled={busy}
                >
                  Test all
                </Button>
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  onClick={() => void handleApplyContentFilter()}
                  isLoading={applyContentFilter.isPending}
                  disabled={busy || liveResults.length === 0}
                  title="Attach the Lioxi custom content filter to the elevated Deploy K3 stacks listed here"
                >
                  Apply content filter
                </Button>
                {liveResults.some((item) => !item.new_api_present) && (
                  <Button
                    variant="secondary"
                    className="px-3 py-1.5 text-xs"
                    onClick={() =>
                      void handleAddNewApi(
                        displayedCards.filter(({ item }) => item.ok && !item.removed && !item.new_api_present),
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
                          displayedCards.filter(({ item }) => item.ok && !item.removed),
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
          {(liveResults.length > 0 || visibleCards.length > 0 || deployQuery.trim() || tierFilter !== "all" || dateFilter !== "all") && (
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <label className="relative min-w-0 flex-1">
                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                <input
                  value={deployQuery}
                  onChange={(event) => setDeployQuery(event.target.value)}
                  placeholder="Search name, email, endpoint, NewAPI, Tier 1…"
                  className="w-full rounded-lg border border-white/[0.08] bg-surface py-2 pl-9 pr-3 text-sm text-gray-100 outline-none placeholder:text-gray-600 focus:border-accent"
                />
              </label>
              <select
                value={tierFilter === "all" ? "all" : String(tierFilter)}
                onChange={(event) => {
                  const value = event.target.value;
                  setTierFilter(value === "all" ? "all" : Number(value));
                }}
                className="rounded-lg border border-white/[0.08] bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent"
                title="Filter by Azure quota tier"
              >
                <option value="all">All quota tiers</option>
                {listedTiers.map((tier) => (
                  <option key={tier} value={tier}>
                    {tier === 0 ? "Free Tier" : `Tier ${tier}`}
                  </option>
                ))}
              </select>
              <select
                value={dateFilter}
                onChange={(event) => setDateFilter(event.target.value as DateFilter)}
                className="rounded-lg border border-white/[0.08] bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent"
                title="Filter by when the stack was deployed"
              >
                <option value="all">All deploy times</option>
                <option value="today">Deployed today</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="unknown">Unknown time</option>
              </select>
              <select
                value={deploySort}
                onChange={(event) => setDeploySort(event.target.value as DeploySort)}
                className="rounded-lg border border-white/[0.08] bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent"
                title="Sort deployed cards. TPM/RPM upgrades stay on top."
              >
                <option value="name">Name A–Z</option>
                <option value="name-desc">Name Z–A</option>
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="tier-asc">Quota tier low–high</option>
                <option value="tier-desc">Quota tier high–low</option>
                <option value="tpm-desc">TPM high–low</option>
                <option value="tpm-asc">TPM low–high</option>
                <option value="email">Email</option>
              </select>
            </div>
          )}
          {visibleCards.length === 0 ? (
            <p className="text-sm text-gray-500">
              {deployQuery.trim() || tierFilter !== "all" || dateFilter !== "all"
                ? "No deployed stacks match this search."
                : leftoverResults.length > 0
                  ? "No live FW-Kimi-K3 on these subscriptions."
                  : "No FW-Kimi-K3 found on these subscriptions yet."}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3">
              {visibleCards.map(({ item, index }) => (
                <DeployedKimiCard
                  key={`${item.subscription_id ?? item.name ?? "row"}-${index}`}
                  item={item}
                  email={cardEmail(item, holderFromPaste(item))}
                  busy={busy || refreshingIndex === index}
                  deploying={deploying && Boolean(item.pending)}
                  rotating={regenerate.isPending && rotatingIndex === index}
                  testing={testModel.isPending && (testingIndex === index || testingIndex === "all")}
                  deleting={undeploy.isPending && deletingIndex === index}
                  addingNewApi={addNewApi.isPending && (addingNewApiIndex === index || addingNewApiIndex === "all")}
                  renamingNewApi={renameNewApi.isPending && renamingNewApiIndex === index}
                  syncingSheet={sheetSync.isPending && (syncingSheetIndex === index || syncingSheetIndex === "all")}
                  refreshing={refreshingIndex === index}
                  upgradingTpm={scaleQuota.isPending && (upgradingTpmIndex === index || upgradingTpmIndex === "all")}
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
                  onUpgradeTpm={() => void handleUpgradeTpm([{ item, index }], false)}
                />
              ))}
            </div>
          )}
          {listTotal > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-gray-500">
                {showAll ? `All ${listTotal}` : `${pageFrom}–${pageTo} of ${listTotal}`}
              </p>
              <div className="flex items-center gap-2">
                {!showAll && (
                  <>
                    <Button
                      variant="secondary"
                      className="px-3 py-1.5 text-xs"
                      disabled={safePage <= 0}
                      onClick={() => setPage(safePage - 1)}
                    >
                      Prev
                    </Button>
                    <span className="tabular-nums text-xs text-gray-400">
                      {safePage + 1} / {pageCount}
                    </span>
                    <Button
                      variant="secondary"
                      className="px-3 py-1.5 text-xs"
                      disabled={safePage >= pageCount - 1}
                      onClick={() => setPage(safePage + 1)}
                    >
                      Next
                    </Button>
                  </>
                )}
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  onClick={() => {
                    setShowAll((current) => !current);
                    setPage(0);
                  }}
                >
                  {showAll ? "Show 10" : "Show all"}
                </Button>
              </div>
            </div>
          )}
          {leftoverResults.length > 0 && (
            <div className="rounded-2xl border border-white/[0.08] bg-surface/60 px-4 py-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium text-gray-200">Leftover identities</p>
                <p className="text-xs text-gray-500">No live FW-Kimi-K3. Remove them from Deploy K3.</p>
                <Button
                  variant="danger"
                  className="ml-auto px-3 py-1.5 text-xs"
                  onClick={() => void handleDropLeftover(leftoverResults, true)}
                  isLoading={dropStored.isPending && droppingIndex === "all"}
                  disabled={busy}
                >
                  Delete leftover
                </Button>
              </div>
              <ul className="flex flex-col gap-2">
                {leftoverResults.map(({ item, index }) => (
                  <li
                    key={`${item.subscription_id ?? item.name ?? "leftover"}-${index}`}
                    className="flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-gray-100">{displayName(item)}</p>
                      <p className="truncate font-mono text-[11px] text-gray-500">
                        {cardEmail(item, holderFromPaste(item)) || "—"}
                        {item.subscription_id ? ` · ${item.subscription_id}` : ""}
                      </p>
                    </div>
                    <Button
                      variant="danger"
                      className="px-2.5 py-1 text-xs"
                      onClick={() => void handleDropLeftover([{ item, index }], false)}
                      isLoading={dropStored.isPending && droppingIndex === index}
                      disabled={busy}
                      title="Remove this stored identity from Deploy K3"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Delete
                    </Button>
                  </li>
                ))}
              </ul>
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

function quotaFields(row: KimiDeployResult): Partial<KimiDeployResult> {
  return {
    tpm: row.tpm,
    rpm: row.rpm,
    capacity: row.capacity,
    quota_limit: row.quota_limit,
    tpm_available: row.tpm_available,
    rpm_available: row.rpm_available,
    tpm_upgrade_available: row.tpm_upgrade_available,
    quota_id: row.quota_id,
    account_tier: row.account_tier,
    account_tier_available: row.account_tier_available,
    quota_tier_upgrade_available: row.quota_tier_upgrade_available,
    sku: row.sku,
    error: row.error,
  };
}

function formatQuotaTokens(value?: number | null) {
  if (value == null) return "—";
  if (value >= 1_000_000) return `${value / 1_000_000}M`;
  if (value >= 1_000) return `${value / 1_000}k`;
  return String(value);
}

function formatQuotaPair(tpm?: number | null, rpm?: number | null) {
  return `${formatQuotaTokens(tpm)} / ${rpm ?? "—"}`;
}

function resourceHost(value?: string | null) {
  if (!value) return "";
  const raw = value.replace(/^https?:\/\//i, "").split("/")[0];
  return raw.split(".")[0].toLowerCase();
}

function looksLikeEmail(value?: string | null) {
  const text = (value || "").trim();
  return text.includes("@") && !text.includes(" ");
}

function cardEmail(item: KimiDeployResult, fallback?: string | null): string {
  if (looksLikeEmail(item.email)) return String(item.email).trim();
  if (looksLikeEmail(fallback)) return String(fallback).trim();
  return "";
}

function storedToSecret(row: KimiStoredAccount): AzureDeploySecret {
  const email = looksLikeEmail(row.account_holder) ? String(row.account_holder).trim() : undefined;
  return {
    name: row.name || "account",
    accountHolder: email,
    tenantId: row.AZURE_TENANT_ID || "",
    clientId: row.AZURE_CLIENT_ID || "",
    subscriptionId: row.AZURE_SUBSCRIPTION_ID,
    subscriptionName: row.subscription_name || undefined,
    personAssociated: row.owner_tag || undefined,
    createdAt: row.created_at || undefined,
  };
}

function matchesAccountSearch(account: AzureDeploySecret, query: string) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const hay = [
    account.name,
    account.accountHolder,
    account.subscriptionName,
    account.subscriptionId,
    account.personAssociated,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(needle);
}

function accountTimeMs(account: AzureDeploySecret) {
  if (!account.createdAt) return 0;
  const ms = new Date(account.createdAt).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

function matchesAccountDateFilter(account: AzureDeploySecret, filter: DateFilter) {
  if (filter === "all") return true;
  const ms = account.createdAt ? new Date(account.createdAt).getTime() : NaN;
  if (!Number.isFinite(ms)) return filter === "unknown";
  if (filter === "unknown") return false;
  const now = new Date();
  if (filter === "today") {
    return ms >= new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  }
  const days = filter === "7d" ? 7 : 30;
  return ms >= now.getTime() - days * 86400000;
}

function compareAccounts(left: AzureDeploySecret, right: AzureDeploySecret, sort: DeploySort) {
  const name = (value?: string | null) => (value || "").trim().toLowerCase();
  switch (sort) {
    case "name":
      return name(left.name).localeCompare(name(right.name));
    case "name-desc":
      return name(right.name).localeCompare(name(left.name));
    case "oldest":
      return accountTimeMs(left) - accountTimeMs(right);
    case "email":
      return name(left.accountHolder).localeCompare(name(right.accountHolder));
    default:
      return accountTimeMs(right) - accountTimeMs(left);
  }
}

function LookupProgress({ done, total }: { done: number; total: number }) {
  const safeTotal = Math.max(total, 1);
  const percent = Math.round((Math.max(0, done) / safeTotal) * 100);
  return (
    <div className="mt-2 flex items-center gap-3">
      <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/[0.07]">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-accent-gradient shadow-glow-sm transition-[width] duration-500 ease-out"
          style={{ width: `${percent}%` }}
        />
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="h-full w-1/3 animate-bar-shimmer bg-gradient-to-r from-transparent via-white/30 to-transparent" />
        </div>
      </div>
      <span className="shrink-0 tabular-nums text-[11px] text-gray-400">
        {done}/{total}
      </span>
    </div>
  );
}

function leftoverKey(item: KimiDeployResult) {
  return (item.subscription_id || item.name || "").trim().toLowerCase();
}

function matchesDeploySearch(item: KimiDeployResult, query: string, extraEmail?: string | null) {
  const needle = query.trim().toLowerCase().replace(/\s+/g, " ");
  if (!needle) return true;
  const compact = needle.replace(/\s+/g, "");
  const tier = quotaTierLabel(item) || "";
  const next = nextQuotaTierLabel(item) || "";
  const hay = [
    item.name,
    item.account_name,
    item.email,
    extraEmail,
    item.subscription_name,
    item.subscription_id,
    item.owner_tag,
    groupLabel(resolveGroup(null, item.new_api_name)),
    item.new_api_name,
    item.azure_openai_endpoint,
    item.resource_group,
    tier,
    next,
    tier.replace(/\s+/g, ""),
    next.replace(/\s+/g, ""),
    hasQuotaUpdate(item) ? "newupdate update" : "",
    item.tpm != null ? String(item.tpm) : "",
    item.tpm != null && item.tpm >= 1000 ? `${item.tpm / 1000}k` : "",
    item.deployed_at || "",
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(needle) || hay.replace(/\s+/g, "").includes(compact);
}

function matchesTierFilter(item: KimiDeployResult, filter: "all" | number) {
  if (filter === "all") return true;
  return quotaTierNumber(item.account_tier) === filter;
}

function deployTimeMs(item: KimiDeployResult): number | null {
  if (!item.deployed_at) return null;
  const ms = new Date(item.deployed_at).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function matchesDateFilter(item: KimiDeployResult, filter: DateFilter) {
  if (filter === "all") return true;
  const ms = deployTimeMs(item);
  if (ms == null) return filter === "unknown";
  if (filter === "unknown") return false;
  const now = new Date();
  if (filter === "today") {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    return ms >= start;
  }
  const days = filter === "7d" ? 7 : 30;
  return ms >= now.getTime() - days * 86400000;
}

function compareDeployCards(left: KimiDeployResult, right: KimiDeployResult, sort: DeploySort) {
  const leftUp = hasQuotaUpdate(left) ? 1 : 0;
  const rightUp = hasQuotaUpdate(right) ? 1 : 0;
  if (leftUp !== rightUp) return rightUp - leftUp;
  const name = (value?: string | null) => (value || "").trim().toLowerCase();
  const leftTime = deployTimeMs(left) ?? 0;
  const rightTime = deployTimeMs(right) ?? 0;
  switch (sort) {
    case "name-desc":
      return name(right.name || right.account_name).localeCompare(name(left.name || left.account_name));
    case "newest":
      return rightTime - leftTime;
    case "oldest":
      return leftTime - rightTime;
    case "email":
      return name(left.email).localeCompare(name(right.email));
    case "tpm-desc":
      return (right.tpm || 0) - (left.tpm || 0);
    case "tpm-asc":
      return (left.tpm || 0) - (right.tpm || 0);
    case "tier-desc":
      return (quotaTierNumber(right.account_tier) ?? -1) - (quotaTierNumber(left.account_tier) ?? -1);
    case "tier-asc":
      return (quotaTierNumber(left.account_tier) ?? -1) - (quotaTierNumber(right.account_tier) ?? -1);
    default:
      return name(left.name || left.account_name).localeCompare(name(right.name || right.account_name));
  }
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
