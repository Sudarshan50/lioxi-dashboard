import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import Spinner from "@/components/ui/Spinner";
import { useAccounts } from "@/hooks/useAccounts";
import { useCreateAccountGroup, useUpdateAccountGroup } from "@/hooks/useAccountGroups";
import { AccountGroup } from "@/types";

interface GroupFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  group?: AccountGroup | null;
}

export default function GroupFormModal({ isOpen, onClose, group }: GroupFormModalProps) {
  const { data: accounts, isLoading: accountsLoading, isError: accountsError } = useAccounts();
  const createGroup = useCreateAccountGroup();
  const updateGroup = useUpdateAccountGroup();
  const isEditing = !!group;

  const [name, setName] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setName(group?.name ?? "");
      setSelectedIds(new Set(group?.accounts.map((a) => a.id) ?? []));
      setError(null);
    }
  }, [isOpen, group]);

  function toggleAccount(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSubmit() {
    setError(null);
    const account_ids = Array.from(selectedIds);
    try {
      if (isEditing && group) {
        await updateGroup.mutateAsync({ id: group.id, payload: { name, account_ids } });
      } else {
        await createGroup.mutateAsync({ name, account_ids });
      }
      onClose();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not save this group.");
    }
  }

  const isPending = createGroup.isPending || updateGroup.isPending;

  return (
    <Modal title={isEditing ? "Edit group" : "Create account group"} isOpen={isOpen} onClose={onClose} widthClassName="max-w-lg">
      <div className="flex flex-col gap-4">
        <Input label="Group name" placeholder="e.g. Team Lioxi" value={name} onChange={(e) => setName(e.target.value)} autoFocus />

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-gray-400">Accounts in this group</label>
          <div className="flex max-h-64 flex-col gap-1 overflow-y-auto rounded-lg border border-surface-border p-2">
            {accountsLoading && (
              <div className="flex justify-center p-3">
                <Spinner />
              </div>
            )}
            {accountsError && <p className="p-2 text-xs text-red-400">Could not load accounts.</p>}
            {!accountsLoading && (accounts ?? []).length === 0 && <p className="p-2 text-xs text-gray-500">No accounts yet.</p>}
            {(accounts ?? []).map((account) => (
              <label
                key={account.id}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-gray-200 hover:bg-surface-hover"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(account.id)}
                  onChange={() => toggleAccount(account.id)}
                  className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
                />
                {account.name}
              </label>
            ))}
          </div>
          <p className="text-xs text-gray-500">{selectedIds.size} account{selectedIds.size === 1 ? "" : "s"} selected</p>
        </div>

        {error && <p className="break-words text-xs text-red-400">{error}</p>}

        <Button onClick={handleSubmit} isLoading={isPending} disabled={!name.trim()}>
          {isEditing ? "Save changes" : "Create group"}
        </Button>
      </div>
    </Modal>
  );
}
