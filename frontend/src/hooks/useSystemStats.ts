import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { SystemStats } from "@/types";

export function useSystemStats() {
  return useQuery({
    queryKey: ["system", "stats"],
    queryFn: async () => (await apiClient.get<SystemStats>("/api/system/stats")).data,
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}
