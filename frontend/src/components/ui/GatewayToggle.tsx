import clsx from "clsx";

export type GatewayView = "ALL" | "O1" | "O2";

interface GatewayToggleProps {
  value: GatewayView;
  onChange: (value: GatewayView) => void;
  className?: string;
}

const LABELS: Record<GatewayView, string> = { ALL: "Combined", O1: "O1", O2: "O2" };

export default function GatewayToggle({ value, onChange, className }: GatewayToggleProps) {
  return (
    <div className={clsx("inline-flex rounded-lg border border-surface-border bg-surface p-0.5", className)}>
      {(["ALL", "O1", "O2"] as const).map((view) => (
        <button
          key={view}
          type="button"
          onClick={() => onChange(view)}
          className={clsx(
            "rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
            value === view ? "bg-accent text-white" : "text-gray-400 hover:text-gray-200"
          )}
        >
          {LABELS[view]}
        </button>
      ))}
    </div>
  );
}
