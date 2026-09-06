import clsx from "clsx";

import { useSystemStats } from "@/hooks/useSystemStats";
import { SystemStats } from "@/types";

function formatGiB(bytes: number): string {
  const gib = bytes / 1024 ** 3;
  if (gib >= 10) return `${gib.toFixed(0)} GB`;
  if (gib >= 1) return `${gib.toFixed(1)} GB`;
  const mib = bytes / 1024 ** 2;
  return `${mib.toFixed(0)} MB`;
}

function barTone(percent: number): string {
  if (percent >= 90) return "bg-rose-400";
  if (percent >= 80) return "bg-amber-400";
  return "bg-emerald-400";
}

function textTone(percent: number): string {
  if (percent >= 90) return "text-rose-300";
  if (percent >= 80) return "text-amber-300";
  return "text-gray-300";
}

function Meter({
  label,
  percent,
  detail,
}: {
  label: string;
  percent: number | null;
  detail: string;
}) {
  const value = percent ?? 0;
  const ready = percent != null;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wider text-gray-500">{label}</span>
        <span className={clsx("text-[11px] font-semibold tabular-nums", ready ? textTone(value) : "text-gray-600")}>
          {ready ? `${Math.round(value)}%` : "—"}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className={clsx("h-full rounded-full transition-[width] duration-500", ready ? barTone(value) : "bg-white/10")}
          style={{ width: `${ready ? Math.min(100, value) : 0}%` }}
        />
      </div>
      <p className="truncate text-[10px] tabular-nums text-gray-500" title={detail}>
        {detail}
      </p>
    </div>
  );
}

function cpuDetail(stats: SystemStats): string {
  const load = `load ${stats.load_avg_1.toFixed(2)}`;
  return stats.cpu_count > 1 ? `${load} · ${stats.cpu_count} cores` : load;
}

export default function SystemStatsPanel() {
  const query = useSystemStats();
  const stats = query.data;
  const storagePercent = stats?.storage.percent ?? 0;
  const critical = storagePercent >= 90;
  const warning = storagePercent >= 80;

  return (
    <div
      className={clsx(
        "mb-3 rounded-xl px-3 py-3",
        critical
          ? "bg-rose-500/10 shadow-[inset_0_0_0_1px_rgba(244,63,94,0.35)]"
          : warning
            ? "bg-amber-500/10 shadow-[inset_0_0_0_1px_rgba(245,158,11,0.3)]"
            : "bg-white/[0.03] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)]"
      )}
    >
      <div className="mb-2.5 flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">Host</p>
        {critical ? (
          <p className="text-[10px] font-semibold text-rose-300">Disk almost full</p>
        ) : warning ? (
          <p className="text-[10px] font-semibold text-amber-300">Disk running low</p>
        ) : null}
      </div>
      <div className="space-y-2.5">
        <Meter
          label="CPU"
          percent={stats?.cpu_percent ?? null}
          detail={stats ? cpuDetail(stats) : query.isError ? "unavailable" : "sampling…"}
        />
        <Meter
          label="Memory"
          percent={stats?.memory.percent ?? null}
          detail={
            stats
              ? `${formatGiB(stats.memory.used_bytes)} / ${formatGiB(stats.memory.total_bytes)}`
              : query.isError
                ? "unavailable"
                : "…"
          }
        />
        <Meter
          label="Storage"
          percent={stats?.storage.percent ?? null}
          detail={
            stats
              ? `${formatGiB(stats.storage.used_bytes)} / ${formatGiB(stats.storage.total_bytes)}`
              : query.isError
                ? "unavailable"
                : "…"
          }
        />
      </div>
    </div>
  );
}
