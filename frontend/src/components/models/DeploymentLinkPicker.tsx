import { ReactNode } from "react";

import { Deployment, RegisteredModel } from "@/types";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";

export const REGISTER_NEW = "register-new";

interface DeploymentLinkPickerProps {
  deployments: Deployment[];
  isLoading: boolean;
  emptyLabel?: string;
  alreadyLinked?: Set<string>;
  registeredModels: RegisteredModel[] | undefined;
  links: Record<string, string>;
  headerAction?: ReactNode;
  onToggle: (deployment: Deployment) => void;
  onRegisteredChange: (deploymentName: string, value: string) => void;
}

export default function DeploymentLinkPicker({
  deployments,
  isLoading,
  emptyLabel = "No deployments found on this resource.",
  alreadyLinked,
  registeredModels,
  links,
  headerAction,
  onToggle,
  onRegisteredChange,
}: DeploymentLinkPickerProps) {
  const selectedCount = Object.keys(links).length;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-medium text-gray-400">Models on this resource</label>
        <div className="flex items-center gap-2">
          {headerAction}
          {isLoading && !headerAction && <Spinner className="h-4 w-4" />}
        </div>
      </div>
      <div className="flex max-h-64 flex-col gap-1 overflow-y-auto rounded-lg border border-surface-border p-2">
        {!isLoading && deployments.length === 0 && <p className="p-2 text-xs text-gray-500">{emptyLabel}</p>}
        {deployments.map((deployment) => {
          const linked = alreadyLinked?.has(deployment.name) ?? false;
          const checked = linked || deployment.name in links;
          return (
            <div key={deployment.name} className="rounded-md px-2 py-1.5 hover:bg-surface-hover">
              <label className="flex items-start gap-2 text-sm text-gray-200">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={linked}
                  onChange={() => onToggle(deployment)}
                  className="mt-0.5 h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent disabled:opacity-50"
                />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{deployment.name}</span>
                  <span className="block text-xs text-gray-500">
                    {deployment.model_name} {deployment.model_version}
                    {linked ? " · already linked" : ""}
                  </span>
                </span>
              </label>
              {checked && !linked && (
                <div className="mt-2 pl-6">
                  <Select
                    value={links[deployment.name] ?? ""}
                    onChange={(e) => onRegisteredChange(deployment.name, e.target.value)}
                  >
                    <option value="">Select a registered model</option>
                    {(registeredModels ?? []).map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.name} (${model.input_price_per_million}/{model.output_price_per_million} per 1M)
                      </option>
                    ))}
                    <option value={REGISTER_NEW}>+ Register a new model...</option>
                  </Select>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-xs text-gray-500">
        {selectedCount} model{selectedCount === 1 ? "" : "s"} selected. You can also add them later from Models.
      </p>
    </div>
  );
}

export function matchRegisteredId(registeredModels: RegisteredModel[] | undefined, modelName: string) {
  const match = (registeredModels ?? []).find((model) => model.name.toLowerCase() === modelName.toLowerCase());
  return match ? String(match.id) : "";
}

export async function linkSelectedDeployments(
  create: (payload: Record<string, unknown>) => Promise<unknown>,
  accountId: number,
  links: Record<string, string>
) {
  const entries = Object.entries(links).filter(([, registeredId]) => registeredId);
  const results = await Promise.allSettled(
    entries.map(([deploymentName, registeredModelId]) =>
      create({
        provider_account_id: accountId,
        deployment_name: deploymentName,
        registered_model_id: Number(registeredModelId),
      })
    )
  );
  return entries.filter((_, index) => results[index].status === "rejected").map(([name]) => name);
}
