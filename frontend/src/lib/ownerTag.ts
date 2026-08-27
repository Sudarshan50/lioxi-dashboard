export const UNTAGGED_OWNER = "__none__";
export const UNTAGGED_LABEL = "Untagged";

export function canonicalOwner(name: string) {
  // Split on whitespace; tokens with - or ' stay as typed, else First-upper + rest-lower.
  return name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) =>
      word.includes("-") || word.includes("'")
        ? word
        : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
    )
    .join(" ");
}

export function uniqueOwners(accounts: { owner_tag?: string | null }[]): string[] {
  const names = new Set<string>();
  for (const account of accounts) {
    const tag = (account.owner_tag ?? "").trim();
    if (tag) names.add(tag);
  }
  return [...names].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
}

export function matchesOwner(tag: string | null | undefined, owner: string | null): boolean {
  if (!owner) return true;
  const value = (tag ?? "").trim();
  if (owner === UNTAGGED_OWNER) return !value;
  return value === owner;
}

export function ownerLabel(tag: string | null | undefined): string {
  return (tag ?? "").trim() || UNTAGGED_LABEL;
}

export function ownerCounts(accounts: { owner_tag?: string | null }[]) {
  const counts = new Map<string, number>();
  let untagged = 0;
  for (const account of accounts) {
    const tag = (account.owner_tag ?? "").trim();
    if (!tag) untagged += 1;
    else counts.set(tag, (counts.get(tag) ?? 0) + 1);
  }
  return { counts, untagged };
}

type NumericBreakdown = {
  id: number;
  name: string;
  total_tokens: number;
  requests: number;
  estimated_cost_usd: number;
  estimated_cost?: number;
  actual_cost?: number | null;
  new_api_cost?: number | null;
  new_api_cost_o1?: number;
  new_api_cost_o2?: number;
  credits_limit?: number | null;
  avg_tpm?: number;
};

export function rollupBreakdownByTag<T extends NumericBreakdown>(
  items: T[],
  accounts: { id: number; owner_tag?: string | null }[]
): T[] {
  const tagById = new Map(accounts.map((account) => [account.id, ownerLabel(account.owner_tag)]));
  const grouped = new Map<string, T>();
  for (const item of items) {
    const tag = tagById.get(item.id) ?? UNTAGGED_LABEL;
    const current = grouped.get(tag);
    if (!current) {
      grouped.set(tag, { ...item, id: -(grouped.size + 1), name: tag });
      continue;
    }
    const prevTokens = current.total_tokens || 0;
    const addTokens = item.total_tokens || 0;
    const nextTokens = prevTokens + addTokens;
    if (nextTokens > 0) {
      current.avg_tpm = ((current.avg_tpm ?? 0) * prevTokens + (item.avg_tpm ?? 0) * addTokens) / nextTokens;
    }
    current.total_tokens = nextTokens;
    current.requests += item.requests || 0;
    current.estimated_cost_usd += item.estimated_cost_usd || 0;
    current.estimated_cost = (current.estimated_cost ?? 0) + (item.estimated_cost ?? 0);
    current.actual_cost = (current.actual_cost ?? 0) + (item.actual_cost ?? 0);
    current.new_api_cost = (current.new_api_cost ?? 0) + (item.new_api_cost ?? 0);
    current.new_api_cost_o1 = (current.new_api_cost_o1 ?? 0) + (item.new_api_cost_o1 ?? 0);
    current.new_api_cost_o2 = (current.new_api_cost_o2 ?? 0) + (item.new_api_cost_o2 ?? 0);
    current.credits_limit = (current.credits_limit ?? 0) + (item.credits_limit ?? 0);
  }
  return [...grouped.values()];
}

export function rollupTpmByTag<T extends { account_id: number; account_name: string; bucket: string; tpm: number }>(
  points: T[],
  accounts: { id: number; owner_tag?: string | null }[]
): T[] {
  const tagById = new Map(accounts.map((account) => [account.id, ownerLabel(account.owner_tag)]));
  const idByTag = new Map<string, number>();
  const summed = new Map<string, T>();
  for (const point of points) {
    const tag = tagById.get(point.account_id) ?? UNTAGGED_LABEL;
    if (!idByTag.has(tag)) idByTag.set(tag, -(idByTag.size + 1));
    const key = `${point.bucket}:${tag}`;
    const current = summed.get(key);
    if (!current) {
      summed.set(key, { ...point, account_id: idByTag.get(tag) as number, account_name: tag });
      continue;
    }
    current.tpm += point.tpm || 0;
  }
  return [...summed.values()];
}
