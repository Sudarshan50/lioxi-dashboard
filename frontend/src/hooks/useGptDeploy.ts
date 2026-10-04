import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { GptAccountRow, GptAvailability, GptJob, GptLogList } from "@/types";

export function useGptAccounts() {
  return useQuery({
    queryKey: ["gpt-accounts"],
    queryFn: async () => (await apiClient.get<GptAccountRow[]>("/api/gpt-deploy/accounts")).data,
  });
}

export function useGptAvailability(accountId: number | null) {
  return useQuery({
    queryKey: ["gpt-availability", accountId],
    enabled: accountId != null,
    queryFn: async () =>
      (await apiClient.get<GptAvailability>(`/api/gpt-deploy/accounts/${accountId}/availability`)).data,
  });
}

export function useGptJob() {
  return useQuery({
    queryKey: ["gpt-job"],
    queryFn: async () => (await apiClient.get<GptJob>("/api/gpt-deploy/jobs/current")).data,
    refetchInterval: (query) => (query.state.data?.running ? 2000 : 10000),
  });
}

export function useGptLogs(accountId: number | null, running: boolean) {
  return useQuery({
    queryKey: ["gpt-logs", accountId],
    queryFn: async () => {
      const params = accountId != null ? { account_id: accountId } : {};
      return (await apiClient.get<GptLogList>("/api/gpt-deploy/logs", { params })).data;
    },
    refetchInterval: running ? 2000 : 15000,
  });
}

export function useStartGptDeploy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { account_ids: number[]; stack: boolean; models: string[] }) =>
      (await apiClient.post<GptJob>("/api/gpt-deploy/jobs", payload)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["gpt-job"] });
      void queryClient.invalidateQueries({ queryKey: ["gpt-logs"] });
    },
  });
}
