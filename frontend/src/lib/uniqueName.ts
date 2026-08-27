export function allocateUniqueName(preferred: string, taken: Iterable<string>): string {
  const cleaned = preferred.trim().replace(/\s+/g, " ").slice(0, 128) || "account";
  const used = new Set([...taken].map((name) => name.trim().toLowerCase()).filter(Boolean));
  if (!used.has(cleaned.toLowerCase())) return cleaned;
  for (let index = 1; index < 1000; index += 1) {
    const suffix = String(index);
    const candidate = `${cleaned.slice(0, 128 - suffix.length)}${suffix}`;
    if (!used.has(candidate.toLowerCase())) return candidate;
  }
  return `${cleaned.slice(0, 120)}-x`;
}
