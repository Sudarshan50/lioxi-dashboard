import { combinedSpendUsd } from "@/lib/accountSort";
import { formatCurrency } from "@/lib/format";
import { grantAmountUsd, grantTier } from "@/lib/grantTier";
import { amountPayableUsd } from "@/lib/payable";
import { Account } from "@/types";

export type GrantBucket = {
  active: number;
  activeCount: number;
  pool: number;
  o1: number;
  o2: number;
};

export function emptyGrantBucket(): GrantBucket {
  return { active: 0, activeCount: 0, pool: 0, o1: 0, o2: 0 };
}

export function summarizeAccounts(rows: Account[], usdInr: number) {
  const oneK = emptyGrantBucket();
  const tenK = emptyGrantBucket();
  let spend = 0;
  let payable = 0;
  let pool = 0;
  let count = 0;
  for (const account of rows) {
    const cost = combinedSpendUsd(account) ?? 0;
    const grant = grantAmountUsd(account, usdInr) ?? 0;
    spend += cost;
    payable += amountPayableUsd(cost);
    pool += grant;
    count += 1;
    const tier = grantTier(account, usdInr);
    const bucket = tier === "1k" ? oneK : tier === "10k" ? tenK : null;
    if (!bucket) continue;
    bucket.active += cost;
    bucket.activeCount += 1;
    bucket.pool += grant;
    bucket.o1 += account.new_api_cost_o1_usd || 0;
    bucket.o2 += account.new_api_cost_o2_usd || 0;
  }
  return {
    spend,
    payable: Math.round(payable * 100) / 100,
    pool,
    count,
    oneK,
    tenK,
  };
}

export function GrantTierBits({
  label,
  tone,
  bucket,
}: {
  label: string;
  tone: "emerald" | "sky";
  bucket: GrantBucket;
}) {
  const spendClass = tone === "emerald" ? "text-emerald-300" : "text-sky-300";
  return (
    <>
      {label} <span className={`tabular-nums ${spendClass}`}>{formatCurrency(bucket.active, "USD")}</span>
      / <span className="tabular-nums text-gray-200">{formatCurrency(bucket.pool, "USD")}</span> pool
      {bucket.activeCount ? ` (${bucket.activeCount})` : ""}
    </>
  );
}
