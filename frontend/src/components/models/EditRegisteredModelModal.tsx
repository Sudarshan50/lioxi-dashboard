import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useUpdateRegisteredModel } from "@/hooks/useRegisteredModels";
import { RegisteredModel } from "@/types";

interface EditRegisteredModelModalProps {
  model: RegisteredModel | null;
  onClose: () => void;
}

export default function EditRegisteredModelModal({ model, onClose }: EditRegisteredModelModalProps) {
  const updateModel = useUpdateRegisteredModel();
  const [inputPrice, setInputPrice] = useState("");
  const [cachedPrice, setCachedPrice] = useState("");
  const [outputPrice, setOutputPrice] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (model) {
      setInputPrice(String(model.input_price_per_million));
      setCachedPrice(String(model.cached_input_price_per_million));
      setOutputPrice(String(model.output_price_per_million));
      setError(null);
    }
  }, [model]);

  async function handleSubmit() {
    if (!model) return;
    setError(null);
    try {
      await updateModel.mutateAsync({
        id: model.id,
        payload: {
          input_price_per_million: parseFloat(inputPrice || "0"),
          cached_input_price_per_million: parseFloat(cachedPrice || "0"),
          output_price_per_million: parseFloat(outputPrice || "0"),
        },
      });
      onClose();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not update this model.");
    }
  }

  return (
    <Modal title="Edit model pricing" isOpen={model !== null} onClose={onClose} widthClassName="max-w-lg">
      {model && (
        <div className="flex flex-col gap-4">
          <p className="text-xs text-gray-500">
            {model.name} - pricing here applies to every deployment linked to it (
            {model.deployments_count} account{model.deployments_count === 1 ? "" : "s"}).
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Input label="Input $/1M" type="number" step="0.01" value={inputPrice} onChange={(e) => setInputPrice(e.target.value)} />
            <Input
              label="Cached input $/1M"
              type="number"
              step="0.01"
              value={cachedPrice}
              onChange={(e) => setCachedPrice(e.target.value)}
            />
            <Input label="Output $/1M" type="number" step="0.01" value={outputPrice} onChange={(e) => setOutputPrice(e.target.value)} />
          </div>
          {error && <p className="break-words text-xs text-red-400">{error}</p>}
          <Button onClick={handleSubmit} isLoading={updateModel.isPending}>
            Save changes
          </Button>
        </div>
      )}
    </Modal>
  );
}
