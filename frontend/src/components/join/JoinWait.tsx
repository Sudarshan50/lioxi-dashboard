import { useId } from "react";

const MOTES = [
  { top: "12%", left: "22%", delay: "0s", size: "h-1 w-1" },
  { top: "18%", left: "72%", delay: "0.7s", size: "h-1.5 w-1.5" },
  { top: "68%", left: "14%", delay: "1.4s", size: "h-1 w-1" },
  { top: "74%", left: "78%", delay: "0.35s", size: "h-1 w-1" },
  { top: "42%", left: "8%", delay: "1.1s", size: "h-[3px] w-[3px]" },
  { top: "38%", left: "88%", delay: "1.8s", size: "h-1 w-1" },
];

export default function JoinWait({ label }: { label: string }) {
  const gid = useId().replace(/:/g, "");

  return (
    <div className="join-wait flex flex-col items-center gap-6 py-3">
      <div className="relative h-40 w-40">
        <div className="join-wait-bloom pointer-events-none absolute inset-[-32%] rounded-full" />

        <div className="absolute inset-0 rounded-full border border-white/[0.12] bg-gradient-to-b from-white/[0.07] to-white/[0.015] shadow-[inset_0_1px_0_rgba(255,255,255,0.16)] backdrop-blur-[2px]" />

        <svg className="join-wait-arc absolute inset-[10%] h-[80%] w-[80%]" viewBox="0 0 100 100" aria-hidden>
          <defs>
            <linearGradient id={`${gid}-arc`} x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="rgba(165,180,252,0)" />
              <stop offset="35%" stopColor="rgba(165,180,252,0.95)" />
              <stop offset="70%" stopColor="rgba(192,132,252,0.7)" />
              <stop offset="100%" stopColor="rgba(56,189,248,0)" />
            </linearGradient>
            <linearGradient id={`${gid}-arc-soft`} x1="100%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="rgba(167,139,250,0)" />
              <stop offset="45%" stopColor="rgba(129,140,248,0.55)" />
              <stop offset="100%" stopColor="rgba(255,255,255,0)" />
            </linearGradient>
          </defs>
          <circle
            cx="50"
            cy="50"
            r="46"
            fill="none"
            stroke={`url(#${gid}-arc)`}
            strokeWidth="2.1"
            strokeLinecap="round"
            strokeDasharray="72 220"
          />
        </svg>

        <svg className="join-wait-arc-rev absolute inset-[22%] h-[56%] w-[56%]" viewBox="0 0 100 100" aria-hidden>
          <circle
            cx="50"
            cy="50"
            r="46"
            fill="none"
            stroke={`url(#${gid}-arc-soft)`}
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeDasharray="48 240"
          />
        </svg>

        {MOTES.map((mote) => (
          <span
            key={`${mote.top}-${mote.left}`}
            className={`join-wait-mote absolute rounded-full bg-indigo-100/90 shadow-[0_0_8px_rgba(199,210,254,0.8)] ${mote.size}`}
            style={{ top: mote.top, left: mote.left, animationDelay: mote.delay }}
          />
        ))}

        <div className="absolute inset-0 flex items-center justify-center">
          <div className="join-wait-core relative flex h-[3.75rem] w-[3.75rem] items-center justify-center rounded-full bg-accent-gradient shadow-[0_0_28px_rgba(129,140,248,0.55)]">
            <span className="absolute inset-[18%] rounded-full bg-white/25 blur-[6px]" />
            <span className="relative h-2 w-2 rounded-full bg-white shadow-[0_0_10px_rgba(255,255,255,0.85)]" />
          </div>
        </div>
      </div>

      <div className="flex w-full max-w-[16rem] flex-col items-center gap-2.5">
        <p key={label} className="animate-fade-up px-2 text-center text-sm leading-relaxed text-gray-200">
          {label}
        </p>
        <div className="relative h-[2px] w-36 overflow-hidden rounded-full bg-white/[0.08]">
          <span className="join-wait-sweep absolute inset-y-0 w-2/3 bg-gradient-to-r from-transparent via-indigo-100 to-transparent" />
        </div>
      </div>
    </div>
  );
}
