import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Inbox, Wallet } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import Badge from "@/components/ui/Badge";
import GroupBadge from "@/components/ui/GroupBadge";
import GroupChips from "@/components/ui/GroupChips";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Spinner from "@/components/ui/Spinner";
import PendingGrantsModal from "@/components/pending/PendingGrantsModal";
import { JoinGroup, groupCounts, matchesGroup } from "@/lib/joinGroup";
import { invalidateAfterDeploy, useKimiDeployDefaults, useSaveKimiDeployDefaults } from "@/hooks/useKimiDeploy";
import apiClient from "@/lib/apiClient";
import { useBanSettings } from "@/hooks/useBan";
import { formatCurrency } from "@/lib/format";
import { enqueuePendingApprove, enqueuePendingApproveBatch } from "@/lib/submitApi";
import { toastError, toastSuccess } from "@/lib/toast";
import { PendingGrantSummary, PendingListResponse, PendingSubmitRequest } from "@/types";

function statusTone(status: string): "info" | "success" | "error" | "warning" | "neutral" {
  if (status === "pending_approval") return "info";
  if (status === "approved") return "success";
  if (status === "failed" || status === "rejected") return "error";
  if (status === "creating_sp" || status === "approving") return "warning";
  return "neutral";
}

function statusLabel(status: string) {
  if (status === "pending_approval") return "pending";
  if (status === "approved") return "approved";
  if (status === "rejected") return "rejected";
  if (status === "failed") return "failed";
  if (status === "approving") return "deploying";
  return status.replace(/_/g, " ");
}

