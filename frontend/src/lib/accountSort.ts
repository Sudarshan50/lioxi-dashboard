import { Account } from "@/types";

export function finiteNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

export function compareOptionalNumbers(
  left: number | null | undefined,
  right: number | null | undefined,
  direction: "asc" | "desc"
): number {
  const first = finiteNumber(left);
  const second = finiteNumber(right);
  if (first == null && second == null) return 0;
  if (first == null) return 1;
  if (second == null) return -1;
  if (first === second) return 0;
  const order = first < second ? -1 : 1;
  return direction === "asc" ? order : -order;
}

export function compareOptionalText(left: string | null | undefined, right: string | null | undefined): number {
  const first = normalizeAccountName(left);
  const second = normalizeAccountName(right);
  if (!first && !second) return 0;
  if (!first) return 1;
  if (!second) return -1;
  return first.localeCompare(second, undefined, { sensitivity: "base", numeric: true });
}

function normalizeAccountName(value: string | null | undefined): string {
  return (value ?? "").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
}

export function toUsd(amount: unknown, currency: string | null | undefined, usdInr: number): number | null {
  const value = finiteNumber(amount);
  if (value == null) return null;
  const code = (currency || "USD").toUpperCase();
  if (code === "USD") return value;
  if (code === "INR") return usdInr > 0 ? value / usdInr : null;
  return value;
}

export function gatewaySet(account?: Account): Set<string> {
  return new Set((account?.new_api_gateway ?? "").split("+").filter((part) => part === "O1" || part === "O2"));
}

export function portalSpendUsd(account: Account | undefined, portal: "O1" | "O2"): number | null {
  if (!account || !gatewaySet(account).has(portal)) return null;
  const raw = portal === "O1" ? account.new_api_cost_o1_usd : account.new_api_cost_o2_usd;
  return finiteNumber(raw) ?? 0;
}

export function combinedSpendUsd(account?: Account): number | null {
  if (!account) return null;
  const combined = finiteNumber(account.new_api_cost_usd);
  if (combined != null) return combined;
  const o1 = portalSpendUsd(account, "O1");
  const o2 = portalSpendUsd(account, "O2");
  if (o1 == null && o2 == null) return null;
  return (o1 ?? 0) + (o2 ?? 0);
}

export function actualSpendUsd(
  item: { actual_cost?: number | null; actual_cost_currency?: string | null } | undefined,
  usdInr: number
): number | null {
  if (!item) return null;
  const amount = finiteNumber(item.actual_cost);
  if (amount == null || amount === 0) return null;
  return toUsd(amount, item.actual_cost_currency, usdInr);
}

function hasMonetaryCredits(account?: Account): boolean {
  return Boolean(account?.credits_available && account.credits_unit === "currency");
}

export function creditRemainingUsd(account: Account | undefined, usdInr: number): number | null {
  if (!hasMonetaryCredits(account)) return null;
  return toUsd(account?.credits_remaining, account?.credits_currency, usdInr);
}

export function creditOutstandingUsd(account: Account | undefined, usdInr: number): number | null {
  if (!hasMonetaryCredits(account) || !account) return null;
  const used = finiteNumber(account.credits_used);
  if (used != null) return toUsd(Math.max(used, 0), account.credits_currency, usdInr);
  const limit = finiteNumber(account.credits_limit);
  const remaining = finiteNumber(account.credits_remaining);
  if (limit == null || remaining == null) return null;
  return toUsd(Math.max(limit - remaining, 0), account.credits_currency, usdInr);
}

export function consumedPercent(account?: Account): number | null {
  if (!hasMonetaryCredits(account) || !account) return null;
  const limit = finiteNumber(account.credits_limit);
  const remaining = finiteNumber(account.credits_remaining);
  if (limit == null || limit <= 0 || remaining == null) return null;
  return Math.min(Math.max(limit - remaining, 0) / limit * 100, 100);
}

export function creditLeftRatio(account?: Account): number | null {
  if (!hasMonetaryCredits(account) || !account) return null;
  const limit = finiteNumber(account.credits_limit);
  const remaining = finiteNumber(account.credits_remaining);
  if (limit == null || limit <= 0 || remaining == null) return null;
  return Math.min(Math.max(remaining / limit, 0), 1);
}

export function gatewayRank(account?: Account): number | null {
  if (!account) return null;
  if (account.new_api_status === 1) return 0;
  if (account.new_api_status != null) return 1;
  if (gatewaySet(account).size > 0) return 2;
  return null;
}
