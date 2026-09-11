import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { NotificationListResponse } from "@/types";

export const NOTIFICATIONS_QUERY_KEY = ["notifications"] as const;

export function useNotifications() {
  return useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: async () => (await apiClient.get<NotificationListResponse>("/api/notifications")).data,
  });
}