function SubmitCard({
  row,
  busy,
  declining,
  onDecline,
  onApprove,
}: {
  row: PendingSubmitRequest;
  busy: boolean;
  declining: boolean;
  onDecline: () => void;
  onApprove?: () => void;
}) {
  const failed = row.status === "failed";
  const rolesFailed = failed && row.error_kind === "roles";
  const deployFailed = failed && row.error_kind === "deploy";
  const tenantLevel =
    Boolean(row.subscription_id && row.tenant_id && row.subscription_id === row.tenant_id) ||
    /tenant level/i.test(row.subscription_name || "") ||
    /subscriptionnotfound/i.test(row.error_message || "");
  const inFlight = row.status === "creating_sp" || row.status === "approving";
  const canDecline =
    row.status === "pending_approval" || row.status === "failed" || row.status === "creating_sp";
  const canApprove = !inFlight && !rolesFailed && Boolean(row.can_retry_deploy && onApprove);
  return (
    <Card className={`flex flex-col gap-4 ${inFlight ? "!border-accent/40 shadow-glow" : failed ? "!border-red-500/25" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <p className="truncate font-medium text-gray-100">{row.name || row.person_associated || "Submission"}</p>
            {row.person_associated && (
              <Badge tone="info" className="max-w-[8rem] shrink-0 truncate" title="Name tag">
                {row.person_associated}
              </Badge>
            )}
            <GroupBadge group={row.group_tag} />
            <Badge tone={statusTone(row.status)} className={inFlight ? "animate-pulse" : undefined}>
              {statusLabel(row.status)}
            </Badge>
          </div>
          <p className="mt-0.5 truncate text-xs text-gray-500">{row.account_holder || "No email"}</p>
        </div>
      </div>
      <dl className="grid grid-cols-1 gap-1 text-xs text-gray-400">
        <div className="truncate">
          <span className="text-gray-500">Subscription · </span>
          {tenantLevel ? "None (tenant login only)" : row.subscription_name || row.subscription_id || "—"}
        </div>
        {row.subscription_id && <div className="truncate font-mono text-[11px] text-gray-500">{row.subscription_id}</div>}
        {row.tenant_id && (
          <div className="truncate">
            <span className="text-gray-500">Tenant · </span>
            <span className="font-mono text-[11px] text-gray-500">{row.tenant_id}</span>
          </div>
        )}
        {row.billing_error && <div className="text-amber-400">Billing note: {row.billing_error}</div>}
        {tenantLevel && (
          <div className="text-amber-300">
            This is a Microsoft tenant login, not an Azure subscription. Decline removes the card. They must join
            again with an account that has a real subscription.
          </div>
        )}
        {rolesFailed && !tenantLevel && (
          <div className="text-amber-300">
            Azure role assignment failed. Ask them to join again — Retry deploy will not fix missing roles.
          </div>
        )}
        {deployFailed && (
          <div className="text-amber-300">
            K3 deploy failed. Retry deploy if Azure grants Kimi access. Clear leftover removes the empty Azure stack
            and this card.
          </div>
        )}
        {inFlight && <div className="text-indigo-300">Running on the server. You can leave this page.</div>}
        {row.error_message && (
          <div className="whitespace-pre-wrap break-words rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-2 text-red-300">
            {row.error_message}
          </div>
        )}
      </dl>
      {(canDecline || canApprove) && (
        <div className="flex flex-wrap justify-end gap-2">
          {canDecline && (
            <Button
              variant="danger"
              className="px-3 py-1.5 text-xs"
              disabled={busy}
              isLoading={declining}
              onClick={() => {
                if (deployFailed) {
                  if (
                    !window.confirm(
                      "Clear leftover Azure Kimi resources and delete the stored identity? They can join again after this."
                    )
                  ) {
                    return;
                  }
                } else if (!window.confirm("Decline this submission and delete the stored identity?")) {
                  return;
                }
                onDecline();
              }}
            >
              {deployFailed ? "Clear leftover" : "Decline"}
            </Button>
          )}
          {canApprove && (
            <Button className="px-3 py-1.5 text-xs" disabled={busy} onClick={onApprove}>
              {row.status === "failed" ? "Retry deploy" : "Approve"}
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

export default function PendingPage() {
  const queryClient = useQueryClient();
  const defaults = useKimiDeployDefaults();
  const saveDefaults = useSaveKimiDeployDefaults();
  const ban = useBanSettings();
  const [priority, setPriority] = useState(10);
  const [weight, setWeight] = useState(1);
  const seededDefaults = useRef(false);
  const list = useQuery({
    queryKey: ["pending-submits"],
    queryFn: async () => (await apiClient.get<PendingListResponse>("/api/pending")).data,
    refetchInterval: (query) => {
      const rows = query.state.data?.requests ?? [];
      return rows.some((row) => row.status === "approving" || row.status === "creating_sp") ? 3_000 : 8_000;
    },
  });
  const [submitting, setSubmitting] = useState(false);
  const [grantsOpen, setGrantsOpen] = useState(false);
  const [groupFilter, setGroupFilter] = useState<JoinGroup | null>(null);

  useEffect(() => {
    if (!defaults.data || seededDefaults.current) return;
    seededDefaults.current = true;
    setPriority(defaults.data.priority);
    setWeight(defaults.data.weight);
  }, [defaults.data]);

  useEffect(() => {
    if (list.isError) toastError("Could not load pending submissions.", { toastId: "pending-load" });
  }, [list.isError]);

  const routing = { new_api_priority: priority, new_api_weight: weight };

  const reject = useMutation({
    mutationFn: async (id: number) =>
      (
        await apiClient.post<{ ok: boolean; deleted_id: number; subscription_id?: string | null }>(
          `/api/pending/${id}/decline`,
          undefined,
          { timeout: 15 * 60 * 1000 }
        )
      ).data,
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["pending-submits"] });
      const previous = queryClient.getQueryData<PendingListResponse>(["pending-submits"]);
      const row = previous?.requests.find((item) => item.id === id);
      if (row?.error_kind === "deploy") {
        return { previous };
      }
      if (previous) {
        const requests = previous.requests.filter((item) => item.id !== id);
        queryClient.setQueryData<PendingListResponse>(["pending-submits"], {
          requests,
          pending_count: requests.filter((item) => item.status === "pending_approval").length,
          failed_count: requests.filter((item) => item.status === "failed").length,
        });
      }
      return { previous };
    },
    onError: (exc: unknown, _id, context) => {
      if (context?.previous) queryClient.setQueryData(["pending-submits"], context.previous);
      const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toastError(typeof detail === "string" ? detail : "Could not decline this request.");
    },
    onSuccess: (_data, id, context) => {
      const row = context?.previous?.requests.find((item) => item.id === id);
      toastSuccess(
        row?.error_kind === "deploy"
          ? "Leftover Azure Kimi stack cleared. They can join again."
          : "Declined. They can register again."
      );
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["pending-submits"] });
    },
  });

  async function refreshAfterQueue() {
    await queryClient.invalidateQueries({ queryKey: ["pending-submits"] });
    invalidateAfterDeploy(queryClient);
  }

  async function approve(row: PendingSubmitRequest) {
    setSubmitting(true);
    try {
      await enqueuePendingApprove(row.id, routing);
      toastSuccess("Deploy queued on the server.");
      await refreshAfterQueue();
    } catch (exc) {
      toastError(exc instanceof Error ? exc.message : "Could not queue deploy.");
    } finally {
      setSubmitting(false);
    }
  }

  async function approveBatch(retry: boolean) {
    setSubmitting(true);
    try {
      // Without the filter the server sweeps every pending row, deploying
      // accounts the admin cannot see on screen.
      const result = await enqueuePendingApproveBatch({
        retry,
        ...(groupFilter ? { group: groupFilter } : {}),
        ...routing,
      });
      if (result.started.length === 0) {
        toastError(retry ? "Nothing to retry." : "Nothing to approve.");
      } else {
        toastSuccess(
          retry
            ? `Queued ${result.started.length} retry job${result.started.length === 1 ? "" : "s"} on the server.`
            : `Queued ${result.started.length} deploy${result.started.length === 1 ? "" : "s"} on the server.`
        );
      }
      await refreshAfterQueue();
    } catch (exc) {
      toastError(exc instanceof Error ? exc.message : "Could not queue deploys.");
    } finally {
      setSubmitting(false);
    }
  }

  const allRows = list.data?.requests ?? [];
  const groupStats = useMemo(() => groupCounts(allRows), [allRows]);
  const rows = useMemo(
    () => allRows.filter((row) => matchesGroup(row, groupFilter)),
    [allRows, groupFilter]
  );
  const waiting = useMemo(() => rows.filter((row) => row.status === "pending_approval"), [rows]);
  const inflight = useMemo(
    () => rows.filter((row) => row.status === "creating_sp" || row.status === "approving"),
    [rows]
  );
  const failedRows = useMemo(() => rows.filter((row) => row.status === "failed"), [rows]);
  const retryable = useMemo(
    () => failedRows.filter((row) => row.can_retry_deploy && row.error_kind === "deploy"),
    [failedRows]
  );
  const approvedRows = useMemo(() => rows.filter((row) => row.status === "approved"), [rows]);
  const grantSummary: PendingGrantSummary = list.data?.grant_summary ?? {
    total: waiting.length,
    fetched: 0,
    missing: waiting.length,
    failed: 0,
    pool_usd: 0,
    count_10k: 0,
    count_1k: 0,
    count_other: 0,
    pool_10k_usd: 0,
    pool_1k_usd: 0,
    fetched_at: null,
  };
  const grantsLoaded = grantSummary.fetched > 0;
  const busy = submitting || reject.isPending;
  const empty =
    !list.isLoading && waiting.length === 0 && inflight.length === 0 && failedRows.length === 0 && approvedRows.length === 0;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="gradient-title text-2xl font-semibold tracking-tight">Pending</h1>
        <p className="mt-1 text-sm text-gray-500">
          Approve queues Kimi K3 on the server. You can leave this page. Retry deploy reuses the stored identity. Clear
          leftover deletes the empty Azure stack so they can /join again.
        </p>
      </div>
      {ban.data?.auto_approve && (
        <p className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.07] px-4 py-3 text-sm text-emerald-100">
          Auto-approve is on.
        </p>
      )}
      <Card className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-gray-200">Default priority / weight</h2>
        </div>
        <label className="flex flex-col gap-1 text-[11px] text-gray-500">
          Priority
          <input
            type="number"
            min={0}
            max={10000}
            value={priority}
            onChange={(event) => setPriority(Number(event.target.value) || 0)}
            disabled={busy}
            className="w-20 rounded-lg border border-white/[0.08] bg-surface px-2 py-1.5 text-sm text-gray-100 outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-gray-500">
          Weight
          <input
            type="number"
            min={1}
            max={10000}
            value={weight}
            onChange={(event) => setWeight(Math.max(1, Number(event.target.value) || 1))}
            disabled={busy}
            className="w-20 rounded-lg border border-white/[0.08] bg-surface px-2 py-1.5 text-sm text-gray-100 outline-none focus:border-accent"
          />
        </label>
        <Button
          variant="secondary"
          className="px-3 py-1.5 text-xs"
          disabled={busy || saveDefaults.isPending}
          isLoading={saveDefaults.isPending}
          onClick={() => {
            saveDefaults.mutate(
              { priority, weight },
              {
                onSuccess: () => toastSuccess(`Saved default priority ${priority} · weight ${weight}.`),
                onError: (exc: unknown) => {
                  const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
                  toastError(typeof detail === "string" ? detail : "Could not save deploy defaults.");
                },
              }
            );
          }}
        >
          Save defaults
        </Button>
      </Card>
      {inflight.length > 0 && (
        <p className="rounded-xl border border-accent/25 bg-accent/[0.07] px-4 py-3 text-sm text-indigo-100">
          {inflight.length} job{inflight.length === 1 ? "" : "s"} running on the server. This page only checks status —
          closing it does not stop the work.
        </p>
      )}
      {list.isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : empty ? (
        <EmptyState
          icon={<Inbox size={28} className="text-gray-500" />}
          title="No pending submissions"
          description="When someone finishes /join, they show up here for K3 deploy. Account errors from join also land here."
        />
      ) : (
        <>
          {failedRows.length > 0 && (
            <section className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <AlertTriangle size={16} className="text-red-400" />
                <h2 className="text-sm font-semibold text-gray-100">Join / deploy errors</h2>
                <Badge tone="error">{failedRows.length}</Badge>
                {retryable.length > 0 && (
                  <Button
                    className="ml-auto px-3 py-1.5 text-xs"
                    disabled={busy}
                    isLoading={submitting}
                    onClick={() => void approveBatch(true)}
                  >
                    Retry all ({retryable.length})
                  </Button>
                )}
              </div>
              <p className="text-xs text-gray-500">
                Retry deploy if K3 failed after the identity was stored (quota/model access). Clear leftover deletes the
                empty Azure stack and this card. Role failures need /join again.
              </p>
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {failedRows.map((row) => (
                  <SubmitCard
                    key={row.id}
                    row={row}
                    busy={busy}
                    declining={reject.isPending && reject.variables === row.id}
                    onDecline={() => reject.mutate(row.id)}
                    onApprove={row.can_retry_deploy ? () => void approve(row) : undefined}
                  />
                ))}
              </div>
            </section>
          )}
          <GroupChips
            counts={groupStats}
            total={allRows.length}
            value={groupFilter}
            onChange={setGroupFilter}
          />
          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-gray-100">Ready to approve</h2>
              <Badge tone="info">{waiting.length}</Badge>
              {waiting.length > 0 && (
                <Button
                  className="ml-auto px-3 py-1.5 text-xs"
                  disabled={busy}
                  isLoading={submitting}
                  onClick={() => void approveBatch(false)}
                >
                  Approve all ({waiting.length})
                </Button>
              )}
            </div>
            {waiting.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                <span>
                  {grantsLoaded ? (
                    <>
                      Pending Azure grants:{" "}
                      <span className="tabular-nums text-gray-200">{formatCurrency(grantSummary.pool_usd, "USD")}</span>{" "}
                      pool · {grantSummary.count_10k}× 10k · {grantSummary.count_1k}× 1k
                      {grantSummary.count_other > 0 ? ` · ${grantSummary.count_other}× other` : ""}
                      {grantSummary.missing > 0 ? ` · ${grantSummary.missing} not loaded` : ""}
                      {grantSummary.failed > 0 ? ` · ${grantSummary.failed} null` : ""}
                    </>
                  ) : (
                    "Pending Azure grants not loaded"
                  )}
                </span>
                <Button variant="secondary" className="px-2.5 py-1 text-[11px]" onClick={() => setGrantsOpen(true)}>
                  <Wallet size={12} />
                  {grantsLoaded ? "View grants" : "Load grants"}
                </Button>
              </div>
            )}
            {waiting.length === 0 && inflight.length === 0 ? (
              <p className="text-xs text-gray-500">No submissions waiting for K3 deploy.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {inflight.map((row) => (
                  <SubmitCard
                    key={row.id}
                    row={row}
                    busy={busy}
                    declining={reject.isPending && reject.variables === row.id}
                    onDecline={() => reject.mutate(row.id)}
                  />
                ))}
                {waiting.map((row) => (
                  <SubmitCard
                    key={row.id}
                    row={row}
                    busy={busy}
                    declining={reject.isPending && reject.variables === row.id}
                    onDecline={() => reject.mutate(row.id)}
                    onApprove={() => void approve(row)}
                  />
                ))}
              </div>
            )}
          </section>
          {approvedRows.length > 0 && (
            <section className="flex flex-col gap-3">
              <h2 className="text-sm font-semibold text-gray-100">Recently approved</h2>
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {approvedRows.map((row) => (
                  <SubmitCard
                    key={row.id}
                    row={row}
                    busy={false}
                    declining={false}
                    onDecline={() => undefined}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}
      <PendingGrantsModal
        isOpen={grantsOpen}
        onClose={() => setGrantsOpen(false)}
        needsFetch={!grantsLoaded || grantSummary.missing > 0}
      />
    </div>
  );
}
