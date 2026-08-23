import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { MonitoredModel } from "@/types";

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: async () => (await apiClient.get<MonitoredModel[]>("/api/models")).data,
  });
}

export function useCreateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => (await apiClient.post<MonitoredModel>("/api/models", payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["registered-models"] });
    },
  });
}

export function useUpdateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: Record<string, unknown> }) =>
      (await apiClient.patch<MonitoredModel>(`/api/models/${id}`, payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["models"] }),
  });
}

export function useDeleteModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (modelId: number) => apiClient.delete(`/api/models/${modelId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["models"] }),
  });
}
