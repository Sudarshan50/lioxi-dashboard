import { Trash2 } from "lucide-react";
import { useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Spinner from "@/components/ui/Spinner";
import { useDeleteModel, useUpdateModel } from "@/hooks/useModels";
import { EstimateCurrency, formatEstimatedCost, formatTokens } from "@/lib/format";
import { BreakdownItem, MonitoredModel } from "@/types";

interface ModelRowProps {
  model: MonitoredModel;
  usage?: BreakdownItem;
  usageLoading?: boolean;
  estimateCurrency: EstimateCurrency;
  usdInr: number;
}

export default function ModelRow({
  model,
  usage,
  usageLoading = false,
  estimateCurrency,
  usdInr,
}: ModelRowProps) {
  const updateModel = useUpdateModel();
  const deleteModel = useDeleteModel();
  const [error, setError] = useState<string | null>(null);

  async function handleToggle() {
    setError(null);
    try {
      await updateModel.mutateAsync({ id: model.id, payload: { enabled: !model.enabled } });
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not update this deployment.");
    }
  }

  async function handleDelete() {
    setError(null);
    try {
      await deleteModel.mutateAsync(model.id);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not delete this deployment.");
    }
  }

  return (
    <tr className="border-b border-surface-border text-sm last:border-0 hover:bg-surface-hover/60">
      <td className="whitespace-nowrap py-3 pr-4">
        <p className="font-medium text-gray-100">{model.model_name}</p>
        <p className="text-xs text-gray-500">{model.deployment_name}</p>
        {error && <p className="mt-1 max-w-[16rem] break-words text-xs text-red-400">{error}</p>}
      </td>
      <td className="whitespace-nowrap py-3 pr-4 text-gray-400">
        {usageLoading ? <Spinner className="h-4 w-4" /> : usage ? formatTokens(usage.total_tokens) : "—"}
      </td>
      <td className="whitespace-nowrap py-3 pr-4 text-gray-400">
        {usageLoading ? (
          <Spinner className="h-4 w-4" />
        ) : usage?.estimated_cost_usd != null ? (
          formatEstimatedCost(usage.estimated_cost_usd, estimateCurrency, usdInr)
        ) : (
          "—"
        )}
      </td>
      <td className="whitespace-nowrap py-3 pr-4">
        <Badge tone={model.enabled ? "success" : "neutral"}>{model.enabled ? "Enabled" : "Disabled"}</Badge>
      </td>
      <td className="whitespace-nowrap py-3 pr-0 text-right">
        <div className="flex justify-end gap-2">
          <Button variant="secondary" className="px-2.5 py-1.5 text-xs" onClick={handleToggle} isLoading={updateModel.isPending}>
            {model.enabled ? "Disable" : "Enable"}
          </Button>
          <Button variant="danger" className="px-2.5 py-1.5" onClick={handleDelete} isLoading={deleteModel.isPending}>
            <Trash2 size={14} />
          </Button>
        </div>
      </td>
    </tr>
  );
}
