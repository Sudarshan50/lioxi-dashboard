import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { AlertConfig, AlertStateItem, AlertStatus } from "@/types";

export function useAlertStatus() {
  return useQuery({
    queryKey: ["alerts", "status"],
    queryFn: async () => (await apiClient.get<AlertStatus>("/api/alerts/status")).data,
  });
}

export function useAlertConfig() {
  return useQuery({
    queryKey: ["alerts", "config"],
    queryFn: async () => (await apiClient.get<AlertConfig>("/api/alerts/config")).data,
  });
}

export function useSaveAlertConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (config: AlertConfig) => (await apiClient.put<AlertConfig>("/api/alerts/config", config)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export function useAlertState() {
  return useQuery({
    queryKey: ["alerts", "state"],
    queryFn: async () => (await apiClient.get<AlertStateItem[]>("/api/alerts/state")).data,
  });
}

export function useSendTestAlert() {
  return useMutation({
    mutationFn: async () => (await apiClient.post<{ status: string }>("/api/alerts/test")).data,
  });
}

export function useSetPayableSettled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, settled }: { id: number; settled: boolean }) =>
      (await apiClient.patch<{ id: number; payable_settled: boolean; payable_settled_at: string | null }>(
        `/api/alerts/state/${id}/settled`,
        { settled }
      )).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts", "state"] });
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useSetAtCapManual() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, atCap }: { id: number; atCap: boolean }) =>
      (await apiClient.patch<{ id: number; at_cap_manual: boolean }>(`/api/alerts/state/${id}/at-cap`, {
        at_cap: atCap,
      })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts", "state"] });
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useRunAlertCheck() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<{ sent: number; skipped?: string }>("/api/alerts/check")).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts", "state"] });
    },
  });
}
