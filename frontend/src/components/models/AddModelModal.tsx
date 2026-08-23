import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import DeploymentLinkPicker, {
  REGISTER_NEW,
  linkSelectedDeployments,
  matchRegisteredId,
} from "@/components/models/DeploymentLinkPicker";
import RegisterModelModal from "@/components/models/RegisterModelModal";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { useAccountDeployments, useAccounts } from "@/hooks/useAccounts";
import { useCreateModel, useModels } from "@/hooks/useModels";
import { useRegisteredModels } from "@/hooks/useRegisteredModels";
import { Deployment } from "@/types";

interface AddModelModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function AddModelModal({ isOpen, onClose }: AddModelModalProps) {
  const { data: accounts, isLoading: accountsLoading, isError: accountsError } = useAccounts();
  const { data: existingModels } = useModels();
  const { data: registeredModels } = useRegisteredModels();
  const [accountId, setAccountId] = useState<number | null>(null);
  const [deploymentLinks, setDeploymentLinks] = useState<Record<string, string>>({});
  const [registeringDeployment, setRegisteringDeployment] = useState<Deployment | null>(null);
  const [error, setError] = useState<string | null>(null);

  const deployments = useAccountDeployments(accountId);
  const createModel = useCreateModel();

  const alreadyLinked = useMemo(() => {
    const names = new Set<string>();
    for (const model of existingModels ?? []) {
      if (accountId != null && model.provider_account_id === accountId) names.add(model.deployment_name);
    }
    return names;
  }, [existingModels, accountId]);

  useEffect(() => {
    if (!isOpen || !accountId) return;
    const found = deployments.data ?? [];
    const links: Record<string, string> = {};
    for (const deployment of found) {
      if (alreadyLinked.has(deployment.name)) continue;
      const matchedId = matchRegisteredId(registeredModels, deployment.model_name);
      if (matchedId) links[deployment.name] = matchedId;
    }
    setDeploymentLinks(links);
  }, [isOpen, accountId, deployments.data]);

  function reset() {
    setAccountId(null);
    setDeploymentLinks({});
    setRegisteringDeployment(null);
    setError(null);
    onClose();
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
      const deployment = (deployments.data ?? []).find((item) => item.name === deploymentName) ?? null;
      setRegisteringDeployment(deployment);
      return;
    }
    setDeploymentLinks((prev) => ({ ...prev, [deploymentName]: value }));
  }

  async function handleSubmit() {
    setError(null);
    if (!accountId) return;
    const incomplete = Object.entries(deploymentLinks).filter(([, registeredId]) => !registeredId);
    if (incomplete.length > 0) {
      setError("Pick a registered model for each selected deployment, or uncheck it.");
      return;
    }
    if (Object.keys(deploymentLinks).length === 0) {
      setError("Select at least one deployment to link.");
      return;
    }
    try {
      const failed = await linkSelectedDeployments(createModel.mutateAsync, accountId, deploymentLinks);
      if (failed.length > 0) {
        setError(`Could not link ${failed.join(", ")}.`);
        return;
      }
      reset();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not add these models.");
    }
  }

  const selectedCount = Object.keys(deploymentLinks).length;
  const isSaving = createModel.isPending;

  return (
    <>
      <Modal title="Add model to monitor" isOpen={isOpen} onClose={reset} widthClassName="max-w-xl">
        <div className="flex flex-col gap-4">
          <Select
            label="Account"
            value={accountId ?? ""}
            disabled={accountsLoading}
            onChange={(e) => {
              setAccountId(Number(e.target.value) || null);
              setDeploymentLinks({});
              setError(null);
            }}
          >
            <option value="">{accountsLoading ? "Loading accounts..." : "Select an account"}</option>
            {accounts?.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </Select>
          {accountsError && <p className="break-words text-xs text-red-400">Could not load accounts.</p>}

          {accountId && (
            <DeploymentLinkPicker
              deployments={deployments.data ?? []}
              isLoading={deployments.isLoading}
              emptyLabel="No deployments found on this account."
              alreadyLinked={alreadyLinked}
              registeredModels={registeredModels}
              links={deploymentLinks}
              onToggle={toggleDeployment}
              onRegisteredChange={handleRegisteredChange}
            />
          )}
          {deployments.isError && (
            <p className="break-words text-xs text-red-400">Could not load deployments for this account.</p>
          )}

          {error && <p className="break-words text-xs text-red-400">{error}</p>}

          <Button
            onClick={handleSubmit}
            isLoading={isSaving}
            disabled={!accountId || selectedCount === 0 || deployments.isLoading}
          >
            <Plus size={16} />
            {selectedCount > 1 ? `Link ${selectedCount} deployments` : "Link deployment"}
          </Button>
        </div>
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
