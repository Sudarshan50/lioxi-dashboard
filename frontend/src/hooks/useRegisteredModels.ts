import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { RegisteredModel } from "@/types";

export function useRegisteredModels() {
  return useQuery({
    queryKey: ["registered-models"],
    queryFn: async () => (await apiClient.get<RegisteredModel[]>("/api/registered-models")).data,
  });
}

export function useRegisterModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) =>
      (await apiClient.post<RegisteredModel>("/api/registered-models", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["registered-models"] }),
  });
}

export function useUpdateRegisteredModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: Record<string, unknown> }) =>
      (await apiClient.patch<RegisteredModel>(`/api/registered-models/${id}`, payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registered-models"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useDeleteRegisteredModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => apiClient.delete(`/api/registered-models/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["registered-models"] }),
  });
}
