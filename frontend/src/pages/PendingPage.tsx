import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Inbox } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Spinner from "@/components/ui/Spinner";
import { invalidateAfterDeploy } from "@/hooks/useKimiDeploy";
import apiClient from "@/lib/apiClient";
import { streamPendingApprove } from "@/lib/submitApi";
import { KimiDeployProgressEvent, KimiDeployResult, PendingListResponse, PendingSubmitRequest } from "@/types";

type RunProgress = { total: number; done: number; phase: string; message: string; startedAt: number };

function statusTone(status: string): "info" | "success" | "error" | "warning" | "neutral" {
  if (status === "pending_approval") return "info";
  if (status === "approved") return "success";
  if (status === "failed" || status === "rejected") return "error";
  if (status === "creating_sp") return "warning";
  return "neutral";
}

function statusLabel(status: string) {
  if (status === "pending_approval") return "pending";
  if (status === "approved") return "approved";
  if (status === "rejected") return "rejected";
  if (status === "failed") return "failed";
  return status.replace(/_/g, " ");
}

function deployPercent(progress: RunProgress) {
  if (progress.phase === "done") return 100;
  const azure = progress.total > 0 ? progress.done / progress.total : 0;
  if (progress.phase === "azure") return Math.min(82, Math.max(3, Math.round(azure * 82)));
  if (progress.phase === "portal") return 88;
  if (progress.phase === "newapi") return 94;
  return Math.min(99, Math.max(3, Math.round(azure * 100)));
}

function formatElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function ProgressBar({ progress, elapsedMs, failed }: { progress: RunProgress; elapsedMs: number; failed: number }) {
  const percent = deployPercent(progress);
  return (
    <div className="overflow-hidden rounded-2xl border border-accent/25 bg-accent/[0.07] px-4 py-3 shadow-glow">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-100">{progress.message}</p>
          <p className="mt-0.5 text-xs text-gray-400">
            {progress.phase}
            {failed ? ` · ${failed} failed` : ""}
          </p>
        </div>
        <div className="flex items-baseline gap-3 tabular-nums">
          <span className="text-lg font-semibold text-gray-100">{percent}%</span>
          <span className="text-xs text-gray-500">{formatElapsed(elapsedMs)}</span>
        </div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/[0.08]">
        <div
          className="relative h-full overflow-hidden rounded-full bg-accent-gradient transition-[width] duration-700 ease-out"
          style={{ width: `${percent}%` }}
        >
          <span className="absolute inset-y-0 left-0 w-2/3 animate-bar-shimmer bg-gradient-to-r from-transparent via-white/35 to-transparent" />
        </div>
      </div>
    </div>
  );
}

