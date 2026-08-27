import { RefreshCw } from "lucide-react";
import { useState } from "react";

import DeploymentLinkPicker, {
  REGISTER_NEW,
  linkSelectedDeployments,
  matchRegisteredId,
} from "@/components/models/DeploymentLinkPicker";
import RegisterModelModal from "@/components/models/RegisterModelModal";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import { useAccounts, useCreateAccount, useDiscoverDeployments, useDiscoverResources } from "@/hooks/useAccounts";
import { useCreateModel } from "@/hooks/useModels";
import { useRegisteredModels } from "@/hooks/useRegisteredModels";
import { AzureAccountImport, parseAzureAccountImportArray } from "@/lib/parseAzureCredentials";
import { allocateUniqueName } from "@/lib/uniqueName";
import { parseCreditGrant } from "@/components/accounts/ManualResourceFields";
import OwnerTagField from "@/components/accounts/OwnerTagField";
import { Deployment, DiscoveredResource } from "@/types";

interface BulkUploadAccountsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type Step = "json" | "configure";

interface ImportRow {
  id: string;
  name: string;
  tenantId: string;
  clientId: string;
  clientSecret: string;
  subscriptionId: string;
  included: boolean;
  resources: DiscoveredResource[];
  selectedResourceId: string;
  deployments: Deployment[];
  deploymentLinks: Record<string, string>;
  resourcesLoading: boolean;
  deploymentsLoading: boolean;
  error: string | null;
  resourceName: string;
  location: string;
  creditsLimit: string;
  deploymentName: string;
  registeredId: string;
  ownerTag: string;
}

