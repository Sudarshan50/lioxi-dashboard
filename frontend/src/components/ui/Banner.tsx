import clsx from "clsx";
import { ReactNode } from "react";

type Tone = "success" | "error";

const toneClasses: Record<Tone, string> = {
  success: "border-emerald-500/20 bg-emerald-500/10 text-emerald-300",
  error: "border-red-500/20 bg-red-500/10 text-red-300",
};

export default function Banner({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <div className={clsx("rounded-xl border px-3.5 py-2.5 text-xs leading-relaxed", toneClasses[tone])}>{children}</div>
  );
}
