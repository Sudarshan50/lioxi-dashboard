import clsx from "clsx";

import { JOIN_GROUPS, JoinGroup, groupLabel } from "@/lib/joinGroup";

interface GroupChipsProps {
  counts: Map<string, number>;
  total: number;
  value: JoinGroup | null;
  onChange: (group: JoinGroup | null) => void;
  /** Hide entirely until a second group actually exists. */
  hideWhenSingle?: boolean;
}

/** SB / VCS filter, styled to match OwnerChips. */
export default function GroupChips({
  counts,
  total,
  value,
  onChange,
  hideWhenSingle = false,
}: GroupChipsProps) {
  const present = JOIN_GROUPS.filter((group) => (counts.get(group) ?? 0) > 0);
  if (hideWhenSingle && present.length < 2) return null;

  function chip(key: JoinGroup | null, label: string, count: number) {
    const selected = value === key;
    return (
      <button
        key={key ?? "all"}
        type="button"
        onClick={() => onChange(selected ? null : key)}
        className={clsx(
          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
          selected
            ? "border-amber-400/40 bg-amber-500/15 text-amber-200"
            : "border-white/[0.08] bg-black/20 text-gray-400 hover:border-white/[0.14] hover:text-gray-200"
        )}
      >
        <span>{label}</span>
        <span className="tabular-nums text-gray-500">{count}</span>
      </button>
    );
  }

  return (
    <div className="mt-4 border-t border-white/[0.06] pt-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-gray-400">Group</p>
        {value && (
          <button type="button" onClick={() => onChange(null)} className="text-xs text-gray-500 hover:text-gray-200">
            Clear group
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {chip(null, "All", total)}
        {JOIN_GROUPS.map((group) => chip(group, groupLabel(group), counts.get(group) ?? 0))}
      </div>
    </div>
  );
}
