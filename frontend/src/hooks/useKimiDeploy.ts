import { useMutation, useQueries, useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { KimiDeleteResponse, KimiDeployResponse, KimiDeployStatus, KimiRegenerateResponse, KimiTestResponse } from "@/types";

const DEPLOY_TIMEOUT_MS = 45 * 60 * 1000;
const KEYS_TIMEOUT_MS = 15 * 60 * 1000;
const LOOKUP_TIMEOUT_MS = 120000;

function accountFingerprint(account: Record<string, string>) {
  return `${account.AZURE_CLIENT_ID}:${account.AZURE_SUBSCRIPTION_ID}:${(account.AZURE_CLIENT_SECRET || "").slice(-4)}`;
}

export function useKimiDeployStatus() {
  return useQuery({
    queryKey: ["kimi-deploy-status"],
    queryFn: async () => (await apiClient.get<KimiDeployStatus>("/api/kimi-deploy/status")).data,
  });
}

export function useKimiDeploy() {
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[]; jobs: number }) =>
      (await apiClient.post<KimiDeployResponse>("/api/kimi-deploy", payload, { timeout: DEPLOY_TIMEOUT_MS })).data,
  });
}

export function useKimiRegenerateKeys() {
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[]; jobs?: number }) =>
      (
        await apiClient.post<KimiRegenerateResponse>("/api/kimi-deploy/regenerate-keys", payload, {
          timeout: KEYS_TIMEOUT_MS,
        })
      ).data,
  });
}

export function useKimiUndeploy() {
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[]; jobs?: number }) =>
      (
        await apiClient.post<KimiDeleteResponse>("/api/kimi-deploy/undeploy", payload, {
          timeout: KEYS_TIMEOUT_MS,
        })
      ).data,
  });
}

export function useKimiTestModel() {
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[] }) =>
      (await apiClient.post<KimiTestResponse>("/api/kimi-deploy/test", payload, { timeout: LOOKUP_TIMEOUT_MS })).data,
  });
}

export function useKimiInventory(accounts: Record<string, string>[], enabled: boolean) {
  const queries = useQueries({
    queries: accounts.map((account) => ({
      queryKey: ["kimi-inventory", accountFingerprint(account)],
      queryFn: async () =>
        (
          await apiClient.post<KimiDeployResponse>(
            "/api/kimi-deploy/inventory",
            { accounts: [account] },
            { timeout: LOOKUP_TIMEOUT_MS }
          )
        ).data,
      enabled: enabled && Boolean(account.AZURE_CLIENT_ID),
      staleTime: 30_000,
      retry: 1,
    })),
  });

  return {
    queries,
    isFetching: queries.some((query) => query.isFetching),
    isError: queries.length > 0 && queries.every((query) => query.isError),
    refetch: () => Promise.all(queries.map((query) => query.refetch())),
  };
}
