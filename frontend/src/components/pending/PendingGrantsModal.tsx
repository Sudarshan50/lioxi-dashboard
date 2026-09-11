import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import Badge from "@/components/ui/Badge";
import GroupBadge from "@/components/ui/GroupBadge";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import Spinner from "@/components/ui/Spinner";
import apiClient from "@/lib/apiClient";
import { formatCurrency, formatRelative } from "@/lib/format";
import { toastError, toastSuccess } from "@/lib/toast";
import { PendingGrantAccount, PendingGrantSummary, PendingGrantsResponse } from "@/types";

const GRANT_TIMEOUT_MS = 15 * 60 * 1000;

function emptySummary(): PendingGrantSummary {
  return {
    total: 0,
    fetched: 0,
    missing: 0,
    failed: 0,
    pool_usd: 0,
    count_10k: 0,
    count_1k: 0,
    count_other: 0,
    pool_10k_usd: 0,
    pool_1k_usd: 0,
    fetched_at: null,
  };
}

function tierTone(tier: PendingGrantAccount["grant_tier"]): "info" | "success" | "neutral" {
  if (tier === "10k") return "info";
  if (tier === "1k") return "success";
  return "neutral";
}

function grantToast(data: PendingGrantsResponse) {
  if (data.summary.total === 0) return "No waiting submissions to check.";
  const ok = data.summary.fetched - data.summary.failed;
  const parts = [`Checked ${data.summary.total}`];
  if (ok > 0) parts.push(`${ok} with grants`);
  if (data.summary.failed > 0) parts.push(`${data.summary.failed} null`);
  return `${parts.join(" · ")}.`;
}

function grantLabel(row: PendingGrantAccount) {
  if (row.credits_available && row.grant_usd != null) {
    const usd = formatCurrency(row.grant_usd, "USD");
    const code = (row.credits_currency || "USD").toUpperCase();
    if (code !== "USD" && row.credits_limit != null) {
      return `${formatCurrency(row.credits_limit, code)} (${usd})`;
    }
    return usd;
  }
  return "null";
}

