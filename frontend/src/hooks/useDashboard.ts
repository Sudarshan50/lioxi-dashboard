import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/apiClient";
import { AccountTpmPoint, BreakdownItem, DashboardOverview, FxRate, TimeseriesPoint } from "@/types";

interface Filters {
  range: string;
  accountId?: number | null;
  modelId?: number | null;
  groupId?: number | null;
  gateway?: string | null;
  owner?: string | null;
}

function toParams(filters: Filters) {
  return {
    range: filters.range,
    account_id: filters.accountId,
    model_id: filters.modelId,
    group_id: filters.groupId,
    gateway: filters.gateway || undefined,
    owner: filters.owner || undefined,
  };
}

export function useDashboardOverview(filters: Filters, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["dashboard", "overview", filters],
    queryFn: async () =>
      (await apiClient.get<DashboardOverview>("/api/dashboard/overview", { params: toParams(filters) })).data,
    enabled: options?.enabled ?? true,
  });
}

export function useDashboardTimeseries(filters: Filters, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["dashboard", "timeseries", filters],
    queryFn: async () =>
      (await apiClient.get<TimeseriesPoint[]>("/api/dashboard/timeseries", { params: toParams(filters) })).data,
    enabled: options?.enabled ?? true,
  });
}

export function useTimeseriesByAccount(filters: Filters) {
  return useQuery({
    queryKey: ["dashboard", "timeseries-by-account", filters],
    queryFn: async () =>
      (await apiClient.get<AccountTpmPoint[]>("/api/dashboard/timeseries-by-account", { params: toParams(filters) }))
        .data,
  });
}

export function useBreakdownByAccount(
  range: string,
  modelId?: number | null,
  accountId?: number | null,
  groupId?: number | null,
  gateway?: string | null,
  owner?: string | null
) {
  return useQuery({
    queryKey: ["dashboard", "by-account", range, modelId, accountId, groupId, gateway, owner],
    queryFn: async () =>
      (
        await apiClient.get<BreakdownItem[]>("/api/dashboard/by-account", {
          params: {
            range,
            model_id: modelId,
            account_id: accountId,
            group_id: groupId,
            gateway: gateway || undefined,
            owner: owner || undefined,
          },
        })
      ).data,
  });
}

export function useBreakdownByModel(
  range: string,
  accountId?: number | null,
  groupId?: number | null,
  modelId?: number | null,
  gateway?: string | null,
  owner?: string | null
) {
  return useQuery({
    queryKey: ["dashboard", "by-model", range, accountId, groupId, modelId, gateway, owner],
    queryFn: async () =>
      (
        await apiClient.get<BreakdownItem[]>("/api/dashboard/by-model", {
          params: {
            range,
            account_id: accountId,
            group_id: groupId,
            model_id: modelId,
            gateway: gateway || undefined,
            owner: owner || undefined,
          },
        })
      ).data,
  });
}

export function useUsdInrRate() {
  return useQuery({
    queryKey: ["dashboard", "fx"],
    queryFn: async () => (await apiClient.get<FxRate>("/api/dashboard/fx")).data,
    staleTime: 60 * 60 * 1000,
  });
}

export function useBreakdownByDeployment(
  range: string,
  accountId?: number | null,
  groupId?: number | null,
  owner?: string | null
) {
  return useQuery({
    queryKey: ["dashboard", "by-deployment", range, accountId, groupId, owner],
    queryFn: async () =>
      (
        await apiClient.get<BreakdownItem[]>("/api/dashboard/by-deployment", {
          params: { range, account_id: accountId, group_id: groupId, owner: owner || undefined },
        })
      ).data,
  });
}
