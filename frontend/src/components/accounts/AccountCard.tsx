import { Check, Pencil, PlugZap, Power, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import DeploymentLinkPicker, {
  REGISTER_NEW,
  linkSelectedDeployments,
  matchRegisteredId,
} from "@/components/models/DeploymentLinkPicker";
import RegisterModelModal from "@/components/models/RegisterModelModal";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import {
  useDeleteAccount,
  useDiscoverAccountDeployments,
  useDiscoverAccountResources,
  useSetGatewayStatus,
  useSyncAccount,
  useTestAccount,
  useUpdateAccount,
} from "@/hooks/useAccounts";
import { useCreateModel, useModels } from "@/hooks/useModels";
import { useRegisteredModels } from "@/hooks/useRegisteredModels";
import { formatCurrency, formatDateTime, formatRelative } from "@/lib/format";
import { Account, Deployment, DiscoveredResource } from "@/types";

export default function AccountCard({ account }: { account: Account }) {
  const testAccount = useTestAccount();
  const syncAccount = useSyncAccount();
  const deleteAccount = useDeleteAccount();
  const updateAccount = useUpdateAccount();
  const discover = useDiscoverAccountResources();
  const discoverDeployments = useDiscoverAccountDeployments();
  const setGatewayStatus = useSetGatewayStatus();
  const createModel = useCreateModel();
  const { data: models } = useModels();
  const { data: registeredModels } = useRegisteredModels();
  const [testResult, setTestResult] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState(account.name);
  const [resources, setResources] = useState<DiscoveredResource[]>([]);
  const [selectedResourceId, setSelectedResourceId] = useState(account.resource_id);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [deploymentLinks, setDeploymentLinks] = useState<Record<string, string>>({});
  const [registeringDeployment, setRegisteringDeployment] = useState<Deployment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [pendingPortal, setPendingPortal] = useState<string | null>(null);

  const portals = useMemo(
    () => (account.new_api_gateway ?? "").split("+").filter((p): p is "O1" | "O2" => p === "O1" || p === "O2"),
    [account.new_api_gateway]
  );

  function portalStatus(portal: "O1" | "O2"): number | null {
    return portal === "O1" ? (account.new_api_status_o1 ?? null) : (account.new_api_status_o2 ?? null);
  }

  const alreadyLinked = useMemo(() => {
    const names = new Set<string>();
    for (const model of models ?? []) {
      if (model.provider_account_id === account.id) names.add(model.deployment_name);
    }
    return names;
  }, [models, account.id]);

  async function handleTest() {
    setError(null);
    setTestResult(null);
    try {
      const result = await testAccount.mutateAsync(account.id);
      setTestResult(result.status === "ok" ? "Connection OK" : result.detail ?? "Connection failed.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not test this account.");
    }
  }

  async function handleSync() {
    setError(null);
    try {
      const result = await syncAccount.mutateAsync(account.id);
      if (result.status === "error") {
        setError(result.error ?? `Sync failed for ${result.name ?? "this account"}.`);
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Sync failed.");
    }
  }

  async function handleGatewayToggle(enable: boolean, gateway?: "O1" | "O2") {
    setError(null);
    setPendingPortal(gateway ?? "ALL");
    try {
      await setGatewayStatus.mutateAsync({ accountId: account.id, enable, gateway });
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ??
          `Could not ${enable ? "enable" : "disable"} the ${gateway ?? "gateway"} channel.`
      );
    } finally {
      setPendingPortal(null);
    }
  }

  async function handleDelete() {
    setError(null);
    try {
      await deleteAccount.mutateAsync(account.id);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not delete this account.");
    }
  }

  function cancelEditing() {
    setIsEditing(false);
    setResources([]);
    setDeployments([]);
    setDeploymentLinks({});
    setRegisteringDeployment(null);
    setError(null);
    setModelsLoading(false);
  }

  async function startEditing() {
    setNameDraft(account.name);
    setSelectedResourceId(account.resource_id);
    setResources([]);
    setDeployments([]);
    setDeploymentLinks({});
    setRegisteringDeployment(null);
    setError(null);
    setModelsLoading(true);
    setIsEditing(true);
    try {
      const found = await discover.mutateAsync(account.id);
      setResources(found);
      const resourceId = found.some((resource) => resource.resource_id === account.resource_id)
        ? account.resource_id
        : found[0]?.resource_id ?? account.resource_id;
      setSelectedResourceId(resourceId);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not discover resources for this account.");
    }
  }

  useEffect(() => {
    if (!isEditing || !selectedResourceId || discover.isPending) return;
    let cancelled = false;
    setModelsLoading(true);
    setDeployments([]);
    setDeploymentLinks({});
    (async () => {
      try {
        const found = await discoverDeployments.mutateAsync({
          accountId: account.id,
          resourceId: selectedResourceId,
        });
        if (cancelled) return;
        setDeployments(found);
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.response?.data?.detail ?? "Could not load deployments for this resource.");
        }
      } finally {
        if (!cancelled) setModelsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isEditing, selectedResourceId, account.id, discover.isPending]);

  useEffect(() => {
    if (!isEditing || modelsLoading || deployments.length === 0) return;
    setDeploymentLinks((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const deployment of deployments) {
        if (alreadyLinked.has(deployment.name)) {
          if (deployment.name in next) {
            delete next[deployment.name];
            changed = true;
          }
          continue;
        }
        if (next[deployment.name]) continue;
        const matchedId = matchRegisteredId(registeredModels, deployment.model_name);
        if (matchedId) {
          next[deployment.name] = matchedId;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [isEditing, deployments, alreadyLinked, registeredModels, modelsLoading]);

  function handleResourceChange(resourceId: string) {
    setSelectedResourceId(resourceId);
    setError(null);
  }

  function toggleDeployment(deployment: Deployment) {
    if (alreadyLinked.has(deployment.name)) return;
    setDeploymentLinks((prev) => {
      const next = { ...prev };
      if (deployment.name in next) delete next[deployment.name];
      else next[deployment.name] = matchRegisteredId(registeredModels, deployment.model_name);
      return next;
    });
  }

  function handleRegisteredChange(deploymentName: string, value: string) {
    if (value === REGISTER_NEW) {
      const deployment = deployments.find((item) => item.name === deploymentName) ?? null;
      setRegisteringDeployment(deployment);
      return;
    }
    setDeploymentLinks((prev) => ({ ...prev, [deploymentName]: value }));
  }

  async function handleSave() {
    const trimmed = nameDraft.trim();
    if (!trimmed) {
      setError("Account name is required.");
      return;
    }
    const incomplete = Object.entries(deploymentLinks).filter(([, registeredId]) => !registeredId);
    if (incomplete.length > 0) {
      setError("Pick a registered model for each selected deployment, or uncheck it.");
      return;
    }
    const resource = resources.find((r) => r.resource_id === selectedResourceId);
    const payload: {
      name?: string;
      resource_id?: string;
      resource_group?: string;
      resource_name?: string;
      endpoint?: string;
      kind?: string;
      location?: string;
    } = {};
    if (trimmed !== account.name) payload.name = trimmed;
    if (resource && resource.resource_id !== account.resource_id) {
      payload.resource_id = resource.resource_id;
      payload.resource_group = resource.resource_group;
      payload.resource_name = resource.name;
      payload.endpoint = resource.endpoint;
      payload.kind = resource.kind;
      payload.location = resource.location;
    }
    const hasAccountChanges = Object.keys(payload).length > 0;
    const hasNewLinks = Object.keys(deploymentLinks).length > 0;
    if (!hasAccountChanges && !hasNewLinks) {
      cancelEditing();
      return;
    }
    setError(null);
    try {
      if (hasAccountChanges) {
        await updateAccount.mutateAsync({ accountId: account.id, ...payload });
      }
      if (hasNewLinks) {
        const failed = await linkSelectedDeployments(createModel.mutateAsync, account.id, deploymentLinks);
        if (failed.length > 0) {
          setError(`Account saved, but could not link ${failed.join(", ")}.`);
          return;
        }
      }
      cancelEditing();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not update this account.");
    }
  }

  const statusTone = account.last_sync_status === "success" ? "success" : account.last_sync_status === "error" ? "error" : "neutral";
  const isSaving = updateAccount.isPending || createModel.isPending;
  const isDiscovering = discover.isPending || modelsLoading || discoverDeployments.isPending;

  return (
    <>
      <Card className="flex flex-col gap-3">
        <div className="flex items-start justify-between gap-3">
          {isEditing ? (
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <div className="flex items-center gap-2">
                <Input
                  autoFocus
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") cancelEditing();
                  }}
                  className="py-1.5"
                />
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={isSaving || isDiscovering}
                  className="text-emerald-400 hover:text-emerald-300 disabled:opacity-60"
                  aria-label="Save account"
                >
                  {isSaving ? <Spinner className="h-4 w-4" /> : <Check size={16} />}
                </button>
                <button type="button" onClick={cancelEditing} className="text-gray-500 hover:text-gray-300" aria-label="Cancel">
                  <X size={16} />
                </button>
              </div>
              <div className="flex items-end gap-3">
                <div className="min-w-0 flex-1">
                  <Select
                    label="Resource"
                    value={selectedResourceId}
                    onChange={(e) => handleResourceChange(e.target.value)}
                    disabled={discover.isPending || resources.length === 0}
                  >
                    {resources.length === 0 ? (
                      <option value={account.resource_id}>
                        {account.resource_name} ({account.kind}, {account.location})
                      </option>
                    ) : (
                      resources.map((resource) => (
                        <option key={resource.resource_id} value={resource.resource_id}>
                          {resource.name} ({resource.kind}, {resource.location})
                        </option>
                      ))
                    )}
                  </Select>
                </div>
                {discover.isPending && <Spinner className="mb-1.5 shrink-0" />}
              </div>
              <DeploymentLinkPicker
                deployments={deployments}
                isLoading={discover.isPending || modelsLoading || discoverDeployments.isPending}
                alreadyLinked={alreadyLinked}
                registeredModels={registeredModels}
                links={deploymentLinks}
                onToggle={toggleDeployment}
                onRegisteredChange={handleRegisteredChange}
              />
              {error && <p className="break-words text-xs text-red-400">{error}</p>}
            </div>
          ) : (
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-1.5">
                <p className="truncate font-medium text-gray-100">{account.name}</p>
                <button type="button" onClick={startEditing} className="text-gray-600 hover:text-gray-300" aria-label="Edit account">
                  <Pencil size={12} />
                </button>
              </div>
              <p className="truncate text-xs text-gray-500">
                {account.resource_name} - {account.location}
              </p>
              {account.new_api_name && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge tone="info" className="max-w-full truncate">
                    {account.new_api_name}
                  </Badge>
                  {account.new_api_status != null && account.new_api_status !== 1 && (
                    <Badge tone="warning">gateway disabled</Badge>
                  )}
                  {portals.map((portal) => {
                    const status = portalStatus(portal);
                    const enabled = status === 1;
                    const unknown = status == null;
                    const isPending = setGatewayStatus.isPending && pendingPortal === portal;
                    return (
                      <button
                        key={portal}
                        type="button"
                        onClick={() => handleGatewayToggle(!enabled, portal)}
                        disabled={setGatewayStatus.isPending || unknown}
                        title={
                          unknown
                            ? `${portal} status unknown — wait for the next NewAPI sync`
                            : `${enabled ? "Disable" : "Enable"} the ${portal} channel for this account`
                        }
                        className={
                          unknown
                            ? "inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-200 disabled:opacity-70"
                            : enabled
                              ? "inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300 transition-colors hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-60"
                              : "inline-flex items-center gap-1 rounded-md border border-gray-600/50 bg-gray-500/10 px-1.5 py-0.5 text-[10px] font-medium text-gray-400 transition-colors hover:border-emerald-500/40 hover:bg-emerald-500/10 hover:text-emerald-300 disabled:opacity-60"
                        }
                      >
                        {isPending ? <Spinner className="h-3 w-3" /> : <Power size={10} />}
                        {portal} {unknown ? "?" : enabled ? "on" : "off"}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
          {!isEditing && (
            <Badge tone={statusTone} className="shrink-0">
              {account.last_sync_status ?? "not synced"}
            </Badge>
          )}
        </div>
        {!isEditing && (
          <>
            <p className="truncate text-xs text-gray-500">{account.endpoint}</p>
            <div className="flex flex-col gap-1">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate text-gray-500">Credits remaining</span>
                <div className="flex shrink-0 items-center gap-1.5">
                  <span className={`tabular-nums ${hasMonetaryCredits(account) ? "text-gray-200" : "text-gray-600"}`}>
                    {formatCreditBalance(account)}
                  </span>
                  <button
                    type="button"
                    onClick={handleSync}
                    disabled={syncAccount.isPending}
                    className="text-gray-600 hover:text-gray-300 disabled:opacity-50"
                    aria-label="Refresh credits"
                  >
                    {syncAccount.isPending ? <Spinner className="h-3 w-3" /> : <RefreshCw size={12} />}
                  </button>
                </div>
              </div>
              {creditOutstanding(account) != null && (
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-gray-500">Outstanding</span>
                  <span className="tabular-nums text-amber-300">{formatCurrency(creditOutstanding(account)!, account.credits_currency || "USD")}</span>
                </div>
              )}
              {account.new_api_cost_usd != null && (
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-gray-500">NewAPI spend</span>
                  <span className="tabular-nums text-violet-300">{formatCurrency(account.new_api_cost_usd, "USD")}</span>
                </div>
              )}
              {(account.new_api_weight != null || account.new_api_priority != null) && (
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-gray-500">Gateway weight / priority</span>
                  <span className="tabular-nums text-gray-200">
                    {account.new_api_weight ?? "—"} / {account.new_api_priority ?? "—"}
                  </span>
                </div>
              )}
            </div>
            <p className="truncate text-xs text-gray-500">
              Last synced {formatRelative(account.last_synced_at)}
              {account.last_synced_at && (
                <span className="hidden text-gray-600 sm:inline"> · {formatDateTime(account.last_synced_at)}</span>
              )}
            </p>
            {error && <p className="break-words text-xs text-red-400">{error}</p>}
            {account.last_sync_error && <p className="break-words text-xs text-red-400">{account.last_sync_error}</p>}
            {testResult && <p className="break-words text-xs text-accent">{testResult}</p>}
            <div className="mt-2 flex flex-wrap gap-2">
              <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={handleTest} isLoading={testAccount.isPending}>
                <PlugZap size={14} /> Test
              </Button>
              <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={handleSync} isLoading={syncAccount.isPending}>
                <RefreshCw size={14} /> Sync now
              </Button>
              <Button variant="danger" className="ml-auto px-2.5 py-1.5" onClick={handleDelete} isLoading={deleteAccount.isPending}>
                <Trash2 size={14} />
              </Button>
            </div>
          </>
        )}
      </Card>

      <RegisterModelModal
        isOpen={registeringDeployment !== null}
        onClose={() => setRegisteringDeployment(null)}
        initialName={registeringDeployment?.model_name}
        onRegistered={(id) => {
          if (!registeringDeployment) return;
          setDeploymentLinks((prev) => ({ ...prev, [registeringDeployment.name]: String(id) }));
        }}
      />
    </>
  );
}

function hasMonetaryCredits(account: Account): boolean {
  return account.credits_available && account.credits_unit === "currency";
}

function formatCreditBalance(account: Account): string {
  if (!hasMonetaryCredits(account) || account.credits_remaining == null) return "Unavailable";
  const currency = account.credits_currency || "USD";
  const remaining = formatCurrency(account.credits_remaining, currency);
  if (account.credits_limit != null) return `${remaining} / ${formatCurrency(account.credits_limit, currency)}`;
  return remaining;
}

function creditOutstanding(account: Account): number | null {
  if (!hasMonetaryCredits(account)) return null;
  if (account.credits_used != null) return Math.max(account.credits_used, 0);
  if (account.credits_limit != null && account.credits_remaining != null) {
    return Math.max(account.credits_limit - account.credits_remaining, 0);
  }
  return null;
}