export default function PendingGrantsModal({
  isOpen,
  onClose,
  needsFetch,
}: {
  isOpen: boolean;
  onClose: () => void;
  needsFetch: boolean;
}) {
  const queryClient = useQueryClient();
  const refresh = useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<PendingGrantsResponse>("/api/pending/grants/refresh", undefined, {
          timeout: GRANT_TIMEOUT_MS,
        })
      ).data,
    onSuccess: (data) => {
      queryClient.setQueryData(["pending-grants"], data);
      void queryClient.invalidateQueries({ queryKey: ["pending-submits"] });
    },
    onError: (exc: unknown) => {
      const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toastError(typeof detail === "string" ? detail : "Could not fetch Azure grants.");
    },
  });
  const stored = useQuery({
    queryKey: ["pending-grants"],
    queryFn: async () => (await apiClient.get<PendingGrantsResponse>("/api/pending/grants")).data,
    enabled: isOpen && !needsFetch && !refresh.isPending,
  });
  const startRefresh = refresh.mutate;
  const fetching = refresh.isPending;
  const payload = stored.data;
  const summary = payload?.summary ?? emptySummary();
  const accounts = useMemo(() => {
    const rows = payload?.accounts ?? [];
    return [...rows].sort((left, right) => (right.grant_usd ?? -1) - (left.grant_usd ?? -1));
  }, [payload?.accounts]);
  const autoStarted = useRef(false);

  useEffect(() => {
    if (!isOpen) {
      autoStarted.current = false;
      return;
    }
    if (autoStarted.current || fetching) return;
    if (needsFetch || stored.isError || (stored.isSuccess && (stored.data?.summary.missing ?? 0) > 0)) {
      autoStarted.current = true;
      startRefresh();
    }
  }, [isOpen, needsFetch, fetching, stored.isError, stored.isSuccess, stored.data, startRefresh]);

  const showSpinner = fetching || (needsFetch && !payload) || (stored.isLoading && !payload);

  return (
    <Modal title="Pending Azure grants" isOpen={isOpen} onClose={onClose} widthClassName="max-w-2xl">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="text-sm text-gray-400">
            Live Azure credit grants for submissions waiting to approve. This is not loaded until you open this
            panel. Get latest info re-checks Azure and stores the result.
          </p>
          <Button
            variant="secondary"
            className="shrink-0 px-3 py-1.5 text-xs"
            disabled={showSpinner}
            onClick={() => {
              startRefresh(undefined, {
                onSuccess: (data) => toastSuccess(grantToast(data)),
              });
            }}
          >
            <RefreshCw size={14} />
            Get latest info
          </Button>
        </div>
        {showSpinner ? (
          <div className="flex flex-col items-center gap-3 py-12 text-sm text-gray-400">
            <Spinner className="h-7 w-7" />
            Fetching Azure grants…
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <div className="rounded-xl border border-white/[0.06] bg-surface/60 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wide text-gray-500">Total pool</p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-gray-50">
                  {formatCurrency(summary.pool_usd, "USD")}
                </p>
                <p className="text-xs text-gray-500">
                  {summary.total} waiting account{summary.total === 1 ? "" : "s"}
                  {summary.failed > 0 ? ` · ${summary.failed} null` : ""}
                </p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-surface/60 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wide text-gray-500">10k accounts</p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-gray-50">{summary.count_10k}</p>
                <p className="text-xs text-gray-500">{formatCurrency(summary.pool_10k_usd, "USD")} pool</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-surface/60 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wide text-gray-500">1k accounts</p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-gray-50">{summary.count_1k}</p>
                <p className="text-xs text-gray-500">{formatCurrency(summary.pool_1k_usd, "USD")} pool</p>
              </div>
            </div>
            {summary.count_other > 0 && (
              <p className="text-xs text-gray-500">
                {summary.count_other} other grant{summary.count_other === 1 ? "" : "s"} included in the total pool.
              </p>
            )}
            {summary.failed > 0 && (
              <p className="text-xs text-amber-300">
                {summary.failed} account{summary.failed === 1 ? "" : "s"} did not return a grant (null).
              </p>
            )}
            {refresh.isError ? (
              <p className="text-sm text-amber-300">Could not fetch Azure grants. Use Get latest info to retry.</p>
            ) : accounts.length === 0 ? (
              <p className="text-sm text-gray-500">No submissions waiting for K3 deploy.</p>
            ) : (
              <div className="overflow-hidden rounded-xl border border-white/[0.06]">
                <table className="w-full text-left text-sm">
                  <thead className="bg-white/[0.03] text-[11px] uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">Email</th>
                      <th className="px-3 py-2 font-medium">Grant</th>
                      <th className="px-3 py-2 font-medium">Tier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((row) => (
                      <tr key={row.id} className="border-t border-white/[0.05]">
                        <td className="max-w-[14rem] px-3 py-2">
                          <div className="flex min-w-0 items-center gap-1.5">
                            <p className="truncate text-gray-100">{row.email || "No email"}</p>
                            <GroupBadge group={row.group_tag} />
                          </div>
                          {(row.person_associated || row.name) && (
                            <p className="truncate text-[11px] text-gray-500">
                              {[row.person_associated, row.name].filter(Boolean).join(" · ")}
                            </p>
                          )}
                          {row.credits_error && (
                            <p className="mt-0.5 text-[11px] text-amber-300">{row.credits_error}</p>
                          )}
                        </td>
                        <td
                          className={`whitespace-nowrap px-3 py-2 tabular-nums ${
                            row.grant_usd == null ? "text-gray-500" : "text-gray-100"
                          }`}
                        >
                          {grantLabel(row)}
                        </td>
                        <td className="px-3 py-2">
                          {row.grant_tier ? (
                            <Badge tone={tierTone(row.grant_tier)}>{row.grant_tier}</Badge>
                          ) : (
                            <span className="text-xs text-gray-500">null</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-[11px] text-gray-500">
              {summary.fetched_at ? `Last fetched ${formatRelative(summary.fetched_at)}` : "Not fetched yet"}
              {summary.failed > 0 ? ` · ${summary.failed} null` : ""}
              {summary.missing > 0 ? ` · ${summary.missing} not loaded` : ""}
            </p>
          </>
        )}
      </div>
    </Modal>
  );
}
