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
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const names = settings.data?.names ?? [];
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return names;
    return names.filter((row) => row.name.toLowerCase().includes(needle));
  }, [names, search]);

  const accountsByOwner = useMemo(() => {
    const groups = new Map<string, Account[]>();
    for (const account of accounts.data ?? []) {
      const tag = (account.owner_tag ?? "").trim().toLowerCase();
      if (!tag) continue;
      const rows = groups.get(tag) ?? [];
      rows.push(account);
      groups.set(tag, rows);
    }
    return groups;
  }, [accounts.data]);

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) return;
    try {
      const row = await createEnrollee.mutateAsync(name);
      setNewName("");
      toastSuccess(`Added ${row.name}.`);
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

  async function handleAutoApprove(enabled: boolean) {
    try {
      const result = await setAutoApprove.mutateAsync(enabled);
      toastSuccess(
        enabled
          ? result.started.length
            ? `Auto-approve on. Queued ${result.started.length}.`
            : "Auto-approve on."
          : "Auto-approve off."
      );
    } catch (exc) {
      toastError(apiDetail(exc, "Could not update auto-approve."));
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <h1 className="gradient-title text-2xl font-semibold tracking-tight">Ban</h1>

      <Card className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold text-gray-100">Auto-approve</h2>
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(settings.data?.auto_approve)}
          disabled={settings.isLoading || setAutoApprove.isPending}
          onClick={() => void handleAutoApprove(!settings.data?.auto_approve)}
          className="shrink-0 rounded-full p-0.5"
        >
          <span
            className={`relative block h-5 w-9 overflow-hidden rounded-full transition-colors ${
              settings.data?.auto_approve ? "bg-accent" : "bg-white/15"
            }`}
          >
            <span
              className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                settings.data?.auto_approve ? "translate-x-4" : ""
              }`}
            />
          </span>
        </button>
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
          <Button type="submit" isLoading={createEnrollee.isPending} disabled={!newName.trim()} className="sm:mb-0.5">
            <Plus size={16} />
            Add
          </Button>
        </form>
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
              const totals = summarizeAccounts(accountsByOwner.get(row.name.toLowerCase()) ?? [], usdInr);
              return (
                <li key={row.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5 sm:px-4">
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
                    <p className="shrink-0 truncate text-sm font-medium text-gray-100">{row.name}</p>
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
