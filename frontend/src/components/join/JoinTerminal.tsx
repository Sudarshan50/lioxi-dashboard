import { useEffect, useRef } from "react";

export type JoinTermLine = {
  kind: "cmd" | "out" | "err" | "ok";
  text: string;
};

function tone(kind: JoinTermLine["kind"]) {
  if (kind === "cmd") return "text-sky-300";
  if (kind === "err") return "text-rose-300";
  if (kind === "ok") return "text-emerald-400";
  return "text-zinc-400";
}

export default function JoinTerminal({
  lines,
  waiting,
}: {
  lines: JoinTermLine[];
  waiting?: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [lines, waiting]);

  return (
    <div className="join-term overflow-hidden rounded-xl border border-white/[0.1] bg-[#0b0d14] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-1.5">
        <span className="h-2 w-2 rounded-full bg-rose-400/80" />
        <span className="h-2 w-2 rounded-full bg-amber-300/80" />
        <span className="h-2 w-2 rounded-full bg-emerald-400/80" />
        <span className="ml-1 font-mono text-[10px] uppercase tracking-wide text-zinc-500">az</span>
      </div>
      <div
        ref={scroller}
        className="join-term-scroll h-40 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-5"
      >
        {lines.length === 0 && (
          <p className="text-zinc-600">$ waiting for Azure CLI…</p>
        )}
        {lines.map((line, index) => (
          <p key={`${index}-${line.text.slice(0, 24)}`} className={`break-all ${tone(line.kind)}`}>
            {line.text}
          </p>
        ))}
        {waiting && <span className="join-term-cursor inline-block h-4 w-1.5 align-middle bg-zinc-300" />}
      </div>
    </div>
  );
}
