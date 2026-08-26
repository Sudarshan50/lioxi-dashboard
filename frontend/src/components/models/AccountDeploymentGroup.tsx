import { ReactNode } from "react";

import Badge from "@/components/ui/Badge";
import ModelRow from "@/components/models/ModelRow";
import { EstimateCurrency, formatCurrency, formatEstimatedCost } from "@/lib/format";
import { Account, BreakdownItem, MonitoredModel } from "@/types";

interface AccountDeploymentGroupProps {
  account?: Account;
  accountName: string;
  deployments: MonitoredModel[];
  usageById: Map<number, BreakdownItem>;
  usageLoading: boolean;
  estimateCurrency: EstimateCurrency;
  usdInr: number;
  actualCost: number | null;
  actualCostCurrency: string | null;
}

export default function AccountDeploymentGroup({
  account,
  accountName,
  deployments,
  usageById,
  usageLoading,
  estimateCurrency,
  usdInr,
  actualCost,
  actualCostCurrency,
}: AccountDeploymentGroupProps) {
  const portals = gatewayLabels(account);
  const remaining = creditRemaining(account);
  const limit = account?.credits_limit ?? null;
  const currency = account?.credits_currency || "USD";
  const outstanding = creditOutstanding(account);
  const consumed = consumedPercent(account);

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.06] bg-surface-raised/80 bg-card-sheen shadow-card backdrop-blur-sm">
      <div className="border-b border-white/[0.06] bg-black/20 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <p className="font-medium text-gray-100">{accountName}</p>
              {account?.owner_tag && (
                <Badge tone="info" title="Tag">
                  {account.owner_tag}
                </Badge>
              )}
              {portals.map((portal) => {
                const status = portal === "O1" ? account?.new_api_status_o1 : account?.new_api_status_o2;
                const enabled = status === 1;
                const unknown = status == null;
                return (
                  <Badge key={portal} tone={unknown ? "warning" : enabled ? "success" : "neutral"}>
                    {portal} {unknown ? "?" : enabled ? "on" : "off"}
                  </Badge>
                );
              })}
            </div>
            {account?.new_api_name && (
              <p className="mt-0.5 truncate text-[11px] text-gray-400" title="NewAPI channel">
                {account.new_api_name}
              </p>
            )}
            {account?.resource_name && (
              <p className="mt-0.5 truncate text-xs text-gray-500">
                {account.resource_name}
                {account.location ? ` · ${account.location}` : ""}
              </p>
            )}
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Metric label="Azure credits">
            {remaining != null ? (
              <>
                <p className="tabular-nums text-gray-100">
                  {formatCurrency(remaining, currency)}
                  {limit != null && <span className="text-gray-500"> / {formatCurrency(limit, currency)}</span>}
                </p>
                {consumed != null && (
                  <div className="mt-1.5">
                    <div className="h-1 overflow-hidden rounded-full bg-white/[0.06]">
                      <div
                        className={`h-full rounded-full ${consumed >= 95 ? "bg-red-400" : consumed >= 75 ? "bg-amber-400" : "bg-emerald-400"}`}
                        style={{ width: `${Math.min(consumed, 100)}%` }}
                      />
                    </div>
                    <p className="mt-1 text-[11px] text-gray-500">{consumed.toFixed(0)}% consumed</p>
                  </div>
                )}
              </>
            ) : (
              <p className="text-gray-600">—</p>
            )}
          </Metric>

          <Metric label="Outstanding">
            <p className="tabular-nums text-gray-100">
              {outstanding != null ? formatCurrency(outstanding, currency) : "—"}
            </p>
          </Metric>

          <Metric label="Actual billed (30d)">
            <p className="tabular-nums text-gray-100">
              {actualCost != null && actualCost > 0 ? formatCurrency(actualCost, actualCostCurrency || "USD") : "—"}
            </p>
          </Metric>

          <Metric label="NewAPI lifetime">
            {account?.new_api_cost_usd != null ? (
              <div className="flex flex-col gap-0.5">
                <p className="tabular-nums text-gray-100">
                  {formatEstimatedCost(account.new_api_cost_usd, estimateCurrency, usdInr)}
                  {portals.length > 1 && <span className="ml-1 text-[11px] font-normal text-gray-500">combined</span>}
                </p>
                {portals.length > 0 && (
                  <p className="text-[11px] text-gray-500">
                    {portals.includes("O1") && (
                      <span>
                        O1 {formatEstimatedCost(account.new_api_cost_o1_usd ?? 0, estimateCurrency, usdInr)}
                      </span>
                    )}
                    {portals.includes("O1") && portals.includes("O2") && <span className="px-1 text-gray-600">·</span>}
                    {portals.includes("O2") && (
                      <span>
                        O2 {formatEstimatedCost(account.new_api_cost_o2_usd ?? 0, estimateCurrency, usdInr)}
                      </span>
                    )}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-gray-600">—</p>
            )}
          </Metric>
        </div>
      </div>

      <div className="overflow-x-auto px-4 sm:px-5">
        <table className="w-full min-w-[720px] text-left">
          <thead>
            <tr className="border-b border-surface-border text-xs uppercase tracking-wide text-gray-500">
              <th className="py-3 pr-4 font-medium">Model</th>
              <th className="py-3 pr-4 font-medium">Tokens (30d)</th>
              <th className="py-3 pr-4 font-medium">Est. cost (30d)</th>
              <th className="py-3 pr-4 font-medium">Status</th>
              <th className="py-3 pr-0 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {deployments.map((model) => (
              <ModelRow
                key={model.id}
                model={model}
                usage={usageById.get(model.id)}
                usageLoading={usageLoading}
                estimateCurrency={estimateCurrency}
                usdInr={usdInr}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-500">{label}</p>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

function gatewayLabels(account?: Account): Array<"O1" | "O2"> {
  return (account?.new_api_gateway ?? "").split("+").filter((part): part is "O1" | "O2" => part === "O1" || part === "O2");
}

function hasMonetaryCredits(account?: Account): boolean {
  return Boolean(account?.credits_available && account.credits_unit === "currency");
}

function creditRemaining(account?: Account): number | null {
  if (!hasMonetaryCredits(account) || account?.credits_remaining == null || !Number.isFinite(account.credits_remaining)) {
    return null;
  }
  return account.credits_remaining;
}

function creditOutstanding(account?: Account): number | null {
  if (!hasMonetaryCredits(account) || !account) return null;
  if (account.credits_used != null && Number.isFinite(account.credits_used)) return Math.max(account.credits_used, 0);
  if (account.credits_limit != null && account.credits_remaining != null) {
    return Math.max(account.credits_limit - account.credits_remaining, 0);
  }
  return null;
}

function consumedPercent(account?: Account): number | null {
  const remaining = creditRemaining(account);
  if (remaining == null || account?.credits_limit == null || account.credits_limit <= 0) return null;
  return Math.min(Math.max(account.credits_limit - remaining, 0) / account.credits_limit * 100, 100);
}
