export interface AzureCredentialFields {
  tenantId: string;
  clientId: string;
  clientSecret: string;
  subscriptionId: string;
}

export interface AzureCredentialParseResult {
  values: AzureCredentialFields;
  filled: (keyof AzureCredentialFields)[];
  missing: (keyof AzureCredentialFields)[];
  error?: string;
}

const FIELD_ALIASES: Record<keyof AzureCredentialFields, string[]> = {
  tenantId: ["AZURE_TENANT_ID", "tenant_id", "tenantId", "tenant"],
  clientId: ["AZURE_CLIENT_ID", "client_id", "clientId", "appId", "app_id"],
  clientSecret: ["AZURE_CLIENT_SECRET", "client_secret", "clientSecret", "password"],
  subscriptionId: ["AZURE_SUBSCRIPTION_ID", "subscription_id", "subscriptionId", "subscription"],
};

const NESTED_KEYS = ["credentials", "azure", "sp", "servicePrincipal", "service_principal"];

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  return null;
}

function normalizeKey(key: string) {
  return key.replace(/[_-]/g, "").toLowerCase();
}

function firstNumber(source: Record<string, unknown>, aliases: string[]): number | null {
  const lookup = new Map(Object.entries(source).map(([key, value]) => [normalizeKey(key), value]));
  for (const alias of aliases) {
    const value = lookup.get(normalizeKey(alias));
    if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value.replace(/,/g, "").trim());
      if (Number.isFinite(parsed) && parsed > 0) return parsed;
    }
  }
  return null;
}

function firstString(source: Record<string, unknown>, aliases: string[]): string {
  const lookup = new Map(Object.entries(source).map(([key, value]) => [normalizeKey(key), value]));
  for (const alias of aliases) {
    const value = lookup.get(normalizeKey(alias));
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function parseJsonObject(raw: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, error: "Paste a JSON object with the service principal fields." };

  const candidates = [trimmed];
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start >= 0 && end > start) candidates.push(trimmed.slice(start, end + 1));

  let lastError = "That does not look like valid JSON.";
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      const record = asRecord(parsed);
      if (!record) {
        lastError = "JSON must be an object, not an array.";
        continue;
      }
      return { ok: true, value: record };
    } catch {
      lastError = "That does not look like valid JSON.";
    }
  }
  return { ok: false, error: lastError };
}

function parseEnvObject(raw: string): Record<string, unknown> | null {
  const record: Record<string, unknown> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
      (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
    ) {
      value = value.slice(1, -1);
    }
    record[match[1]] = value;
  }
  return Object.keys(record).length > 0 ? record : null;
}

export function looksLikeCredentialBlob(raw: string) {
  const trimmed = raw.trim();
  if (!trimmed) return false;
  if (trimmed.includes("{") && trimmed.endsWith("}")) return true;
  return /AZURE_(TENANT_ID|CLIENT_ID|CLIENT_SECRET|SUBSCRIPTION_ID)\s*=/.test(trimmed);
}

export function parseAzureCredentials(raw: string): AzureCredentialParseResult {
  const json = parseJsonObject(raw);
  const env = parseEnvObject(raw);
  const record = json.ok ? json.value : env;
  if (!record) {
    return {
      values: { tenantId: "", clientId: "", clientSecret: "", subscriptionId: "" },
      filled: [],
      missing: ["tenantId", "clientId", "clientSecret", "subscriptionId"],
      error: json.ok ? "No AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, or AZURE_SUBSCRIPTION_ID found." : json.error,
    };
  }

  const sources = [record];
  if (env && env !== record) sources.push(env);
  for (const nestedKey of NESTED_KEYS) {
    const nested = asRecord(record[nestedKey]);
    if (nested) sources.push(nested);
  }
  return credentialsFromSources(sources);
}

