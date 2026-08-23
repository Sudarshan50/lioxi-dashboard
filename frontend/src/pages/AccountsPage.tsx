import { Cloud, Layers, Pencil, Plus, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import AccountCard from "@/components/accounts/AccountCard";
import AddAccountModal from "@/components/accounts/AddAccountModal";
import BulkUploadAccountsModal from "@/components/accounts/BulkUploadAccountsModal";
import GroupFormModal from "@/components/accounts/GroupFormModal";
import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import { useAccounts, useSyncAccounts } from "@/hooks/useAccounts";
import { useAccountGroups, useDeleteAccountGroup } from "@/hooks/useAccountGroups";
import { Account, AccountGroup } from "@/types";

type AccountSort = "name" | "synced" | "credits-left" | "outstanding" | "newapi-spend";

export default function AccountsPage() {
  const { data: accounts, isLoading, isError: accountsError } = useAccounts();
  const { data: groups, isLoading: isGroupsLoading, isError: groupsError } = useAccountGroups();
  const { syncAccounts, progress, isSyncing } = useSyncAccounts();
  const deleteGroup = useDeleteAccountGroup();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isBulkOpen, setIsBulkOpen] = useState(false);
  const [isGroupModalOpen, setIsGroupModalOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<AccountGroup | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<AccountSort>("name");
  const [syncAllMessage, setSyncAllMessage] = useState<string | null>(null);
  const [syncAllError, setSyncAllError] = useState<string | null>(null);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [deletingGroupId, setDeletingGroupId] = useState<number | null>(null);

  async function handleSyncAll() {
    setSyncAllMessage(null);
    setSyncAllError(null);
    const ids = (accounts ?? []).map((account) => account.id);
    if (ids.length === 0) {
      setSyncAllError("No accounts to sync.");
      return;
    }
    try {
      const result = await syncAccounts(ids);
      const failedNames = (result.failed ?? []).map((item) => item.name).filter((name): name is string => Boolean(name));
      if (result.synced > 0) {
        setSyncAllMessage(`Synced ${result.synced} account${result.synced === 1 ? "" : "s"}.`);
      }
      if (failedNames.length > 0) {
        setSyncAllError(`Sync failed for ${failedNames.join(", ")}.`);
      } else if (result.failed.length > 0) {
        setSyncAllError("Sync failed for one or more accounts.");
      }
    } catch (err: any) {
      setSyncAllError(err?.response?.data?.detail ?? "Sync all failed - check individual accounts below.");
    }
  }

  async function handleDeleteGroup(group: AccountGroup) {
    setGroupError(null);
    setDeletingGroupId(group.id);
    try {
      await deleteGroup.mutateAsync(group.id);
    } catch (err: any) {
      setGroupError(err?.response?.data?.detail ?? "Could not delete this group.");
    } finally {
      setDeletingGroupId(null);
    }
  }

  function openCreateGroup() {
    setEditingGroup(null);
    setIsGroupModalOpen(true);
  }

  function openEditGroup(group: AccountGroup) {
    setEditingGroup(group);
    setIsGroupModalOpen(true);
  }

  const filteredAccounts = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const visible = (accounts ?? []).filter((account) => {
      if (needle) {
        const haystack = `${account.name} ${account.resource_name} ${account.new_api_name ?? ""} ${account.new_api_tag ?? ""}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      if (sort === "synced") return syncTime(account) != null;
      if (sort === "credits-left") return creditRemaining(account) != null;
      if (sort === "outstanding") return creditOutstanding(account) != null;
      if (sort === "newapi-spend") return account.new_api_cost_usd != null;
      return true;
    });
    return [...visible].sort((left, right) => compareAccounts(left, right, sort));
  }, [accounts, search, sort]);

  return (
    <div className="flex flex-col gap-5 sm:gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="gradient-title text-2xl font-semibold tracking-tight">Accounts</h1>
          <p className="mt-1 text-sm text-gray-500">Azure OpenAI / Foundry accounts monitored via read-only service principals</p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:shrink-0">
          {accounts && accounts.length > 0 && (
            <Button variant="secondary" onClick={handleSyncAll} isLoading={isSyncing} className="w-full tabular-nums sm:w-auto">
              {!isSyncing && <RefreshCw size={16} />}
              {isSyncing && progress ? `Sync all ${progress.current}/${progress.total}` : "Sync all"}
            </Button>
          )}
          <Button variant="secondary" onClick={() => setIsBulkOpen(true)} className="w-full sm:w-auto">
            <Upload size={16} /> Bulk upload
          </Button>
          <Button onClick={() => setIsModalOpen(true)} className="w-full sm:w-auto">
            <Plus size={16} /> Add account
          </Button>
        </div>
      </div>
      {syncAllMessage && <Banner tone="success">{syncAllMessage}</Banner>}
      {syncAllError && <Banner tone="error">{syncAllError}</Banner>}
      {(accountsError || groupsError) && <Banner tone="error">Could not load accounts or groups. Try refreshing the page.</Banner>}

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm font-semibold text-gray-200">Account groups</h2>
          <Button onClick={openCreateGroup} className="w-full text-xs sm:w-auto">
            <Plus size={14} /> Create group
          </Button>
        </div>

        {groupError && <Banner tone="error">{groupError}</Banner>}

        {isGroupsLoading ? (
          <Spinner />
        ) : groups && groups.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {groups.map((group) => (
              <Card key={group.id} className="flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-gray-100">{group.name}</p>
                  <Badge tone="neutral">
                    {group.accounts.length} account{group.accounts.length === 1 ? "" : "s"}
                  </Badge>
                </div>
                <p className="truncate text-xs text-gray-500">
                  {group.accounts.length > 0 ? group.accounts.map((a) => a.name).join(", ") : "No accounts yet"}
                </p>
                <div className="mt-1 flex gap-2">
                  <Button variant="secondary" className="px-2.5 py-1.5 text-xs" onClick={() => openEditGroup(group)}>
                    <Pencil size={13} /> Edit
                  </Button>
                  <Button
                    variant="danger"
                    className="px-2.5 py-1.5"
                    onClick={() => handleDeleteGroup(group)}
                    isLoading={deletingGroupId === group.id}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Layers size={28} className="text-gray-600" />}
            title="No account groups yet"
            description="Group accounts (e.g. by team) to filter and view combined usage for them on the Overview dashboard."
            action={<Button onClick={openCreateGroup}>Create group</Button>}
          />
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-gray-200">All accounts</h2>

        {accounts && accounts.length > 0 && (
          <Card className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="relative min-w-0 w-full sm:max-w-sm">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search accounts by name or resource"
                className="w-full rounded-lg border border-surface-border bg-surface py-2 pl-8 pr-3 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent"
              />
            </div>
            <div className="w-full sm:max-w-[14rem]">
              <Select label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as AccountSort)}>
                <option value="name">Name</option>
                <option value="synced">Last synced</option>
                <option value="credits-left">Credits left</option>
                <option value="outstanding">Outstanding amount</option>
                <option value="newapi-spend">NewAPI spend</option>
              </Select>
            </div>
          </Card>
        )}

        {isLoading ? (
          <Spinner />
        ) : accounts && accounts.length > 0 ? (
          filteredAccounts.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {filteredAccounts.map((account) => (
                <AccountCard key={account.id} account={account} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Search size={28} className="text-gray-600" />}
              title={search.trim() ? "No accounts match your search" : emptySortTitle(sort)}
            />
          )
        ) : (
          <EmptyState
            icon={<Cloud size={28} className="text-gray-600" />}
            title="No accounts yet"
            description="Add your first Azure account to start monitoring usage."
            action={<Button onClick={() => setIsModalOpen(true)}>Add account</Button>}
          />
        )}
      </section>

      <AddAccountModal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} />
      <BulkUploadAccountsModal isOpen={isBulkOpen} onClose={() => setIsBulkOpen(false)} />
      <GroupFormModal isOpen={isGroupModalOpen} onClose={() => setIsGroupModalOpen(false)} group={editingGroup} />
    </div>
  );
}

function compareAccounts(left: Account, right: Account, sort: AccountSort): number {
  const primary =
    sort === "synced"
      ? compareNumbers(syncTime(left), syncTime(right), "desc")
      : sort === "credits-left"
        ? compareNumbers(creditRemaining(left), creditRemaining(right), "asc")
        : sort === "outstanding"
          ? compareNumbers(creditOutstanding(left), creditOutstanding(right), "desc")
          : sort === "newapi-spend"
            ? compareNumbers(left.new_api_cost_usd, right.new_api_cost_usd, "desc")
            : 0;
  return primary !== 0 ? primary : left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
}

function syncTime(account: Account): number | null {
  if (!account.last_synced_at) return null;
  const value = new Date(account.last_synced_at).getTime();
  return Number.isFinite(value) ? value : null;
}

function hasMonetaryCredits(account: Account): boolean {
  return account.credits_available && account.credits_unit === "currency";
}

function creditRemaining(account: Account): number | null {
  if (!hasMonetaryCredits(account) || !Number.isFinite(account.credits_remaining)) return null;
  return account.credits_remaining;
}

function creditOutstanding(account: Account): number | null {
  if (!hasMonetaryCredits(account)) return null;
  if (account.credits_used != null && Number.isFinite(account.credits_used)) return Math.max(account.credits_used, 0);
  if (account.credits_limit != null && account.credits_remaining != null) {
    return Math.max(account.credits_limit - account.credits_remaining, 0);
  }
  return null;
}

function compareNumbers(left: number | null, right: number | null, direction: "asc" | "desc"): number {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  const delta = left - right;
  if (delta === 0) return 0;
  return direction === "asc" ? delta : -delta;
}

function emptySortTitle(sort: AccountSort): string {
  if (sort === "synced") return "No accounts have been synced yet";
  if (sort === "credits-left") return "No accounts have credit balances";
  if (sort === "outstanding") return "No accounts have outstanding amounts";
  if (sort === "newapi-spend") return "No accounts are matched to NewAPI channels yet";
  return "No accounts match";
}
