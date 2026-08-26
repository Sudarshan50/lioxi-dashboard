import clsx from "clsx";

import { UNTAGGED_OWNER } from "@/lib/ownerTag";

interface OwnerChipsProps {
  owners: string[];
  counts: Map<string, number>;
  untagged: number;
  value: string | null;
  onChange: (owner: string | null) => void;
}

export default function OwnerChips({ owners, counts, untagged, value, onChange }: OwnerChipsProps) {
  if (owners.length === 0 && untagged === 0) return null;

  function chip(key: string, label: string, count: number) {
    const selected = value === key;
    return (
      <button
        key={key}
        type="button"
        onClick={() => onChange(selected ? null : key)}
        className={clsx(
          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
          selected
            ? "border-violet-400/40 bg-violet-500/15 text-violet-200"
            : "border-white/[0.08] bg-black/20 text-gray-400 hover:border-white/[0.14] hover:text-gray-200"
        )}
      >
        <span className="max-w-[10rem] truncate">{label}</span>
        <span className="tabular-nums text-gray-500">{count}</span>
      </button>
    );
  }

  return (
    <div className="mt-4 border-t border-white/[0.06] pt-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-gray-400">Tags</p>
        {value && (
          <button type="button" onClick={() => onChange(null)} className="text-xs text-gray-500 hover:text-gray-200">
            Clear tag
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {owners.map((owner) => chip(owner, owner, counts.get(owner) ?? 0))}
        {untagged > 0 && chip(UNTAGGED_OWNER, "Untagged", untagged)}
      </div>
    </div>
  );
}
