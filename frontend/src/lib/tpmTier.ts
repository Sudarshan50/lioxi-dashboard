import { KimiDeployResult } from "@/types";

export function quotaTierNumber(label?: string | null): number | null {
  const text = (label || "").trim().toLowerCase();
  if (!text) return null;
  if (text.includes("free")) return 0;
  const match = text.match(/tier[\s_-]*(\d+)/i);
  return match ? Number(match[1]) : null;
}

export function quotaTierLabel(item: KimiDeployResult | null | undefined): string | null {
  return (item?.account_tier || "").trim() || null;
}

export function nextQuotaTierLabel(item: KimiDeployResult | null | undefined): string | null {
  return (item?.account_tier_available || "").trim() || null;
}

export function hasQuotaUpdate(item: KimiDeployResult | null | undefined): boolean {
  return Boolean(item?.tpm_upgrade_available);
}
