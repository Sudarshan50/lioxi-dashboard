import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useRegisterModel } from "@/hooks/useRegisteredModels";

interface RegisterModelModalProps {
  isOpen: boolean;
  onClose: () => void;
  onRegistered?: (registeredModelId: number) => void;
  initialName?: string;
}

export default function RegisterModelModal({ isOpen, onClose, onRegistered, initialName }: RegisterModelModalProps) {
  const registerModel = useRegisterModel();
  const [name, setName] = useState(initialName ?? "");
  const [inputPrice, setInputPrice] = useState("");
  const [cachedPrice, setCachedPrice] = useState("0");
  const [outputPrice, setOutputPrice] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) setName(initialName ?? "");
  }, [isOpen, initialName]);

  function reset() {
    setName("");
    setInputPrice("");
    setCachedPrice("0");
    setOutputPrice("");
    setError(null);
    onClose();
  }

  async function handleSubmit() {
    setError(null);
    try {
      const registered = await registerModel.mutateAsync({
        name,
        input_price_per_million: parseFloat(inputPrice || "0"),
        cached_input_price_per_million: parseFloat(cachedPrice || "0"),
        output_price_per_million: parseFloat(outputPrice || "0"),
      });
      onRegistered?.(registered.id);
      reset();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not register this model.");
    }
  }

  return (
    <Modal title="Register a model" isOpen={isOpen} onClose={reset} widthClassName="max-w-lg">
      <div className="flex flex-col gap-4">
        <p className="text-xs text-gray-500">
          Register a model once with a unique name and its price. Every deployment of this model across any account
          can then link to it, and editing its price here updates them all - just like registering a channel before
          using it.
        </p>
        <Input
          label="Model name"
          placeholder="e.g. FW-Kimi-K3"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Input label="Input $/1M" type="number" step="0.01" value={inputPrice} onChange={(e) => setInputPrice(e.target.value)} />
          <Input label="Cached input $/1M" type="number" step="0.01" value={cachedPrice} onChange={(e) => setCachedPrice(e.target.value)} />
          <Input label="Output $/1M" type="number" step="0.01" value={outputPrice} onChange={(e) => setOutputPrice(e.target.value)} />
        </div>
        {error && <p className="break-words text-xs text-red-400">{error}</p>}
        <Button onClick={handleSubmit} isLoading={registerModel.isPending} disabled={!name.trim()}>
          Register model
        </Button>
      </div>
    </Modal>
  );
}
