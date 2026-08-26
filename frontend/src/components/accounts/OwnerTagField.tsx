import Input from "@/components/ui/Input";
import { useAccounts } from "@/hooks/useAccounts";
import { uniqueOwners } from "@/lib/ownerTag";

interface OwnerTagFieldProps {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  compact?: boolean;
}

export default function OwnerTagField({ value, onChange, id = "owner-tag", compact = false }: OwnerTagFieldProps) {
  const { data: accounts } = useAccounts();
  const owners = uniqueOwners(accounts ?? []);
  const listId = `${id}-options`;

  return (
    <div className="flex min-w-0 w-full flex-col gap-1.5">
      <Input
        id={id}
        label="Name tag"
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Ritesh"
        autoComplete="off"
        maxLength={64}
      />
      <datalist id={listId}>
        {owners.map((owner) => (
          <option key={owner} value={owner} />
        ))}
      </datalist>
      {!compact && (
        <p className="-mt-0.5 text-xs text-gray-500">
          Person this account belongs to. Leave blank to auto-tag if the resource is already on the name list.
        </p>
      )}
    </div>
  );
}
