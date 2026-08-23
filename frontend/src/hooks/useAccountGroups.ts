import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { AccountGroup } from "@/types";

export function useAccountGroups() {
  return useQuery({
    queryKey: ["account-groups"],
    queryFn: async () => (await apiClient.get<AccountGroup[]>("/api/account-groups")).data,
  });
}

export function useCreateAccountGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { name: string; account_ids: number[] }) =>
      (await apiClient.post<AccountGroup>("/api/account-groups", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["account-groups"] }),
  });
}

export function useUpdateAccountGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: { name?: string; account_ids?: number[] } }) =>
      (await apiClient.patch<AccountGroup>(`/api/account-groups/${id}`, payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["account-groups"] }),
  });
}

export function useDeleteAccountGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => apiClient.delete(`/api/account-groups/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["account-groups"] }),
  });
}
