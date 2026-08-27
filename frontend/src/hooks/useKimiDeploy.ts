import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { KimiDeleteResponse, KimiDeployProgressEvent, KimiDeployResponse, KimiDeployResult, KimiDeployStatus, KimiNewApiAuth, KimiNewApiPool, KimiRegenerateResponse, KimiSheetStatus, KimiSheetSyncResponse, KimiStoredResponse, KimiTestResponse } from "@/types";

const DEPLOY_TIMEOUT_MS = 45 * 60 * 1000;
const KEYS_TIMEOUT_MS = 15 * 60 * 1000;
const LOOKUP_TIMEOUT_MS = 120000;

function accountFingerprint(account: Record<string, string>) {
  return `${account.AZURE_CLIENT_ID || ""}:${account.AZURE_SUBSCRIPTION_ID}`;
}

export function useKimiStoredAccounts() {
  return useQuery({
    queryKey: ["kimi-stored-accounts"],
    queryFn: async () => (await apiClient.get<KimiStoredResponse>("/api/kimi-deploy/stored")).data,
  });
}

export function useKimiDeployStatus() {
  return useQuery({
    queryKey: ["kimi-deploy-status"],
    queryFn: async () => (await apiClient.get<KimiDeployStatus>("/api/kimi-deploy/status")).data,
  });
}

export function invalidateAfterDeploy(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["accounts"] });
  void queryClient.invalidateQueries({ queryKey: ["account-groups"] });
  void queryClient.invalidateQueries({ queryKey: ["alerts"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  void queryClient.invalidateQueries({ queryKey: ["models"] });
  void queryClient.invalidateQueries({ queryKey: ["kimi-newapi"] });
  void queryClient.invalidateQueries({ queryKey: ["kimi-newapi-auth"] });
  void queryClient.invalidateQueries({ queryKey: ["kimi-stored-accounts"] });
  void queryClient.invalidateQueries({ queryKey: ["kimi-inventory"] });
}

export function useKimiDeploy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      accounts: Record<string, string>[];
      jobs: number;
      new_api_priority: number;
      new_api_weight: number;
    }) => (await apiClient.post<KimiDeployResponse>("/api/kimi-deploy", payload, { timeout: DEPLOY_TIMEOUT_MS })).data,
    onSuccess: () => invalidateAfterDeploy(queryClient),
  });
}

export async function streamKimiDeploy(
  payload: {
    accounts: Record<string, string>[];
    jobs: number;
    new_api_priority: number;
    new_api_weight: number;
  },
  onEvent: (event: KimiDeployProgressEvent) => void
) {
  const base = String(apiClient.defaults.baseURL || "").replace(/\/$/, "");
  const token = localStorage.getItem("access_token");
  const response = await fetch(`${base}/api/kimi-deploy/stream`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (response.status === 401) {
    localStorage.removeItem("access_token");
    window.location.href = "/login";
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok || !response.body) {
    let detail = `Deploy failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string" && body.detail.trim()) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawDone = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((part) => part.startsWith("data: "));
      if (!line) continue;
      try {
        const event = JSON.parse(line.slice(6)) as KimiDeployProgressEvent;
        onEvent(event);
        if (event.type === "done" || event.type === "error") sawDone = true;
      } catch {
        /* ignore a truncated or keep-alive frame */
      }
    }
  }
  if (!sawDone) throw new Error("Deploy stream ended before results arrived.");
}

export function useO1NewApiAuth() {
  return useQuery({
    queryKey: ["kimi-newapi-auth"],
    queryFn: async () => (await apiClient.get<KimiNewApiAuth>("/api/kimi-deploy/newapi/auth")).data,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
    retry: 1,
  });
}

export function useKimiNewApiPool(enabled: boolean) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["kimi-newapi"],
    queryFn: async () => {
      const data = (await apiClient.get<KimiNewApiPool>("/api/kimi-deploy/newapi")).data;
      if (data.auth_expired) {
        queryClient.setQueryData<KimiNewApiAuth>(["kimi-newapi-auth"], {
          ok: false,
          gateway: "O1",
          auth_expired: true,
          error: data.error || "O1 portal token expired",
        });
      }
      return data;
    },
    enabled,
    staleTime: 15_000,
    retry: 1,
  });
}

export function useKimiAddNewApi() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[]; priority: number; weight: number }) =>
      (await apiClient.post<KimiDeployResponse>("/api/kimi-deploy/newapi", payload, { timeout: LOOKUP_TIMEOUT_MS })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-newapi"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-inventory"] });
    },
  });
}

export function useKimiRenameNewApi() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      name: string;
      priority?: number | null;
      weight?: number | null;
      channel_id?: number | null;
      subscription_id?: string;
      account_name?: string;
      azure_openai_endpoint?: string;
    }) => (await apiClient.post<KimiDeployResult>("/api/kimi-deploy/newapi/rename", payload, { timeout: LOOKUP_TIMEOUT_MS })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-newapi"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-inventory"] });
    },
  });
}

export function useKimiRegenerateKeys() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[]; jobs?: number }) =>
      (
        await apiClient.post<KimiRegenerateResponse>("/api/kimi-deploy/regenerate-keys", payload, {
          timeout: KEYS_TIMEOUT_MS,
        })
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-stored-accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-inventory"] });
    },
  });
}

export function useKimiUndeploy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[]; jobs?: number }) =>
      (
        await apiClient.post<KimiDeleteResponse>("/api/kimi-deploy/undeploy", payload, {
          timeout: KEYS_TIMEOUT_MS,
        })
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-stored-accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["kimi-inventory"] });
    },
  });
}

export function useKimiTestModel() {
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[] }) =>
      (await apiClient.post<KimiTestResponse>("/api/kimi-deploy/test", payload, { timeout: LOOKUP_TIMEOUT_MS })).data,
  });
}

export function useKimiSheetStatus() {
  return useQuery({
    queryKey: ["kimi-sheet"],
    queryFn: async () => (await apiClient.get<KimiSheetStatus>("/api/kimi-deploy/sheet")).data,
    staleTime: 30_000,
  });
}

export function useKimiSheetSync() {
  return useMutation({
    mutationFn: async (payload: { results: KimiDeployResult[] }) =>
      (await apiClient.post<KimiSheetSyncResponse>("/api/kimi-deploy/sheet", payload, { timeout: LOOKUP_TIMEOUT_MS })).data,
  });
}

export function useKimiRefreshInventory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { accounts: Record<string, string>[] }) =>
      (
        await apiClient.post<KimiDeployResponse>(
          "/api/kimi-deploy/inventory",
          { accounts: payload.accounts, refresh: true },
          { timeout: LOOKUP_TIMEOUT_MS }
        )
      ).data,
    onSuccess: (data, variables) => {
      variables.accounts.forEach((account, index) => {
        const row = data.results[index];
        if (!row) return;
        queryClient.setQueryData(["kimi-inventory", accountFingerprint(account)], {
          ok_count: row.ok ? 1 : 0,
          fail_count: row.ok ? 0 : 1,
          results: [row],
        });
      });
      void queryClient.invalidateQueries({ queryKey: ["kimi-newapi"] });
    },
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
      enabled: enabled && Boolean(account.AZURE_SUBSCRIPTION_ID || account.AZURE_CLIENT_ID),
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
