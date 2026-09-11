export type JoinProgressValue = { done: number; total: number };

export default function JoinProgress({ label, value }: { label: string; value: JoinProgressValue }) {
  const total = Math.max(value.total, 1);
  const done = Math.min(Math.max(value.done, 0), total);
  const percent = Math.round((done / total) * 100);
  const complete = done >= total;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-white/[0.08] bg-surface-raised/60 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="truncate text-xs font-medium text-gray-300">{label}</p>
        <p className="shrink-0 font-mono text-[11px] tabular-nums text-gray-500">
          {done}/{total}
        </p>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="relative h-2 overflow-hidden rounded-full bg-white/[0.06] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]"
      >
        <div
          className={`relative h-full rounded-full transition-[width] duration-500 ease-out ${
            complete ? "bg-emerald-400/90" : "bg-accent-gradient shadow-glow-sm"
          }`}
          style={{ width: `${percent}%` }}
        >
          {!complete && percent > 0 && (
            <span className="absolute inset-y-0 left-0 w-1/3 animate-bar-shimmer bg-gradient-to-r from-transparent via-white/35 to-transparent" />
          )}
        </div>
      </div>
      <p className={`text-[11px] ${complete ? "text-emerald-300/90" : "text-gray-500"}`}>
        {complete ? "All permissions granted." : `${percent}% of Azure permissions granted`}
      </p>
    </div>
  );
}
