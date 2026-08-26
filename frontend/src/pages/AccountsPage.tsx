import { Cloud, Layers, Pencil, Plus, RefreshCw, Rocket, Search, Trash2, Upload, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import AccountCard from "@/components/accounts/AccountCard";
import AddAccountModal from "@/components/accounts/AddAccountModal";
import BulkUploadAccountsModal from "@/components/accounts/BulkUploadAccountsModal";
import GroupFormModal from "@/components/accounts/GroupFormModal";
import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import OwnerChips from "@/components/ui/OwnerChips";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import { useAccounts, useSyncAccounts } from "@/hooks/useAccounts";
import { useAccountGroups, useDeleteAccountGroup } from "@/hooks/useAccountGroups";
import { useUsdInrRate } from "@/hooks/useDashboard";
import {
  combinedSpendUsd,
  compareOptionalNumbers,
  compareOptionalText,
  consumedPercent,
  creditLeftRatio,
  creditOutstandingUsd,
  creditRemainingUsd,
  finiteNumber,
  gatewayRank,
  portalSpendUsd,
} from "@/lib/accountSort";
import { amountPayableUsd } from "@/lib/payable";
import { formatCurrency } from "@/lib/format";
import { matchesOwner, ownerCounts, ownerLabel, uniqueOwners, UNTAGGED_OWNER } from "@/lib/ownerTag";
import { Account, AccountGroup } from "@/types";

type AccountSort =
  | "name"
  | "name-desc"
  | "synced"
  | "credits-left"
  | "credits-left-desc"
  | "consumed"
  | "outstanding"
  | "newapi-spend"
  | "payable"
  | "newapi-o1"
  | "newapi-o2"
  | "gateway"
  | "owner"
  | "location"
  | "created";

type GatewayFilter = "all" | "o1" | "o2" | "both" | "disabled" | "none";

export default function AccountsPage() {
  const navigate = useNavigate();
  const { data: accounts, isLoading, isError: accountsError } = useAccounts();
  const { data: groups, isLoading: isGroupsLoading, isError: groupsError } = useAccountGroups();
  const { syncAccounts, progress, isSyncing } = useSyncAccounts();
  const deleteGroup = useDeleteAccountGroup();
  const fx = useUsdInrRate();
  const usdInr = fx.data?.usd_inr ?? 87;

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isBulkOpen, setIsBulkOpen] = useState(false);
  const [isGroupModalOpen, setIsGroupModalOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<AccountGroup | null>(null);
  const [search, setSearch] = useState("");
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const [gatewayFilter, setGatewayFilter] = useState<GatewayFilter>("all");
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

  const groupsByAccountId = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const group of groups ?? []) {
      for (const member of group.accounts) {
        const names = map.get(member.id) ?? [];
        names.push(group.name);
        map.set(member.id, names);
      }
    }
    return map;
  }, [groups]);

  const ownerStats = useMemo(() => ownerCounts(accounts ?? []), [accounts]);
  const owners = useMemo(() => uniqueOwners(accounts ?? []), [accounts]);

  const filteredAccounts = useMemo(() => {
    const visible = (accounts ?? []).filter((account) => {
      if (!matchesOwner(account.owner_tag, ownerFilter)) return false;
      if (!matchesGatewayFilter(account, gatewayFilter)) return false;
      return matchesAccountSearch(account, search, groupsByAccountId.get(account.id) ?? []);
    });
    return [...visible].sort((left, right) => compareAccounts(left, right, sort, usdInr));
  }, [accounts, gatewayFilter, groupsByAccountId, ownerFilter, search, sort, usdInr]);

  const hasActiveFilters = Boolean(search.trim() || ownerFilter || gatewayFilter !== "all");

  function resetFilters() {
    setSearch("");
    setOwnerFilter(null);
    setGatewayFilter("all");
  }

  const ownerTotals = useMemo(() => {
    const spend = filteredAccounts.reduce((sum, account) => sum + (account.new_api_cost_usd || 0), 0);
    const payable = filteredAccounts.reduce((sum, account) => sum + amountPayableUsd(account.new_api_cost_usd), 0);
    return { spend, payable, count: filteredAccounts.length };
  }, [filteredAccounts]);

  const accountsByTag = useMemo(() => {
    const groups = new Map<string, typeof filteredAccounts>();
    for (const account of filteredAccounts) {
      const tag = ownerLabel(account.owner_tag);
      const rows = groups.get(tag) ?? [];
      rows.push(account);
      groups.set(tag, rows);
    }
    return [...groups.entries()].sort((left, right) => left[0].localeCompare(right[0], undefined, { sensitivity: "base" }));
  }, [filteredAccounts]);

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
          <Button variant="secondary" onClick={() => navigate("/deploy")} className="w-full sm:w-auto">
            <Rocket size={16} /> Deploy Kimi K3
          </Button>
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
                  <div className="flex shrink-0 items-center gap-1">
                    {group.auto && <Badge tone="info">auto</Badge>}
                    <Badge tone="neutral">
                      {group.accounts.length} account{group.accounts.length === 1 ? "" : "s"}
                    </Badge>
                  </div>
                </div>
                <p className="truncate text-xs text-gray-500">
                  {group.auto
                    ? "Auto-filled from ~$1,000 Azure credit grants"
                    : group.accounts.length > 0
                      ? group.accounts.map((a) => a.name).join(", ")
                      : "No accounts yet"}
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
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-200">All accounts</h2>
          {accounts && accounts.length > 0 && (
            <p className="text-xs tabular-nums text-gray-500">
              {filteredAccounts.length === accounts.length
                ? `${accounts.length} account${accounts.length === 1 ? "" : "s"}`
                : `${filteredAccounts.length} of ${accounts.length}`}
            </p>
          )}
        </div>

        {accounts && accounts.length > 0 && (
          <div className="rounded-2xl border border-white/[0.06] bg-surface-raised/80 bg-card-sheen p-4 shadow-card backdrop-blur-sm sm:p-5">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_11.5rem_13.5rem] xl:items-end">
              <div className="flex min-w-0 flex-col gap-1.5 md:col-span-2 xl:col-span-1">
                <label htmlFor="account-search" className="text-xs font-medium text-gray-400">
                  Search
                </label>
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                  <input
                    id="account-search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Name, resource, channel, tag, or group"
                    className="w-full rounded-lg border border-surface-border bg-black/30 py-2 pl-8 pr-9 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent"
                  />
                  {search && (
                    <button
                      type="button"
                      onClick={() => setSearch("")}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-gray-500 hover:text-gray-200"
                      aria-label="Clear search"
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
              </div>
              <Select
                id="account-gateway-filter"
                label="Gateway"
                value={gatewayFilter}
                onChange={(e) => setGatewayFilter(e.target.value as GatewayFilter)}
              >
                <option value="all">All gateways</option>
                <option value="both">O1 + O2</option>
                <option value="o1">On O1</option>
                <option value="o2">On O2</option>
                <option value="disabled">Disabled</option>
                <option value="none">No NewAPI match</option>
              </Select>
              <Select id="account-sort" label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as AccountSort)}>
                <option value="name">Name A–Z</option>
                <option value="name-desc">Name Z–A</option>
                <option value="credits-left">Credits left (low)</option>
                <option value="credits-left-desc">Credits left (high)</option>
                <option value="consumed">Consumed %</option>
                <option value="outstanding">Outstanding</option>
                <option value="newapi-spend">NewAPI spend</option>
                <option value="payable">Amount payable</option>
                <option value="newapi-o1">O1 spend</option>
                <option value="newapi-o2">O2 spend</option>
                <option value="gateway">Gateway status</option>
                <option value="owner">Tag</option>
                <option value="location">Location</option>
                <option value="synced">Last synced</option>
                <option value="created">Recently added</option>
              </Select>
            </div>

            <OwnerChips
              owners={owners}
              counts={ownerStats.counts}
              untagged={ownerStats.untagged}
              value={ownerFilter}
              onChange={setOwnerFilter}
            />

            {ownerFilter && (
              <p className="mt-3 text-xs text-gray-500">
                Combined for {ownerFilter === UNTAGGED_OWNER ? "untagged" : ownerFilter}:{" "}
                <span className="tabular-nums text-violet-300">{formatCurrency(ownerTotals.spend, "USD")}</span> spend ·{" "}
                <span className="tabular-nums text-amber-200">{formatCurrency(ownerTotals.payable, "USD")}</span> payable ·{" "}
                <span className="tabular-nums text-gray-300">{ownerTotals.count}</span> account
                {ownerTotals.count === 1 ? "" : "s"}
              </p>
            )}

            {hasActiveFilters && (
              <div className="mt-3 flex items-center justify-end">
                <button type="button" onClick={resetFilters} className="text-xs text-gray-500 hover:text-gray-200">
                  Reset filters
                </button>
              </div>
            )}
          </div>
        )}

        {isLoading ? (
          <Spinner />
        ) : accounts && accounts.length > 0 ? (
          filteredAccounts.length > 0 ? (
            <div className="flex flex-col gap-6">
              {accountsByTag.map(([tag, rows]) => {
                const spend = rows.reduce((sum, account) => sum + (account.new_api_cost_usd || 0), 0);
                const payable = amountPayableUsd(spend);
                return (
                  <div key={tag} className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <h3 className="text-sm font-semibold text-gray-200">{tag}</h3>
                      <p className="text-xs text-gray-500">
                        <span className="tabular-nums text-violet-300">{formatCurrency(spend, "USD")}</span> spend ·{" "}
                        <span className="tabular-nums text-amber-200">{formatCurrency(payable, "USD")}</span> payable ·{" "}
                        {rows.length} account{rows.length === 1 ? "" : "s"}
                      </p>
                    </div>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                      {rows.map((account) => (
                        <AccountCard key={account.id} account={account} />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState
              icon={<Search size={28} className="text-gray-600" />}
              title={hasActiveFilters ? "No accounts match your filters" : "No accounts match"}
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

function normalizeSearchText(value: string): string {
  return value.toLowerCase().replace(/[_\-\s./]+/g, " ").trim();
}

function matchesAccountSearch(account: Account, rawQuery: string, groupNames: string[]): boolean {
  const query = rawQuery.trim();
  if (!query) return true;

  const tagged = query.match(/^tag:\s*(.*)$/i);
  const needle = normalizeSearchText(tagged ? tagged[1] : query);
  if (tagged && !needle) return Boolean((account.new_api_tag ?? "").trim());
  if (!needle) return true;

  if (tagged) {
    return (
      tokensMatch(normalizeSearchText(account.new_api_tag ?? ""), needle) ||
      tokensMatch(normalizeSearchText(account.new_api_name ?? ""), needle)
    );
  }

  const haystack = normalizeSearchText(
    [
      account.name,
      account.resource_name,
      account.resource_group,
      account.endpoint,
      account.location,
      account.owner_tag,
      account.new_api_name,
      account.new_api_tag,
      account.new_api_gateway,
      ...groupNames,
    ]
      .filter(Boolean)
      .join(" ")
  );
  return tokensMatch(haystack, needle);
}

function tokensMatch(haystack: string, needle: string): boolean {
  if (haystack.includes(needle)) return true;
  return needle.split(/\s+/).filter(Boolean).every((token) => haystack.includes(token));
}

function gatewayLabels(account: Account): Set<string> {
  return new Set((account.new_api_gateway ?? "").split("+").filter((part) => part === "O1" || part === "O2"));
}

function matchesGatewayFilter(account: Account, filter: GatewayFilter): boolean {
  const labels = gatewayLabels(account);
  if (filter === "all") return true;
  if (filter === "none") return labels.size === 0;
  if (filter === "both") return labels.has("O1") && labels.has("O2");
  if (filter === "o1") return labels.has("O1");
  if (filter === "o2") return labels.has("O2");
  return account.new_api_status != null && account.new_api_status !== 1;
}

function compareAccounts(left: Account, right: Account, sort: AccountSort, usdInr: number): number {
  const name = compareOptionalText(left.name, right.name);
  let primary = 0;
  if (sort === "name") primary = name;
  else if (sort === "name-desc") primary = -name;
  else if (sort === "synced") primary = compareOptionalNumbers(syncTime(left), syncTime(right), "desc");
  else if (sort === "credits-left") {
    primary = compareOptionalNumbers(creditLeftRatio(left), creditLeftRatio(right), "asc");
  } else if (sort === "credits-left-desc") {
    primary = compareOptionalNumbers(creditLeftRatio(left), creditLeftRatio(right), "desc");
  } else if (sort === "consumed") primary = compareOptionalNumbers(consumedPercent(left), consumedPercent(right), "desc");
  else if (sort === "outstanding") {
    primary = compareOptionalNumbers(creditOutstandingUsd(left, usdInr), creditOutstandingUsd(right, usdInr), "desc");
  } else if (sort === "newapi-spend") primary = compareOptionalNumbers(combinedSpendUsd(left), combinedSpendUsd(right), "desc");
  else if (sort === "payable") {
    const leftSpend = combinedSpendUsd(left);
    const rightSpend = combinedSpendUsd(right);
    primary = compareOptionalNumbers(
      leftSpend == null ? null : amountPayableUsd(leftSpend),
      rightSpend == null ? null : amountPayableUsd(rightSpend),
      "desc"
    );
  } else if (sort === "newapi-o1") primary = compareOptionalNumbers(portalSpendUsd(left, "O1"), portalSpendUsd(right, "O1"), "desc");
  else if (sort === "newapi-o2") primary = compareOptionalNumbers(portalSpendUsd(left, "O2"), portalSpendUsd(right, "O2"), "desc");
  else if (sort === "gateway") primary = compareOptionalNumbers(gatewayRank(left), gatewayRank(right), "asc");
  else if (sort === "owner") primary = compareOptionalText(left.owner_tag, right.owner_tag);
  else if (sort === "location") primary = compareOptionalText(left.location, right.location);
  else if (sort === "created") primary = compareOptionalNumbers(createdTime(left), createdTime(right), "desc");
  if (primary !== 0) return primary;
  if (sort === "credits-left" || sort === "credits-left-desc") {
    const direction = sort === "credits-left" ? "asc" : "desc";
    const dollars = compareOptionalNumbers(creditRemainingUsd(left, usdInr), creditRemainingUsd(right, usdInr), direction);
    if (dollars !== 0) return dollars;
  }
  return name;
}

function syncTime(account: Account): number | null {
  if (!account.last_synced_at) return null;
  return finiteNumber(new Date(account.last_synced_at).getTime());
}

function createdTime(account: Account): number | null {
  if (!account.created_at) return null;
  return finiteNumber(new Date(account.created_at).getTime());
}

