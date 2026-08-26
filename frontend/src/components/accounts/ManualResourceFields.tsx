import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";

export interface ManualResourceValues {
  resourceName: string;
  resourceGroup: string;
  location: string;
  kind: string;
  endpoint: string;
  creditsLimit: string;
}

interface ManualResourceFieldsProps {
  values: ManualResourceValues;
  onChange: (patch: Partial<ManualResourceValues>) => void;
  requireResourceName?: boolean;
}

export function emptyManualResource(creditsLimit = ""): ManualResourceValues {
  return {
    resourceName: "",
    resourceGroup: "",
    location: "",
    kind: "AIServices",
    endpoint: "",
    creditsLimit,
  };
}

export function parseCreditGrant(raw: string): number | undefined {
  const trimmed = raw.replace(/,/g, "").trim();
  if (!trimmed) return undefined;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0) return NaN;
  return value;
}

export default function ManualResourceFields({
  values,
  onChange,
  requireResourceName = true,
}: ManualResourceFieldsProps) {
  return (
    <div className="flex flex-col gap-4">
      <Input
        label={requireResourceName ? "Resource name" : "Resource name (optional)"}
        placeholder="e.g. surai-mt700glk-northcentralus"
        value={values.resourceName}
        onChange={(e) => {
          const resourceName = e.target.value;
          const patch: Partial<ManualResourceValues> = { resourceName };
          if (!values.endpoint || values.endpoint.includes(`${values.resourceName}.`)) {
            const trimmed = resourceName.trim();
            patch.endpoint = trimmed ? `https://${trimmed}.cognitiveservices.azure.com/` : "";
          }
          onChange(patch);
        }}
      />
      <p className="text-xs text-gray-500">
        Use the Azure OpenAI / Foundry resource name (the hostname label). NewAPI matching uses this
        name, so alerts still attach after the next NewAPI sync.
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Input
          label="Location"
          placeholder="e.g. northcentralus"
          value={values.location}
          onChange={(e) => onChange({ location: e.target.value })}
        />
        <Input
          label="Resource group"
          placeholder="optional"
          value={values.resourceGroup}
          onChange={(e) => onChange({ resourceGroup: e.target.value })}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select label="Kind" value={values.kind} onChange={(e) => onChange({ kind: e.target.value })}>
          <option value="AIServices">AIServices</option>
          <option value="OpenAI">OpenAI</option>
          <option value="Project">Project</option>
        </Select>
        <Input
          label="Credit grant (USD)"
          type="number"
          min={0}
          step="1"
          placeholder="e.g. 1000 or 10000"
          value={values.creditsLimit}
          onChange={(e) => onChange({ creditsLimit: e.target.value })}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {[1000, 10000].map((amount) => (
          <button
            key={amount}
            type="button"
            onClick={() => onChange({ creditsLimit: String(amount) })}
            className="rounded-full border border-surface-border px-2 py-0.5 text-xs text-gray-400 hover:border-accent/40 hover:text-gray-200"
          >
            ${amount.toLocaleString()}
          </button>
        ))}
      </div>
      <p className="text-xs text-gray-500">
        Credit grant is the cap NewAPI spend is compared to for Telegram alerts and auto-disable.
        Set it when Azure credits cannot be fetched.
      </p>
      <Input
        label="Endpoint"
        placeholder="auto-filled from resource name"
        value={values.endpoint}
        onChange={(e) => onChange({ endpoint: e.target.value })}
      />
    </div>
  );
}
