import Badge from "@/components/ui/Badge";
import { groupLabel, isVcs, resolveGroup } from "@/lib/joinGroup";

interface GroupBadgeProps {
  /** Explicit group_tag when the row has one. */
  group?: string | null;
  /** NewAPI channel name, used when the row carries no group_tag. */
  channel?: string | null;
  className?: string;
}

/**
 * Shows which Join group a row belongs to. Both groups are labelled: SB in a
 * quiet neutral chip, VCS in amber so it stands out against a mostly-SB fleet.
 */
export default function GroupBadge({ group, channel, className }: GroupBadgeProps) {
  const resolved = resolveGroup(group, channel);
  return (
    <Badge
      tone={isVcs(resolved) ? "warning" : "neutral"}
      className={`shrink-0 ${className ?? ""}`}
      title="Join group"
    >
      {groupLabel(resolved)}
    </Badge>
  );
}