function credentialsFromSources(sources: Record<string, unknown>[]): AzureCredentialParseResult {
  const pick = (aliases: string[]) => {
    for (const source of sources) {
      const value = firstString(source, aliases);
      if (value) return value;
    }
    return "";
  };

  const values: AzureCredentialFields = {
    tenantId: pick(FIELD_ALIASES.tenantId),
    clientId: pick(FIELD_ALIASES.clientId),
    clientSecret: pick(FIELD_ALIASES.clientSecret),
    subscriptionId: pick(FIELD_ALIASES.subscriptionId),
  };
  const filled = (Object.keys(values) as (keyof AzureCredentialFields)[]).filter((key) => values[key]);
  const missing = (Object.keys(values) as (keyof AzureCredentialFields)[]).filter((key) => !values[key]);

  if (filled.length === 0) {
    return {
      values,
      filled,
      missing,
      error: "No AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, or AZURE_SUBSCRIPTION_ID found.",
    };
  }

  return { values, filled, missing };
}

const NAME_ALIASES = ["account_name", "accountName", "ACCOUNT_NAME", "name", "account"];

export interface AzureAccountImport {
  name: string;
  tenantId: string;
  clientId: string;
  clientSecret: string;
  subscriptionId: string;
  resourceName?: string;
  location?: string;
  creditsLimit?: number;
  deploymentName?: string;
}

export function parseAzureAccountImportArray(raw: string): { accounts: AzureAccountImport[]; error?: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { accounts: [], error: "Paste a JSON array of accounts." };

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { accounts: [], error: "That does not look like valid JSON." };
  }

  let items: unknown[] | null = Array.isArray(parsed) ? parsed : null;
  const wrapper = asRecord(parsed);
  if (!items && wrapper) {
    for (const key of ["accounts", "items", "data"]) {
      if (Array.isArray(wrapper[key])) {
        items = wrapper[key] as unknown[];
        break;
      }
    }
  }
  if (!items) return { accounts: [], error: "JSON must be an array of accounts." };
  if (items.length === 0) return { accounts: [], error: "The array is empty." };

  const accounts: AzureAccountImport[] = [];
  for (let index = 0; index < items.length; index++) {
    const record = asRecord(items[index]);
    if (!record) return { accounts: [], error: `Entry ${index + 1} must be a JSON object.` };
    const sources = [record];
    for (const nestedKey of NESTED_KEYS) {
      const nested = asRecord(record[nestedKey]);
      if (nested) sources.push(nested);
    }
    const creds = credentialsFromSources(sources);
    const name = firstString(record, NAME_ALIASES);
    const problems: string[] = [];
    if (!name) problems.push("account_name");
    if (creds.missing.length > 0) {
      problems.push(
        ...creds.missing.map((key) =>
          ({ tenantId: "AZURE_TENANT_ID", clientId: "AZURE_CLIENT_ID", clientSecret: "AZURE_CLIENT_SECRET", subscriptionId: "AZURE_SUBSCRIPTION_ID" }[key])
        )
      );
    }
    if (problems.length > 0) {
      return { accounts: [], error: `Entry ${index + 1} is missing ${problems.join(", ")}.` };
    }
    const resourceName = firstString(record, ["resource_name", "resourceName", "AZURE_RESOURCE_NAME"]);
    const location = firstString(record, ["location", "AZURE_LOCATION"]);
    const deploymentName = firstString(record, ["deployment_name", "deploymentName", "model_name", "modelName"]);
    const creditsLimit = firstNumber(record, ["credits_limit", "credit_limit", "creditsLimit", "grant"]);
    accounts.push({
      name,
      tenantId: creds.values.tenantId,
      clientId: creds.values.clientId,
      clientSecret: creds.values.clientSecret,
      subscriptionId: creds.values.subscriptionId,
      resourceName: resourceName || undefined,
      location: location || undefined,
      creditsLimit: creditsLimit ?? undefined,
      deploymentName: deploymentName || undefined,
    });
  }
  return { accounts };
}

export function describeParsedCredentials(result: AzureCredentialParseResult): string {
  const labels: Record<keyof AzureCredentialFields, string> = {
    tenantId: "AZURE_TENANT_ID",
    clientId: "AZURE_CLIENT_ID",
    clientSecret: "AZURE_CLIENT_SECRET",
    subscriptionId: "AZURE_SUBSCRIPTION_ID",
  };
  const filled = result.filled.map((key) => labels[key]);
  const missing = result.missing.map((key) => labels[key]);
  if (missing.length === 0) return `Filled ${filled.join(", ")}.`;
  return `Filled ${filled.join(", ")}. Still need ${missing.join(", ")}.`;
}
