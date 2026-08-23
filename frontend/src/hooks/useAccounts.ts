import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import apiClient from "@/lib/apiClient";
import { Account, Deployment, DiscoveredResource, SyncAccountResult, SyncAllResult } from "@/types";

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await apiClient.get<Account[]>("/api/accounts")).data,
  });
}

export function useDiscoverResources() {
  return useMutation({
    mutationFn: async (payload: { tenant_id: string; client_id: string; client_secret: string; subscription_id: string }) =>
      (await apiClient.post<DiscoveredResource[]>("/api/accounts/discover", payload)).data,
  });
}

export function useDiscoverDeployments() {
  return useMutation({
    mutationFn: async (payload: {
      tenant_id: string;
      client_id: string;
      client_secret: string;
      subscription_id: string;
      resource_id: string;
    }) => (await apiClient.post<Deployment[]>("/api/accounts/discover-deployments", payload)).data,
  });
}

export function useDiscoverAccountResources() {
  return useMutation({
    mutationFn: async (accountId: number) =>
      (await apiClient.post<DiscoveredResource[]>(`/api/accounts/${accountId}/discover`)).data,
  });
}

export function useDiscoverAccountDeployments() {
  return useMutation({
    mutationFn: async ({ accountId, resourceId }: { accountId: number; resourceId: string }) =>
      (await apiClient.post<Deployment[]>(`/api/accounts/${accountId}/discover-deployments`, { resource_id: resourceId })).data,
  });
}

export function useCreateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => (await apiClient.post<Account>("/api/accounts", payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useUpdateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      accountId,
      ...payload
    }: {
      accountId: number;
      name?: string;
      resource_id?: string;
      resource_group?: string;
      resource_name?: string;
      endpoint?: string;
      kind?: string;
      location?: string;
    }) => (await apiClient.patch<Account>(`/api/accounts/${accountId}`, payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useDeleteAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (accountId: number) => apiClient.delete(`/api/accounts/${accountId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useTestAccount() {
  return useMutation({
    mutationFn: async (accountId: number) => (await apiClient.post(`/api/accounts/${accountId}/test`)).data,
  });
}

export function useSyncAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (accountId: number) => (await apiClient.post<SyncAccountResult>(`/api/accounts/${accountId}/sync`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useSetGatewayStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ accountId, enable, gateway }: { accountId: number; enable: boolean; gateway?: "O1" | "O2" }) =>
      (
        await apiClient.post<{ status: string; flipped: Record<string, number[]>; errors: Record<string, string> }>(
          `/api/accounts/${accountId}/gateway-${enable ? "enable" : "disable"}`,
          null,
          { params: gateway ? { gateway } : undefined }
        )
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useSyncAccounts() {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);

  async function syncAccounts(accountIds: number[]): Promise<SyncAllResult> {
    const total = accountIds.length;
    if (total === 0) return { status: "completed", synced: 0, failed: [] };

    setProgress({ current: 0, total });
    const failed: SyncAllResult["failed"] = [];
    let synced = 0;
    let completed = 0;
    try {
      // Azure credits first so the following NewAPI alert/auto-disable pass
      // sees this run's balances, not the previous cycle.
      await Promise.all(
        accountIds.map(async (accountId) => {
          try {
            const result = (await apiClient.post<SyncAccountResult>(`/api/accounts/${accountId}/sync`)).data;
            if (result.status === "error" || result.status === "missing") {
              failed.push({ id: result.id, name: result.name, error: result.error });
            } else {
              synced += 1;
            }
          } catch (err: any) {
            failed.push({
              id: accountId,
              name: null,
              error: err?.response?.data?.detail ?? "Sync failed.",
            });
          } finally {
            completed += 1;
            setProgress({ current: completed, total });
          }
        })
      );
      await apiClient.post("/api/accounts/newapi-sync").catch(() => undefined);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
      return {
        status: failed.length === 0 ? "completed" : synced > 0 ? "partial" : "error",
        synced,
        failed,
      };
    } finally {
      setProgress(null);
    }
  }

  return { syncAccounts, progress, isSyncing: progress !== null };
}

export function useSyncAllAccounts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<SyncAllResult>("/api/accounts/sync-all")).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useAccountDeployments(accountId: number | null) {
  return useQuery({
    queryKey: ["deployments", accountId],
    queryFn: async () => (await apiClient.get<Deployment[]>(`/api/accounts/${accountId}/deployments`)).data,
    enabled: accountId !== null,
  });
}
