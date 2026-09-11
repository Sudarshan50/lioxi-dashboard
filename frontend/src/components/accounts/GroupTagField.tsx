import { JOIN_GROUPS, JoinGroup, groupLabel, normalizeGroup } from "@/lib/joinGroup";

interface GroupTagFieldProps {
  value: JoinGroup;
  onChange: (value: JoinGroup) => void;
  id?: string;
  compact?: boolean;
}

/** Which Join group an account belongs to. Drives the NewAPI channel series. */
export default function GroupTagField({
  value,
  onChange,
  id = "group-tag",
  compact = false,
}: GroupTagFieldProps) {
  return (
    <div className="flex min-w-0 w-full flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-gray-400">
        Join group
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(normalizeGroup(event.target.value))}
        className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent"
      >
        {JOIN_GROUPS.map((option) => (
          <option key={option} value={option}>
            {groupLabel(option)}
          </option>
        ))}
      </select>
      {!compact && (
        <p className="-mt-0.5 text-xs text-gray-500">
          SB uses kimi-k3-500k-proxy channels; VCS uses cs-proxy.
        </p>
      )}
    </div>
  );
}
