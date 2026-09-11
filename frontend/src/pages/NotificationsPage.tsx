import { Bell } from "lucide-react";

import Badge from "@/components/ui/Badge";
import GroupBadge from "@/components/ui/GroupBadge";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Spinner from "@/components/ui/Spinner";
import { useNotifications } from "@/hooks/useNotifications";
import { formatRelative } from "@/lib/format";

export default function NotificationsPage() {
  const feed = useNotifications();
  const items = feed.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-50">Notifications</h1>
        <p className="mt-1 text-sm text-gray-500">
          Auto TPM/RPM upgrades from Azure sync on NewAPI-enabled Deploy K3 accounts.
        </p>
      </div>
      {feed.isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Bell size={22} className="text-gray-500" />}
          title="No upgrades logged"
          description="When Azure sync raises TPM/RPM on an enabled K3 channel, it shows up here."
        />
      ) : (
        <div className="space-y-3">
          {items.map((row) => (
            <Card key={row.id} className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate font-medium text-gray-100">{row.email || "No email"}</p>
                  {row.owner_tag && <Badge tone="info">{row.owner_tag}</Badge>}
                  <GroupBadge channel={row.new_api_name} />
                  <Badge tone={row.status === "ok" ? "success" : "error"}>{row.status === "ok" ? "upgraded" : "failed"}</Badge>
                </div>
                <p className="text-sm text-gray-300">{row.detail}</p>
                <p className="truncate text-xs text-gray-500">
                  {[row.account_name, row.resource_name, row.new_api_name].filter(Boolean).join(" · ") || "—"}
                </p>
                {row.error && <p className="text-xs text-red-300">{row.error}</p>}
              </div>
              <p className="shrink-0 text-xs text-gray-500">{formatRelative(row.created_at)}</p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
