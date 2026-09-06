import { Account } from "@/types";

export type GrantTier = "1k" | "10k" | "other";

const ONE_K_GRANT_MIN = 800;
const ONE_K_GRANT_MAX = 1500;
const TEN_K_GRANT_MIN = 8000;
const TEN_K_GRANT_MAX = 15000;

function compactName(value: string | null | undefined): string {
  return (value ?? "").toLowerCase().replace(/[\s_-]+/g, "");
}

export function grantAmountUsd(
  account: Pick<Account, "credits_limit" | "credits_currency">,
  usdInr = 87
): number | null {
  const limit = Number(account.credits_limit);
  if (!Number.isFinite(limit) || limit <= 0) return null;
  const code = (account.credits_currency || "USD").toUpperCase();
  if (code === "USD") return limit;
  if (code === "INR") return usdInr > 0 ? limit / usdInr : null;
  return limit;
}

export function isGatewayEnabled(account: Pick<Account, "new_api_status">): boolean {
  return account.new_api_status === 1;
}

export function grantTier(
  account: Pick<Account, "name" | "credits_limit" | "credits_currency">,
  usdInr = 87
): GrantTier {
  const grant = grantAmountUsd(account, usdInr);
  if (grant != null) {
    if (grant >= TEN_K_GRANT_MIN && grant <= TEN_K_GRANT_MAX) return "10k";
    if (grant >= ONE_K_GRANT_MIN && grant <= ONE_K_GRANT_MAX) return "1k";
  }
  const compact = compactName(account.name);
  if (compact.includes("10k")) return "10k";
  if (compact.includes("1k")) return "1k";
  return "other";
}