export default function BulkUploadAccountsModal({ isOpen, onClose }: BulkUploadAccountsModalProps) {
  const { data: existingAccounts } = useAccounts();
  const { data: registeredModels } = useRegisteredModels();
  const discover = useDiscoverResources();
  const discoverDeployments = useDiscoverDeployments();
  const createAccount = useCreateAccount();
  const createModel = useCreateModel();

  const [step, setStep] = useState<Step>("json");
  const [jsonText, setJsonText] = useState("");
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveProgress, setSaveProgress] = useState<{ current: number; total: number } | null>(null);
  const [registering, setRegistering] = useState<{ rowId: string; deployment: Deployment } | null>(null);

  function resetAndClose() {
    setStep("json");
    setJsonText("");
    setRows([]);
    setError(null);
    setIsDiscovering(false);
    setIsSaving(false);
    setSaveProgress(null);
    setRegistering(null);
    onClose();
  }

  function patchRow(id: string, patch: Partial<ImportRow> | ((row: ImportRow) => Partial<ImportRow>)) {
    setRows((prev) =>
      prev.map((row) => {
        if (row.id !== id) return row;
        const next = typeof patch === "function" ? patch(row) : patch;
        return { ...row, ...next };
      })
    );
  }

  async function loadDeployments(row: Pick<ImportRow, "id" | "tenantId" | "clientId" | "clientSecret" | "subscriptionId">, resourceId: string) {
    patchRow(row.id, { deploymentsLoading: true, deployments: [], deploymentLinks: {}, error: null });
    try {
      const found = await discoverDeployments.mutateAsync({
        tenant_id: row.tenantId,
        client_id: row.clientId,
        client_secret: row.clientSecret,
        subscription_id: row.subscriptionId,
        resource_id: resourceId,
      });
      const links: Record<string, string> = {};
      for (const deployment of found) {
        const matchedId = matchRegisteredId(registeredModels, deployment.model_name);
        if (matchedId) links[deployment.name] = matchedId;
      }
      patchRow(row.id, { deployments: found, deploymentLinks: links, deploymentsLoading: false });
    } catch (err: any) {
      patchRow(row.id, {
        deploymentsLoading: false,
        error: err?.response?.data?.detail ?? "Could not load deployments for this resource.",
      });
    }
  }

  async function handleNext() {
    setError(null);
    const parsed = parseAzureAccountImportArray(jsonText);
    if (parsed.error) {
      setError(parsed.error);
      return;
    }
    const used = new Set((existingAccounts ?? []).map((account) => account.name));
    const nextRows = parsed.accounts.map((account, index) => {
      const name = allocateUniqueName(account.name, used);
      used.add(name);
      return toRow({ ...account, name }, index);
    });
    setRows(nextRows);
    setStep("configure");
    setIsDiscovering(true);
    await Promise.all(nextRows.map((row) => discoverRow(row)));
    setIsDiscovering(false);
  }

  async function discoverRow(row: ImportRow) {
    patchRow(row.id, { resourcesLoading: true, included: true, error: null });
    try {
      const found = await discover.mutateAsync({
        tenant_id: row.tenantId,
        client_id: row.clientId,
        client_secret: row.clientSecret,
        subscription_id: row.subscriptionId,
      });
      if (found.length === 0) {
        patchRow(row.id, {
          resources: [],
          selectedResourceId: "",
          resourcesLoading: false,
          included: true,
          error: "Azure listed no resources. Enter resource name, model, and credit grant below.",
        });
        return;
      }
      const selectedResourceId = found.some((item) => item.resource_id === row.selectedResourceId)
        ? row.selectedResourceId
        : found[0].resource_id;
      patchRow(row.id, { resources: found, selectedResourceId, resourcesLoading: false });
      await loadDeployments(row, selectedResourceId);
    } catch (err: any) {
      patchRow(row.id, {
        resourcesLoading: false,
        included: true,
        error: err?.response?.data?.detail ?? "Could not reach Azure. Enter resource name, model, and credit grant below.",
      });
    }
  }

  function refreshDeployments(row: ImportRow) {
    if (row.selectedResourceId) {
      void loadDeployments(row, row.selectedResourceId);
      return;
    }
    void discoverRow(row);
  }

  function handleResourceChange(row: ImportRow, resourceId: string) {
    patchRow(row.id, { selectedResourceId: resourceId });
    void loadDeployments(row, resourceId);
  }

  function toggleDeployment(row: ImportRow, deployment: Deployment) {
    patchRow(row.id, (current) => {
      const next = { ...current.deploymentLinks };
      if (deployment.name in next) delete next[deployment.name];
      else next[deployment.name] = matchRegisteredId(registeredModels, deployment.model_name);
      return { deploymentLinks: next };
    });
  }

  function handleRegisteredChange(row: ImportRow, deploymentName: string, value: string) {
    if (value === REGISTER_NEW) {
      const deployment = row.deployments.find((item) => item.name === deploymentName) ?? null;
      if (deployment) setRegistering({ rowId: row.id, deployment });
      return;
    }
    patchRow(row.id, (current) => ({ deploymentLinks: { ...current.deploymentLinks, [deploymentName]: value } }));
  }

  async function handleImport() {
    setError(null);
    const selected = rows.filter((row) => row.included);
    if (selected.length === 0) {
      setError("Select at least one account to import.");
      return;
    }
    for (const row of selected) {
      if (!row.selectedResourceId && !row.resourceName.trim()) {
        setError(`Pick a resource or enter a resource name for "${row.name}".`);
        return;
      }
      if (!row.selectedResourceId) {
        const grant = parseCreditGrant(row.creditsLimit);
        if (grant == null || Number.isNaN(grant)) {
          setError(`Set a credit grant in USD for "${row.name}" so alerts can run without Azure credits.`);
          return;
        }
      }
      const incomplete = Object.entries(row.deploymentLinks).filter(([, registeredId]) => !registeredId);
      if (incomplete.length > 0) {
        setError(`Pick a registered model for each selected deployment on "${row.name}", or uncheck it.`);
        return;
      }
    }

    setIsSaving(true);
    const failed: string[] = [];
    try {
      for (let index = 0; index < selected.length; index++) {
        const row = selected[index];
        setSaveProgress({ current: index + 1, total: selected.length });
        const resource = row.resources.find((item) => item.resource_id === row.selectedResourceId);
        const grant = parseCreditGrant(row.creditsLimit);
        if (grant != null && Number.isNaN(grant)) {
          failed.push(`${row.name}: invalid credit grant`);
          continue;
        }
        const payload: Record<string, unknown> = {
          name: row.name,
          tenant_id: row.tenantId,
          client_id: row.clientId,
          client_secret: row.clientSecret,
          subscription_id: row.subscriptionId,
        };
        if (resource) {
          payload.resource_id = resource.resource_id;
          payload.resource_group = resource.resource_group;
          payload.resource_name = resource.name;
          payload.endpoint = resource.endpoint;
          payload.kind = resource.kind;
          payload.location = resource.location;
        } else {
          payload.resource_name = row.resourceName.trim();
          payload.location = row.location.trim();
        }
        if (grant) payload.credits_limit = grant;
        if (row.ownerTag.trim()) payload.owner_tag = row.ownerTag.trim();
        try {
          const account = await createAccount.mutateAsync(payload);
          const links = { ...row.deploymentLinks };
          if (row.deploymentName.trim() && row.registeredId) {
            links[row.deploymentName.trim()] = row.registeredId;
          }
          const linkFailed = await linkSelectedDeployments(createModel.mutateAsync, account.id, links);
          if (linkFailed.length > 0) {
            failed.push(`${row.name} (models: ${linkFailed.join(", ")})`);
          }
        } catch (err: any) {
          failed.push(err?.response?.data?.detail ? `${row.name}: ${err.response.data.detail}` : row.name);
        }
      }
      if (failed.length > 0) {
        setError(`Imported with issues: ${failed.join("; ")}.`);
        return;
      }
      resetAndClose();
    } finally {
      setIsSaving(false);
      setSaveProgress(null);
    }
  }

  const selectedCount = rows.filter((row) => row.included).length;
  const busy = isDiscovering || rows.some((row) => row.resourcesLoading || row.deploymentsLoading);

  return (
    <>
      <Modal
        title={step === "json" ? "Bulk upload accounts" : "Configure imported accounts"}
        isOpen={isOpen}
        onClose={resetAndClose}
        widthClassName={step === "json" ? "max-w-xl" : "max-w-3xl"}
      >
        {step === "json" ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">
              Paste a JSON array. Each object needs <span className="text-gray-200">account_name</span> plus the four
              secrets.
            </p>
            <textarea
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              rows={12}
              spellCheck={false}
              placeholder={`[\n  {\n    "account_name": "Production - East US",\n    "AZURE_TENANT_ID": "...",\n    "AZURE_CLIENT_ID": "...",\n    "AZURE_CLIENT_SECRET": "...",\n    "AZURE_SUBSCRIPTION_ID": "..."\n  }\n]`}
              className="w-full min-w-0 resize-y rounded-lg border border-surface-border bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent"
            />
            {error && <p className="break-words text-sm text-red-400">{error}</p>}
            <div className="flex justify-end">
              <Button onClick={handleNext} disabled={!jsonText.trim()}>
                Next
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">
              {isDiscovering
                ? "Discovering resources and deployments for each account…"
                : "Pick a resource and models for each account, then import."}
            </p>
            <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto pr-1">
              {rows.map((row) => (
                <div key={row.id} className="rounded-xl border border-surface-border bg-surface p-4">
                  <div className="flex items-start justify-between gap-2">
                    <label className="flex min-w-0 items-start gap-2">
                      <input
                        type="checkbox"
                        checked={row.included}
                        onChange={() => patchRow(row.id, { included: !row.included })}
                        className="mt-1 h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
                      />
                      <span className="min-w-0">
                        <span className="block font-medium text-gray-100">{row.name}</span>
                        <span className="block truncate text-xs text-gray-500">{row.subscriptionId}</span>
                      </span>
                    </label>
                    <RefreshTag
                      onClick={() => void discoverRow(row)}
                      loading={row.resourcesLoading || row.deploymentsLoading}
                      disabled={isSaving}
                    />
                  </div>
                  <div className="mt-3 flex flex-col gap-3 pl-6">
                    <OwnerTagField
                      value={row.ownerTag}
                      onChange={(value) => patchRow(row.id, { ownerTag: value })}
                      id={`bulk-owner-tag-${row.id}`}
                      compact
                    />
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-gray-400">Resource</span>
                        <RefreshTag
                          onClick={() => void discoverRow(row)}
                          loading={row.resourcesLoading}
                          disabled={isSaving || row.deploymentsLoading}
                        />
                      </div>
                      <Select
                        value={row.selectedResourceId}
                        onChange={(e) => handleResourceChange(row, e.target.value)}
                        disabled={row.resourcesLoading || row.resources.length === 0}
                      >
                        {row.resources.length === 0 ? (
                          <option value="">{row.resourcesLoading ? "Discovering resources..." : "No resources found — enter manually"}</option>
                        ) : (
                          row.resources.map((resource) => (
                            <option key={resource.resource_id} value={resource.resource_id}>
                              {resource.name} ({resource.kind}, {resource.location})
                            </option>
                          ))
                        )}
                      </Select>
                    </div>
                    {row.resources.length === 0 && !row.resourcesLoading && (
                      <div className="flex flex-col gap-3">
                        <Input
                          label="Resource name"
                          placeholder="e.g. surai-mt700glk-northcentralus"
                          value={row.resourceName}
                          onChange={(e) => patchRow(row.id, { resourceName: e.target.value, included: true })}
                        />
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <Input
                            label="Location"
                            placeholder="e.g. northcentralus"
                            value={row.location}
                            onChange={(e) => patchRow(row.id, { location: e.target.value })}
                          />
                          <Input
                            label="Credit grant (USD)"
                            type="number"
                            min={0}
                            placeholder="e.g. 1000"
                            value={row.creditsLimit}
                            onChange={(e) => patchRow(row.id, { creditsLimit: e.target.value })}
                          />
                        </div>
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <Input
                            label="Deployment name"
                            placeholder="e.g. FW-Kimi-K3"
                            value={row.deploymentName}
                            onChange={(e) => patchRow(row.id, { deploymentName: e.target.value })}
                          />
                          <Select
                            label="Registered model"
                            value={row.registeredId}
                            onChange={(e) => {
                              if (e.target.value === REGISTER_NEW) {
                                setRegistering({
                                  rowId: row.id,
                                  deployment: {
                                    name: row.deploymentName || "deployment",
                                    model_name: row.deploymentName,
                                    model_version: "",
                                    sku: "",
                                    capacity: 0,
                                  },
                                });
                                return;
                              }
                              patchRow(row.id, { registeredId: e.target.value });
                            }}
                          >
                            <option value="">Select a registered model</option>
                            {(registeredModels ?? []).map((model) => (
                              <option key={model.id} value={model.id}>
                                {model.name}
                              </option>
                            ))}
                            <option value={REGISTER_NEW}>+ Register a new model...</option>
                          </Select>
                        </div>
                      </div>
                    )}
                    {row.resources.length > 0 && (
                      <Input
                        label="Credit grant (USD, optional)"
                        type="number"
                        min={0}
                        placeholder="Leave blank to use Azure credits"
                        value={row.creditsLimit}
                        onChange={(e) => patchRow(row.id, { creditsLimit: e.target.value })}
                      />
                    )}
                    <DeploymentLinkPicker
                      deployments={row.deployments}
                      isLoading={row.resourcesLoading || row.deploymentsLoading}
                      registeredModels={registeredModels}
                      links={row.deploymentLinks}
                      headerAction={
                        <RefreshTag
                          onClick={() => refreshDeployments(row)}
                          loading={row.deploymentsLoading}
                          disabled={isSaving || row.resourcesLoading}
                        />
                      }
                      onToggle={(deployment) => toggleDeployment(row, deployment)}
                      onRegisteredChange={(deploymentName, value) => handleRegisteredChange(row, deploymentName, value)}
                    />
                    {row.error && <p className="break-words text-xs text-red-400">{row.error}</p>}
                  </div>
                </div>
              ))}
            </div>
            {error && <p className="break-words text-sm text-red-400">{error}</p>}
            <div className="flex justify-between gap-3">
              <Button
                variant="secondary"
                onClick={() => {
                  setStep("json");
                  setRows([]);
                  setError(null);
                }}
                disabled={isSaving}
              >
                Back
              </Button>
              <Button onClick={handleImport} isLoading={isSaving} disabled={busy || selectedCount === 0}>
                {isSaving && saveProgress
                  ? `Importing ${saveProgress.current}/${saveProgress.total}`
                  : `Import ${selectedCount} account${selectedCount === 1 ? "" : "s"}`}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <RegisterModelModal
        isOpen={registering !== null}
        onClose={() => setRegistering(null)}
        initialName={registering?.deployment.model_name}
        onRegistered={(id) => {
          if (!registering) return;
          patchRow(registering.rowId, (current) => {
            if (current.deployments.some((item) => item.name === registering.deployment.name)) {
              return {
                deploymentLinks: { ...current.deploymentLinks, [registering.deployment.name]: String(id) },
              };
            }
            return { registeredId: String(id) };
          });
        }}
      />
    </>
  );
}

function RefreshTag({
  onClick,
  loading,
  disabled,
}: {
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading}
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-surface-border bg-gray-500/15 px-2 py-0.5 text-xs font-medium text-gray-400 transition-colors hover:border-gray-400 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {loading ? <Spinner className="h-3 w-3" /> : <RefreshCw size={11} />}
      Refresh
    </button>
  );
}

function toRow(account: AzureAccountImport, index: number): ImportRow {
  return {
    id: `${index}-${account.name}`,
    name: account.name,
    tenantId: account.tenantId,
    clientId: account.clientId,
    clientSecret: account.clientSecret,
    subscriptionId: account.subscriptionId,
    included: true,
    resources: [],
    selectedResourceId: "",
    deployments: [],
    deploymentLinks: {},
    resourcesLoading: true,
    deploymentsLoading: false,
    error: null,
    resourceName: account.resourceName ?? "",
    location: account.location ?? "",
    creditsLimit: account.creditsLimit != null ? String(account.creditsLimit) : "",
    deploymentName: account.deploymentName ?? "",
    registeredId: "",
    ownerTag: account.ownerTag ?? "",
  };
}
