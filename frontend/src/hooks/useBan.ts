import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { GROUP_VCS, JoinGroup, normalizeGroup } from "@/lib/joinGroup";
import { BanAutoApproveResponse, BanSettings, JoinEnrollee } from "@/types";

export const BAN_QUERY_KEY = ["ban-settings"] as const;

export function useBanSettings() {
  return useQuery({
    queryKey: BAN_QUERY_KEY,
    queryFn: async () => (await apiClient.get<BanSettings>("/api/ban")).data,
  });
}

type AutoApproveVars = { enabled: boolean; group: JoinGroup };

function autoApproveField(group: JoinGroup): "auto_approve" | "auto_approve_vcs" {
  return group === GROUP_VCS ? "auto_approve_vcs" : "auto_approve";
}

export function useSetAutoApprove() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ enabled, group }: AutoApproveVars) =>
      (await apiClient.put<BanAutoApproveResponse>("/api/ban/auto-approve", { enabled, group })).data,
    onMutate: async ({ enabled, group }) => {
      await queryClient.cancelQueries({ queryKey: BAN_QUERY_KEY });
      const previous = queryClient.getQueryData<BanSettings>(BAN_QUERY_KEY);
      if (previous) {
        queryClient.setQueryData(BAN_QUERY_KEY, { ...previous, [autoApproveField(group)]: enabled });
      }
      return { previous };
    },
    onError: (_exc, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(BAN_QUERY_KEY, context.previous);
    },
    onSuccess: (data) => {
      queryClient.setQueryData<BanSettings>(BAN_QUERY_KEY, (current) =>
        current ? { ...current, [autoApproveField(normalizeGroup(data.group))]: data.auto_approve } : current
      );
      void queryClient.invalidateQueries({ queryKey: ["pending-submits"] });
    },
  });
}

export function useCreateEnrollee() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, group }: { name: string; group: JoinGroup }) =>
      (await apiClient.post<JoinEnrollee>("/api/ban/names", { name, group })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: BAN_QUERY_KEY });
    },
  });
}

export function useSetEnrolleeBanned() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, banned }: { id: number; banned: boolean }) =>
      (await apiClient.patch<JoinEnrollee>(`/api/ban/names/${id}`, { banned })).data,
    onSuccess: (row) => {
      queryClient.setQueryData<BanSettings>(BAN_QUERY_KEY, (current) => {
        if (!current) return current;
        return { ...current, names: current.names.map((item) => (item.id === row.id ? row : item)) };
      });
    },
  });
}

