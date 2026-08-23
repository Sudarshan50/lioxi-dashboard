import { useEffect, useState } from "react";

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
import { useCreateAccount, useDiscoverDeployments, useDiscoverResources } from "@/hooks/useAccounts";
import { useCreateModel } from "@/hooks/useModels";
import { useRegisteredModels } from "@/hooks/useRegisteredModels";
import { describeParsedCredentials, looksLikeCredentialBlob, parseAzureCredentials } from "@/lib/parseAzureCredentials";
import { Deployment, DiscoveredResource } from "@/types";

interface AddAccountModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type Step = "credentials" | "select-resource";

export default function AddAccountModal({ isOpen, onClose }: AddAccountModalProps) {
  const [step, setStep] = useState<Step>("credentials");
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [subscriptionId, setSubscriptionId] = useState("");
  const [credentialsJson, setCredentialsJson] = useState("");
  const [jsonHint, setJsonHint] = useState<string | null>(null);
  const [jsonHintTone, setJsonHintTone] = useState<"success" | "error">("success");
  const [resources, setResources] = useState<DiscoveredResource[]>([]);
  const [selectedResourceId, setSelectedResourceId] = useState("");
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [deploymentLinks, setDeploymentLinks] = useState<Record<string, string>>({});
  const [registeringDeployment, setRegisteringDeployment] = useState<Deployment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [didSave, setDidSave] = useState(false);

  const discover = useDiscoverResources();
  const discoverDeployments = useDiscoverDeployments();
  const createAccount = useCreateAccount();
  const createModel = useCreateModel();
  const { data: registeredModels } = useRegisteredModels();

  function resetAndClose() {
    setStep("credentials");
    setName("");
    setTenantId("");
    setClientId("");
    setClientSecret("");
    setSubscriptionId("");
    setCredentialsJson("");
    setJsonHint(null);
    setJsonHintTone("success");
    setResources([]);
    setSelectedResourceId("");
    setDeployments([]);
    setDeploymentLinks({});
    setRegisteringDeployment(null);
    setError(null);
    setDidSave(false);
    onClose();
  }

  function applyCredentialsJson(raw: string, force = false) {
    const trimmed = raw.trim();
    if (!trimmed) {
      setJsonHint(null);
      return;
    }
    if (!force && !looksLikeCredentialBlob(trimmed)) {
      setJsonHint(null);
      return;
    }
    const result = parseAzureCredentials(raw);
    if (result.error) {
      setJsonHintTone("error");
      setJsonHint(result.error);
      return;
    }
    if (result.values.tenantId) setTenantId(result.values.tenantId);
    if (result.values.clientId) setClientId(result.values.clientId);
    if (result.values.clientSecret) setClientSecret(result.values.clientSecret);
    if (result.values.subscriptionId) setSubscriptionId(result.values.subscriptionId);
    setJsonHintTone("success");
    setJsonHint(describeParsedCredentials(result));
    setError(null);
  }

  function registeredIdFor(modelName: string) {
    return matchRegisteredId(registeredModels, modelName);
  }

  async function handleDiscover() {
    setError(null);
    try {
      const found = await discover.mutateAsync({
        tenant_id: tenantId,
        client_id: clientId,
        client_secret: clientSecret,
        subscription_id: subscriptionId,
      });
      if (found.length === 0) {
        setError("No Cognitive Services / Foundry resources found in that subscription for this identity.");
        return;
      }
      setResources(found);
      setSelectedResourceId(found[0].resource_id);
      setStep("select-resource");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not authenticate with those credentials.");
    }
  }

  useEffect(() => {
    if (!isOpen || step !== "select-resource" || !selectedResourceId) return;
    let cancelled = false;
    setDeployments([]);
    setDeploymentLinks({});
    setError(null);
    (async () => {
      try {
        const found = await discoverDeployments.mutateAsync({
          tenant_id: tenantId,
          client_id: clientId,
          client_secret: clientSecret,
          subscription_id: subscriptionId,
          resource_id: selectedResourceId,
        });
        if (cancelled) return;
        setDeployments(found);
        const links: Record<string, string> = {};
        for (const deployment of found) {
          const matchedId = registeredIdFor(deployment.model_name);
          if (matchedId) links[deployment.name] = matchedId;
        }
        setDeploymentLinks(links);
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.response?.data?.detail ?? "Could not load deployments for this resource.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen, step, selectedResourceId]);

  useEffect(() => {
    if (!registeredModels || deployments.length === 0) return;
    setDeploymentLinks((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const deployment of deployments) {
        if (deployment.name in next && !next[deployment.name]) {
          const matchedId = registeredIdFor(deployment.model_name);
          if (matchedId) {
            next[deployment.name] = matchedId;
            changed = true;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [registeredModels, deployments]);

  function toggleDeployment(deployment: Deployment) {
    setDeploymentLinks((prev) => {
      const next = { ...prev };
      if (deployment.name in next) delete next[deployment.name];
      else next[deployment.name] = registeredIdFor(deployment.model_name);
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

  async function handleConfirm() {
    setError(null);
    const resource = resources.find((r) => r.resource_id === selectedResourceId);
    if (!resource) return;
    const incomplete = Object.entries(deploymentLinks).filter(([, registeredId]) => !registeredId);
    if (incomplete.length > 0) {
      setError("Pick a registered model for each selected deployment, or uncheck it.");
      return;
    }
    try {
      const account = await createAccount.mutateAsync({
        name,
        tenant_id: tenantId,
        client_id: clientId,
        client_secret: clientSecret,
        subscription_id: subscriptionId,
        resource_id: resource.resource_id,
        resource_group: resource.resource_group,
        resource_name: resource.name,
        endpoint: resource.endpoint,
        kind: resource.kind,
        location: resource.location,
      });
      const failed = await linkSelectedDeployments(createModel.mutateAsync, account.id, deploymentLinks);
      if (failed.length > 0) {
        setDidSave(true);
        setError(
          `Account saved, but could not link ${failed.join(", ")}. Close this and add them from Models.`
        );
        return;
      }
      resetAndClose();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not save this account.");
    }
  }

  const isSaving = createAccount.isPending || createModel.isPending;

  return (
    <>
      <Modal title="Add Azure account" isOpen={isOpen} onClose={resetAndClose} widthClassName="max-w-xl">
        {step === "credentials" && (
          <div className="flex flex-col gap-4">
            <Input label="Account name" placeholder="e.g. Production - East US" value={name} onChange={(e) => setName(e.target.value)} />
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-gray-400">Paste credentials JSON</label>
              <textarea
                value={credentialsJson}
                onChange={(e) => {
                  setCredentialsJson(e.target.value);
                  applyCredentialsJson(e.target.value);
                }}
                onBlur={() => applyCredentialsJson(credentialsJson, true)}
                rows={5}
                spellCheck={false}
                placeholder={`{\n  "AZURE_TENANT_ID": "...",\n  "AZURE_CLIENT_ID": "...",\n  "AZURE_CLIENT_SECRET": "...",\n  "AZURE_SUBSCRIPTION_ID": "..."\n}`}
                className="w-full min-w-0 resize-y rounded-lg border border-surface-border bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent"
              />
              <p className="text-xs text-gray-500">
                Paste JSON or env vars with AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and
                AZURE_SUBSCRIPTION_ID.
              </p>
              {jsonHint && (
                <p className={jsonHintTone === "error" ? "break-words text-xs text-red-400" : "break-words text-xs text-emerald-400"}>
                  {jsonHint}
                </p>
              )}
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Input label="Tenant ID" value={tenantId} onChange={(e) => setTenantId(e.target.value)} />
              <Input label="Client ID" value={clientId} onChange={(e) => setClientId(e.target.value)} />
            </div>
            <Input label="Client secret" type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} />
            <Input label="Subscription ID" value={subscriptionId} onChange={(e) => setSubscriptionId(e.target.value)} />
            <p className="text-xs text-gray-500">
              Use a read-only service principal with Reader, Monitoring Reader, Cost Management Reader, Cognitive
              Services Usages Reader, and Billing Reader so remaining credits can be loaded. It is stored encrypted
              and is never used to call your models.
            </p>
            {error && <p className="break-words text-xs text-red-400">{error}</p>}
            <Button
              onClick={handleDiscover}
              isLoading={discover.isPending}
              disabled={!name || !tenantId || !clientId || !clientSecret || !subscriptionId}
            >
              Discover resources
            </Button>
          </div>
        )}

        {step === "select-resource" && (
          <div className="flex flex-col gap-4">
            <Select label="Resource" value={selectedResourceId} onChange={(e) => setSelectedResourceId(e.target.value)}>
              {resources.map((resource) => (
                <option key={resource.resource_id} value={resource.resource_id}>
                  {resource.name} ({resource.kind}, {resource.location})
                </option>
              ))}
            </Select>

            <DeploymentLinkPicker
              deployments={deployments}
              isLoading={discoverDeployments.isPending}
              registeredModels={registeredModels}
              links={deploymentLinks}
              onToggle={toggleDeployment}
              onRegisteredChange={handleRegisteredChange}
            />

            {error && <p className="break-words text-xs text-red-400">{error}</p>}
            <div className="flex justify-between gap-3">
              <Button variant="secondary" onClick={() => setStep("credentials")}>
                Back
              </Button>
              <Button
                onClick={didSave ? resetAndClose : handleConfirm}
                isLoading={isSaving}
                disabled={!didSave && discoverDeployments.isPending}
              >
                {didSave ? "Close" : "Save account"}
              </Button>
            </div>
          </div>
        )}
      </Modal>

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
