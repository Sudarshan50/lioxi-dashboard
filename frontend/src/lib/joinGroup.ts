// SB / VCS join groups. Mirrors backend/app/services/join_group.py; the two
// must agree, because the Join wizard previews the name the backend assigns.

export const GROUP_SB = "sb";
export const GROUP_VCS = "vcs";
export const JOIN_GROUPS = [GROUP_SB, GROUP_VCS] as const;

export type JoinGroup = (typeof JOIN_GROUPS)[number];

const LABELS: Record<JoinGroup, string> = { sb: "SB", vcs: "VCS" };

/** Anything unknown or missing reads as SB, so pre-VCS rows keep working. */
export function normalizeGroup(value: string | null | undefined): JoinGroup {
  const group = (value ?? "").trim().toLowerCase();
  return (JOIN_GROUPS as readonly string[]).includes(group) ? (group as JoinGroup) : GROUP_SB;
}

export function isVcs(value: string | null | undefined): boolean {
  return normalizeGroup(value) === GROUP_VCS;
}

export function groupLabel(value: string | null | undefined): string {
  return LABELS[normalizeGroup(value)];
}

/** Local part initials + first domain label: john.doe@corp.com -> jd-corp. */
export function vcsAccountName(email: string | null | undefined): string {
  const text = (email ?? "").trim().toLowerCase();
  const at = text.indexOf("@");
  if (at < 1) return "";
  const local = text.slice(0, at).split("+")[0];
  const initials = local
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
    .map((token) => token[0])
    .join("")
    .slice(0, 16);
  const domain = (text.slice(at + 1).split(/[^a-z0-9]+/).find(Boolean) ?? "").slice(0, 24);
  if (!initials || !domain) return "";
  return `${initials}-${domain}`.slice(0, 64);
}

// The O1 channel name encodes the group, so rows that carry a channel but no
// group_tag of their own (alerts, deploy results, the notification feed) can
// still be labelled without a schema change.
const VCS_CHANNEL_RE = /^cs-proxy-\d+$/i;
const SB_CHANNEL_RE = /^kimi-k3-500k-proxy-\d+$/i;

/** Group implied by a NewAPI channel name, or null when it is not a pool channel. */
export function groupFromChannel(name: string | null | undefined): JoinGroup | null {
  const text = (name ?? "").trim();
  if (VCS_CHANNEL_RE.test(text)) return GROUP_VCS;
  if (SB_CHANNEL_RE.test(text)) return GROUP_SB;
  return null;
}

/** An explicit group_tag wins; otherwise fall back to the channel name. */
export function resolveGroup(
  groupTag: string | null | undefined,
  channelName?: string | null
): JoinGroup {
  const stated = (groupTag ?? "").trim();
  if (stated) return normalizeGroup(stated);
  return groupFromChannel(channelName) ?? GROUP_SB;
}

export function groupCounts(
  rows: { group_tag?: string | null; new_api_name?: string | null }[]
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const key = resolveGroup(row.group_tag, row.new_api_name);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

/** True when a row belongs to the selected group (null selects everything). */
export function matchesGroup(
  row: { group_tag?: string | null; new_api_name?: string | null },
  group: JoinGroup | null
): boolean {
  if (!group) return true;
  return resolveGroup(row.group_tag, row.new_api_name) === group;
}

/** Spaces are not valid in Azure names: "Ayush P" -> "AyushP". */
export function compactPerson(name: string | null | undefined): string {
  return (name ?? "").split(/\s+/).filter(Boolean).join("");
}

/**
 * Portal account name a submission will get, before collision suffixing.
 * Must stay in step with join_account_name() in the backend, or the wizard
 * shows the member a name they will not receive.
 */
export function joinAccountName(
  group: string | null | undefined,
  email: string | null | undefined,
  sbName: string,
  person?: string | null
): string {
  if (!isVcs(group)) return sbName;
  return compactPerson(person) || vcsAccountName(email) || sbName;
}
