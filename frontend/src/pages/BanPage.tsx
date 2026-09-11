import { Ban, Plus, Search, ShieldBan, UserPlus } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Input from "@/components/ui/Input";
import Spinner from "@/components/ui/Spinner";
import { useAccounts } from "@/hooks/useAccounts";
import { useBanSettings, useCreateEnrollee, useSetAutoApprove, useSetEnrolleeBanned } from "@/hooks/useBan";
import { useUsdInrRate } from "@/hooks/useDashboard";
import { GrantTierBits, summarizeAccounts } from "@/lib/grantSpend";
import GroupBadge from "@/components/ui/GroupBadge";
import { GROUP_SB, GROUP_VCS, JOIN_GROUPS, JoinGroup, groupLabel, normalizeGroup } from "@/lib/joinGroup";
import { toastError, toastSuccess } from "@/lib/toast";
import { Account } from "@/types";

function apiDetail(exc: unknown, fallback: string) {
  const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}

export default function BanPage() {
  const settings = useBanSettings();
  const accounts = useAccounts();
  const fx = useUsdInrRate();
  const usdInr = fx.data?.usd_inr ?? 87;
  const setAutoApprove = useSetAutoApprove();
  const createEnrollee = useCreateEnrollee();
  const setBanned = useSetEnrolleeBanned();
  const [newName, setNewName] = useState("");
  const [newGroup, setNewGroup] = useState<JoinGroup>(GROUP_SB);
  const [groupFilter, setGroupFilter] = useState<JoinGroup | "">("");
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const names = settings.data?.names ?? [];
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return names.filter((row) => {
      if (groupFilter && normalizeGroup(row.group_tag) !== groupFilter) return false;
      if (!needle) return true;
      return (
        row.name.toLowerCase().includes(needle) ||
        groupLabel(row.group_tag).toLowerCase().includes(needle)
      );
    });
  }, [names, search, groupFilter]);

  const countsByGroup = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of names) {
      const key = normalizeGroup(row.group_tag);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [names]);

  // Keyed by name AND group: the same person can be enrolled in both, and
  // their SB accounts must not be counted against their VCS row.
  const accountsByOwner = useMemo(() => {
    const groups = new Map<string, Account[]>();
    for (const account of accounts.data ?? []) {
      const tag = (account.owner_tag ?? "").trim().toLowerCase();
      if (!tag) continue;
      const key = `${tag}|${normalizeGroup(account.group_tag)}`;
      const rows = groups.get(key) ?? [];
      rows.push(account);
      groups.set(key, rows);
    }
    return groups;
  }, [accounts.data]);

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) return;
    try {
      const row = await createEnrollee.mutateAsync({ name, group: newGroup });
      setNewName("");
      toastSuccess(`Added ${row.name} to ${groupLabel(row.group_tag)}.`);
    } catch (exc) {
      toastError(apiDetail(exc, "Could not add that name."));
    }
  }

  async function handleToggle(id: number, banned: boolean) {
    setBusyId(id);
    try {
      const row = await setBanned.mutateAsync({ id, banned });
      toastSuccess(banned ? `Banned ${row.name}.` : `Unbanned ${row.name}.`);
    } catch (exc) {
      toastError(apiDetail(exc, "Could not update that name."));
    } finally {
      setBusyId(null);
    }
  }

  async function handleAutoApprove(group: JoinGroup, enabled: boolean) {
    const label = groupLabel(group);
    try {
      const result = await setAutoApprove.mutateAsync({ enabled, group });
      toastSuccess(
        enabled
          ? result.started.length
            ? `${label} auto-approve on. Queued ${result.started.length}.`
            : `${label} auto-approve on.`
          : `${label} auto-approve off.`
      );
    } catch (exc) {
      toastError(apiDetail(exc, "Could not update auto-approve."));
    }
  }

  function autoApproveRow(group: JoinGroup, enabled: boolean, hint: string) {
    return (
      <div key={group} className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-100">{groupLabel(group)}</p>
          <p className="text-xs text-gray-500">{hint}</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-label={`${groupLabel(group)} auto-approve`}
          aria-checked={enabled}
          disabled={settings.isLoading || setAutoApprove.isPending}
          onClick={() => void handleAutoApprove(group, !enabled)}
          className="shrink-0 rounded-full p-0.5"
        >
          <span
            className={`relative block h-5 w-9 overflow-hidden rounded-full transition-colors ${
              enabled ? "bg-accent" : "bg-white/15"
            }`}
          >
            <span
              className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                enabled ? "translate-x-4" : ""
              }`}
            />
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <h1 className="gradient-title text-2xl font-semibold tracking-tight">Ban</h1>

      <Card className="flex flex-col gap-4">
        <h2 className="text-sm font-semibold text-gray-100">Auto-approve</h2>
        <div className="flex flex-col gap-4 divide-y divide-white/[0.05] [&>*:not(:first-child)]:pt-4">
          {autoApproveRow(GROUP_SB, Boolean(settings.data?.auto_approve), "Deploys Lioxi-named accounts on submit.")}
          {autoApproveRow(
            GROUP_VCS,
            Boolean(settings.data?.auto_approve_vcs),
            "Deploys email-initial accounts on submit."
          )}
        </div>
      </Card>

      <Card className="flex flex-col gap-4">
        <h2 className="text-sm font-semibold text-gray-100">Names</h2>
        <form onSubmit={(event) => void handleAdd(event)} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <Input
            id="ban-new-name"
            label="Add a name"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            autoComplete="off"
          />
          <label className="flex shrink-0 flex-col gap-1.5">
            <span className="text-xs font-medium text-gray-400">Group</span>
            <select
              value={newGroup}
              onChange={(event) => setNewGroup(normalizeGroup(event.target.value))}
              className="rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent"
            >
              {JOIN_GROUPS.map((option) => (
                <option key={option} value={option}>
                  {groupLabel(option)}
                </option>
              ))}
            </select>
          </label>
          <Button type="submit" isLoading={createEnrollee.isPending} disabled={!newName.trim()} className="sm:mb-0.5">
            <Plus size={16} />
            Add
          </Button>
        </form>
        <div className="flex flex-wrap gap-1.5">
          {([["", "All"], ...JOIN_GROUPS.map((g) => [g, groupLabel(g)])] as [JoinGroup | "", string][]).map(
            ([value, label]) => (
              <button
                key={value || "all"}
                type="button"
                onClick={() => setGroupFilter(value)}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  groupFilter === value
                    ? "border-accent/40 bg-accent/15 text-accent"
                    : "border-white/[0.08] text-gray-400 hover:border-white/[0.16] hover:text-gray-200"
                }`}
              >
                {label}
                {value ? ` ${countsByGroup.get(value) ?? 0}` : ` ${names.length}`}
              </button>
            )
          )}
        </div>
        <div className="relative">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter"
            className="w-full rounded-lg border border-surface-border bg-surface py-2 pl-9 pr-3 text-sm text-gray-100 outline-none placeholder:text-gray-600 focus:border-accent"
          />
        </div>
        {settings.isLoading ? (
          <div className="flex justify-center py-12">
            <Spinner />
          </div>
        ) : settings.isError ? (
          <EmptyState
            icon={<ShieldBan size={28} className="text-gray-500" />}
            title="Could not load names"
            action={
              <Button variant="secondary" onClick={() => void settings.refetch()}>
                Retry
              </Button>
            }
          />
        ) : names.length === 0 ? (
          <EmptyState icon={<UserPlus size={28} className="text-gray-500" />} title="No names" />
        ) : filtered.length === 0 ? (
          <p className="py-6 text-center text-sm text-gray-500">No match.</p>
        ) : (
          <ul className="divide-y divide-white/[0.05] rounded-xl border border-white/[0.06]">
            {filtered.map((row) => {
              const busy = busyId === row.id;
              const group = normalizeGroup(row.group_tag);
              const totals = summarizeAccounts(
                accountsByOwner.get(`${row.name.toLowerCase()}|${group}`) ?? [],
                usdInr
              );
              return (
                <li key={row.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5 sm:px-4">
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
                    <div className="flex shrink-0 items-center gap-1.5">
                      <p className="truncate text-sm font-medium text-gray-100">{row.name}</p>
                      <GroupBadge group={group} />
                    </div>
                    <p className="min-w-0 text-xs text-gray-500">
                      <GrantTierBits label="1k" tone="emerald" bucket={totals.oneK} />
                      {" · "}
                      <GrantTierBits label="10k" tone="sky" bucket={totals.tenK} />
                    </p>
                  </div>
                  <Badge tone={row.banned ? "error" : "success"}>{row.banned ? "banned" : "open"}</Badge>
                  <Button
                    variant="secondary"
                    className="px-3 py-1.5 text-xs"
                    disabled={busy}
                    isLoading={busy && setBanned.isPending}
                    onClick={() => void handleToggle(row.id, !row.banned)}
                  >
                    <Ban size={14} />
                    {row.banned ? "Unban" : "Ban"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
