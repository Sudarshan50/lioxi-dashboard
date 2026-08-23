import clsx from "clsx";
import { ReactNode } from "react";

import Card from "./Card";

type Tone = "indigo" | "emerald" | "sky" | "amber" | "violet" | "rose";

const toneClasses: Record<Tone, string> = {
  indigo: "bg-gradient-to-br from-indigo-500/25 to-indigo-500/5 text-indigo-300 ring-indigo-500/30",
  emerald: "bg-gradient-to-br from-emerald-500/25 to-emerald-500/5 text-emerald-300 ring-emerald-500/30",
  sky: "bg-gradient-to-br from-sky-500/25 to-sky-500/5 text-sky-300 ring-sky-500/30",
  amber: "bg-gradient-to-br from-amber-500/25 to-amber-500/5 text-amber-300 ring-amber-500/30",
  violet: "bg-gradient-to-br from-violet-500/25 to-violet-500/5 text-violet-300 ring-violet-500/30",
  rose: "bg-gradient-to-br from-rose-500/25 to-rose-500/5 text-rose-300 ring-rose-500/30",
};

const accentBar: Record<Tone, string> = {
  indigo: "from-indigo-400/60",
  emerald: "from-emerald-400/60",
  sky: "from-sky-400/60",
  amber: "from-amber-400/60",
  violet: "from-violet-400/60",
  rose: "from-rose-400/60",
};

interface StatCardProps {
  label: string;
  value: string;
  icon?: ReactNode;
  hint?: string;
  tone?: Tone;
  action?: ReactNode;
}

export default function StatCard({ label, value, icon, hint, tone = "indigo", action }: StatCardProps) {
  return (
    <Card className="group flex items-start justify-between gap-3 overflow-hidden">
      <div
        className={clsx(
          "pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r to-transparent opacity-70",
          accentBar[tone]
        )}
      />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[11px] font-medium uppercase tracking-wider text-gray-500">{label}</p>
          {action}
        </div>
        <p
          className="mt-2 truncate text-xl font-semibold tracking-tight text-gray-50 tabular-nums sm:text-2xl"
          title={value}
        >
          {value}
        </p>
        {hint && (
          <p className="mt-1 truncate text-xs text-gray-500" title={hint}>
            {hint}
          </p>
        )}
      </div>
      {icon && (
        <div
          className={clsx(
            "shrink-0 rounded-xl p-2.5 ring-1 transition-transform duration-200 group-hover:scale-105",
            toneClasses[tone]
          )}
        >
          {icon}
        </div>
      )}
    </Card>
  );
}
