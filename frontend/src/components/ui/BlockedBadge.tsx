import Badge from "@/components/ui/Badge";

/** Portal tag for a disabled account that should stay off. */
export default function BlockedBadge({ className }: { className?: string }) {
  return (
    <Badge tone="error" className={className} title="Blocked — will not be enabled until unblocked on Alerts">
      blocked
    </Badge>
  );
}