function SubmitCard({
  row,
  deploy,
  live,
  busy,
  declining,
  onDecline,
  onApprove,
}: {
  row: PendingSubmitRequest;
  deploy?: KimiDeployResult;
  live: boolean;
  busy: boolean;
  declining: boolean;
  onDecline: () => void;
  onApprove?: () => void;
}) {
  const failed = row.status === "failed";
  return (
    <Card className={`flex flex-col gap-4 ${live ? "!border-accent/40 shadow-glow" : failed ? "!border-red-500/25" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <p className="truncate font-medium text-gray-100">{row.name || row.person_associated || "Submission"}</p>
            {row.person_associated && (
              <Badge tone="info" className="max-w-[8rem] shrink-0 truncate" title="Name tag">
                {row.person_associated}
              </Badge>
            )}
            <Badge tone={statusTone(row.status)} className={live ? "animate-pulse" : undefined}>
              {live ? "deploying" : statusLabel(row.status)}
            </Badge>
          </div>
          <p className="mt-0.5 truncate text-xs text-gray-500">{row.account_holder || "No email"}</p>
        </div>
      </div>
      <dl className="grid grid-cols-1 gap-1 text-xs text-gray-400">
        <div className="truncate">
          <span className="text-gray-500">Subscription · </span>
          {row.subscription_name || row.subscription_id || "—"}
        </div>
        {row.subscription_id && <div className="truncate font-mono text-[11px] text-gray-500">{row.subscription_id}</div>}
        {row.billing_error && <div className="text-amber-400">Billing note: {row.billing_error}</div>}
        {(failed || (row.error_message && row.status !== "pending_approval")) && row.error_message && (
          <div className="whitespace-pre-wrap break-words rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-2 text-red-300">
            {row.error_message}
          </div>
        )}
        {deploy?.ok && (
          <div className="text-emerald-400">
            Deployed {deploy.account_name || deploy.name}
            {deploy.new_api_name ? ` · ${deploy.new_api_name}` : ""}
          </div>
        )}
        {deploy && !deploy.ok && deploy.error && <div className="text-red-400">{deploy.error}</div>}
      </dl>
      {(row.status === "pending_approval" || row.status === "failed") && (
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="danger" className="px-3 py-1.5 text-xs" disabled={busy} isLoading={declining} onClick={onDecline}>
            Decline
          </Button>
          {row.can_retry_deploy && onApprove && (
            <Button className="px-3 py-1.5 text-xs" disabled={busy} isLoading={live} onClick={onApprove}>
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
  const list = useQuery({
    queryKey: ["pending-submits"],
    queryFn: async () => (await apiClient.get<PendingListResponse>("/api/pending")).data,
    refetchInterval: 15_000,
  });
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [results, setResults] = useState<Record<number, KimiDeployResult>>({});
  const [banner, setBanner] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (approvingId == null) return;
    const id = window.setInterval(() => setNow(Date.now()), 400);
    return () => window.clearInterval(id);
  }, [approvingId]);

  const reject = useMutation({
    mutationFn: async (id: number) =>
      (await apiClient.post<{ ok: boolean; deleted_id: number; subscription_id?: string | null }>(`/api/pending/${id}/decline`))
        .data,
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["pending-submits"] });
      const previous = queryClient.getQueryData<PendingListResponse>(["pending-submits"]);
      if (previous) {
        const requests = previous.requests.filter((row) => row.id !== id);
        queryClient.setQueryData<PendingListResponse>(["pending-submits"], {
          requests,
          pending_count: requests.filter((row) => row.status === "pending_approval").length,
          failed_count: requests.filter((row) => row.status === "failed").length,
        });
      }
      return { previous };
    },
    onError: (exc: unknown, _id, context) => {
      if (context?.previous) queryClient.setQueryData(["pending-submits"], context.previous);
      const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setBanner({ tone: "error", text: typeof detail === "string" ? detail : "Could not decline this request." });
    },
    onSuccess: () => {
      setBanner({ tone: "success", text: "Declined. That Azure subscription can register again at /join." });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["pending-submits"] });
    },
  });

  async function approve(row: PendingSubmitRequest) {
    if (approvingId != null) return;
    setBanner(null);
    setApprovingId(row.id);
    setProgress({ total: 1, done: 0, phase: "azure", message: "Starting Kimi K3 deploy…", startedAt: Date.now() });
    let failed = false;
    try {
      await streamPendingApprove(row.id, (event: KimiDeployProgressEvent) => {
        if (event.type === "start" || event.type === "phase") {
          setProgress((prev) => ({
            total: event.total ?? prev?.total ?? 1,
            done: event.done ?? prev?.done ?? 0,
            phase: event.phase ?? prev?.phase ?? "azure",
            message: event.message ?? prev?.message ?? "Deploying…",
            startedAt: prev?.startedAt ?? Date.now(),
          }));
        }
        if (event.type === "account" && event.result) {
          setResults((prev) => ({ ...prev, [row.id]: event.result as KimiDeployResult }));
          setProgress((prev) =>
            prev
              ? {
                  ...prev,
                  done: event.done ?? prev.done + 1,
                  total: event.total ?? prev.total,
                  message: event.message ?? prev.message,
                }
              : prev
          );
        }
        if (event.type === "done") {
          const first = event.results?.[0];
          if (first) setResults((prev) => ({ ...prev, [row.id]: first }));
          if (first && !first.ok) failed = true;
          setProgress((prev) => (prev ? { ...prev, phase: "done", done: prev.total, message: "Deploy finished." } : prev));
        }
        if (event.type === "error") {
          failed = true;
          setBanner({ tone: "error", text: event.detail || "Approve failed." });
        }
      });
      invalidateAfterDeploy(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["pending-submits"] });
      if (!failed) setBanner({ tone: "success", text: "Deploy finished." });
    } catch (exc) {
      setBanner({ tone: "error", text: exc instanceof Error ? exc.message : "Approve failed." });
    } finally {
      setApprovingId(null);
    }
  }

  const rows = list.data?.requests ?? [];
  const waiting = useMemo(() => rows.filter((row) => row.status === "pending_approval"), [rows]);
  const failedRows = useMemo(() => rows.filter((row) => row.status === "failed"), [rows]);
  const approvedRows = useMemo(() => rows.filter((row) => row.status === "approved"), [rows]);
  const busy = approvingId != null || reject.isPending;
  const empty = !list.isLoading && waiting.length === 0 && failedRows.length === 0 && approvedRows.length === 0;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="gradient-title text-2xl font-semibold tracking-tight">Pending</h1>
        <p className="mt-1 text-sm text-gray-500">
          Approve runs Kimi K3. If /join fails, they can Try again without waiting for Decline. Decline only dismisses the error
          card. Network blips are not stored.
        </p>
      </div>
      {banner && <Banner tone={banner.tone}>{banner.text}</Banner>}
      {progress && approvingId != null && (
        <ProgressBar
          progress={progress}
          elapsedMs={Math.max(0, now - progress.startedAt)}
          failed={Object.values(results).filter((item) => !item.ok).length}
        />
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
              <div className="flex items-center gap-2">
                <AlertTriangle size={16} className="text-red-400" />
                <h2 className="text-sm font-semibold text-gray-100">Join / account errors</h2>
                <Badge tone="error">{failedRows.length}</Badge>
              </div>
              <p className="text-xs text-gray-500">
                Azure or identity failures from /join or Approve. Retry deploy if the monitor identity is still stored.
                Otherwise the user can reapply at /join. Decline removes this card.
              </p>
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {failedRows.map((row) => (
                  <SubmitCard
                    key={row.id}
                    row={row}
                    deploy={results[row.id]}
                    live={approvingId === row.id}
                    busy={busy}
                    declining={reject.isPending && reject.variables === row.id}
                    onDecline={() => reject.mutate(row.id)}
                    onApprove={row.can_retry_deploy ? () => void approve(row) : undefined}
                  />
                ))}
              </div>
            </section>
          )}
          <section className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-gray-100">Ready to approve</h2>
              <Badge tone="info">{waiting.length}</Badge>
            </div>
            {waiting.length === 0 ? (
              <p className="text-xs text-gray-500">No submissions waiting for K3 deploy.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {waiting.map((row) => (
                  <SubmitCard
                    key={row.id}
                    row={row}
                    deploy={results[row.id]}
                    live={approvingId === row.id}
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
                    deploy={results[row.id]}
                    live={false}
                    busy={busy}
                    declining={false}
                    onDecline={() => undefined}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
