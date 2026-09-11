import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { JoinGroup } from "@/lib/joinGroup";
import { AlertConfig, AlertStateItem, AlertStatus, ClearGroupChatStatus } from "@/types";

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

export type SendTarget = "sb" | "vcs" | "both";

type SendResult = { status: string; sent: string[]; errors: Record<string, string> };

export function useSendTestAlert() {
  return useMutation({
    mutationFn: async (target: SendTarget = "sb") =>
      (await apiClient.post<SendResult>(`/api/alerts/test?target=${target}`)).data,
  });
}

export function useSendGroupMessage() {
  return useMutation({
    mutationFn: async ({ text, target }: { text: string; target: SendTarget }) =>
      (await apiClient.post<SendResult>("/api/alerts/message", { text, target })).data,
  });
}

// Clear jobs are per group, so the cache key carries the group.
export function useClearGroupChatStatus(group: JoinGroup) {
  return useQuery({
    queryKey: ["alerts", "clear-chats", group],
    queryFn: async () =>
      (await apiClient.get<ClearGroupChatStatus>(`/api/alerts/clear-chats?group=${group}`)).data,
    refetchInterval: (query) => (query.state.data?.running ? 1000 : false),
  });
}

export function useStartClearGroupChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (group: JoinGroup) =>
      (await apiClient.post<ClearGroupChatStatus>(`/api/alerts/clear-chats?group=${group}`)).data,
    onSuccess: (data, group) => {
      queryClient.setQueryData(["alerts", "clear-chats", group], data);
      queryClient.invalidateQueries({ queryKey: ["alerts", "clear-chats", group] });
    },
  });
}

export function useCancelClearGroupChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (group: JoinGroup) =>
      (await apiClient.delete<ClearGroupChatStatus>(`/api/alerts/clear-chats?group=${group}`)).data,
    onSuccess: (data, group) => {
      queryClient.setQueryData(["alerts", "clear-chats", group], data);
      queryClient.invalidateQueries({ queryKey: ["alerts", "clear-chats", group] });
    },
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
