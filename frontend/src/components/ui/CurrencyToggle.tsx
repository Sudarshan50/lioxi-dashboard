import clsx from "clsx";

import { EstimateCurrency } from "@/lib/format";

interface CurrencyToggleProps {
  value: EstimateCurrency;
  onChange: (value: EstimateCurrency) => void;
  className?: string;
}

export default function CurrencyToggle({ value, onChange, className }: CurrencyToggleProps) {
  return (
    <div className={clsx("inline-flex rounded-lg border border-surface-border bg-surface p-0.5", className)}>
      {(["USD", "INR"] as const).map((code) => (
        <button
          key={code}
          type="button"
          onClick={() => onChange(code)}
          className={clsx(
            "rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
            value === code ? "bg-accent text-white" : "text-gray-400 hover:text-gray-200"
          )}
        >
          {code}
        </button>
      ))}
    </div>
  );
}
