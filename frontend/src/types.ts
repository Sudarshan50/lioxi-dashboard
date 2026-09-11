export interface Account {
  id: number;
  name: string;
  provider_type: string;
  subscription_id: string;
  resource_id: string;
  resource_group: string;
  resource_name: string;
  endpoint: string;
  kind: string;
  location: string;
  last_synced_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  credits_remaining: number | null;
  credits_used: number | null;
  credits_limit: number | null;
  credits_currency: string | null;
  credits_unit: string | null;
  credits_label: string | null;
  credits_available: boolean;
  credits_limit_manual?: boolean;
  new_api_gateway: string | null;
  new_api_channel_id: number | null;
  new_api_name: string | null;
  new_api_tag: string | null;
  owner_tag: string | null;
  group_tag?: string;
  new_api_used_quota: number | null;
  new_api_cost_o1_usd: number | null;
  new_api_cost_o2_usd: number | null;
  new_api_cost_usd: number | null;
  new_api_status: number | null;
  new_api_status_o1?: number | null;
  new_api_status_o2?: number | null;
  new_api_weight: number | null;
  new_api_priority: number | null;
  new_api_synced_at: string | null;
  payable_settled?: boolean;
  payable_settled_at?: string | null;
  at_cap_manual?: boolean;
  created_at: string;
}

export interface AccountGroup {
  id: number;
  name: string;
  accounts: { id: number; name: string }[];
  created_at: string;
  auto?: boolean;
}

export interface SyncAllResult {
  status: string;
  synced: number;
  failed: { id: number; name: string | null; error: string | null }[];
}

export interface SyncAccountResult {
  id: number;
  name: string | null;
  status: string;
  error: string | null;
}

export interface DiscoveredResource {
  resource_id: string;
  name: string;
  resource_group: string;
  kind: string;
  location: string;
  endpoint: string;
}

export interface Deployment {
  name: string;
  model_name: string;
  model_version: string;
  sku: string;
  capacity: number;
}

export interface RegisteredModel {
  id: number;
  name: string;
  input_price_per_million: number;
  cached_input_price_per_million: number;
  output_price_per_million: number;
  currency: string;
  deployments_count: number;
  created_at: string;
  updated_at: string;
}

export interface MonitoredModel {
  id: number;
  provider_account_id: number;
  provider_account_name: string;
  deployment_name: string;
  registered_model_id: number;
  model_name: string;
  input_price_per_million: number;
  cached_input_price_per_million: number;
  output_price_per_million: number;
  currency: string;
  enabled: boolean;
  created_at: string;
}

export interface DashboardOverview {
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cached_tokens: number;
  total_requests: number;
  estimated_cost_usd: number;
  estimated_cost: number;
  estimated_cost_currency: string;
  actual_cost: number;
  actual_cost_currency: string;
  new_api_cost: number;
  new_api_cost_o1: number;
  new_api_cost_o2: number;
  new_api_cost_currency: string;
  accounts_count: number;
  models_count: number;
  avg_tpm: number;
  avg_rpm: number;
  peak_tpm: number;
  peak_rpm: number;
}

export interface ClearGroupChatStatus {
  running: boolean;
  phase?: "finding" | "deleting" | "done" | "cancelled" | "error" | string;
  newest_id: number | null;
  attempted: number;
  total: number;
  failed_batches: number;
  ok: boolean | null;
  error: string | null;
}

export interface AlertStatus {
  telegram_configured: boolean;
  chat_id_set: boolean;
  admin_count: number;
  alerts_enabled: boolean;
  clear_chats?: ClearGroupChatStatus;
}

export interface AlertConfig {
  enabled: boolean;
  thresholds: number[];
  rearm_margin: number;
  overspend_buffer_usd: number;
  sync_interval_minutes: number;
  azure_sync_interval_minutes: number;
}

export interface AlertStateItem {
  id: number;
  name: string;
  deployed_at?: string | null;
  new_api_name?: string | null;
  new_api_tag?: string | null;
  owner_tag?: string | null;
  gateway: string | null;
  gateway_enabled: boolean;
  spend_usd: number;
  spend_o1_usd?: number | null;
  spend_o2_usd?: number | null;
  endpoint?: string;
  credits_limit: number | null;
  credits_currency: string | null;
  stop_at_usd: number | null;
  headroom_usd: number | null;
  overspend_buffer_usd: number;
  percent: number | null;
  exhausted: boolean;
  exhausted_reason: "overspent" | "manual" | null;
  at_cap_manual?: boolean;
  alert_level: number;
  payable_settled?: boolean;
  payable_settled_at?: string | null;
}

export interface TimeseriesPoint {
  bucket: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  requests?: number;
  estimated_cost_usd: number;
}

export interface AccountTpmPoint {
  bucket: string;
  account_id: number;
  account_name: string;
  tpm: number;
}

export interface BreakdownItem {
  id: number;
  name: string;
  total_tokens: number;
  requests: number;
  estimated_cost_usd: number;
  estimated_cost?: number;
  currency?: string;
  actual_cost?: number;
  actual_cost_currency?: string;
  new_api_cost?: number;
  new_api_cost_o1?: number;
  new_api_cost_o2?: number;
  credits_limit?: number | null;
  credits_currency?: string | null;
  avg_tpm?: number;
}

export interface FxRate {
  usd_inr: number;
  base: string;
  quote: string;
  source?: "live" | "config" | "fallback";
  is_fallback?: boolean;
}

export interface KimiDeployStatus {
  az_cli: boolean;
  az_path: string | null;
  script_found: boolean;
  script_path: string | null;
  ready: boolean;
  message: string;
  can_bootstrap?: boolean;
  az_user?: string | null;
  az_user_type?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  bootstrap_message?: string;
}

export interface KimiSheetStatus {
  configured: boolean;
}

export interface KimiSheetSyncResponse {
  ok: boolean;
  configured: boolean;
  synced: number;
  error?: string | null;
}

export interface KimiStoredAccount {
  name?: string | null;
  account_holder?: string | null;
  AZURE_TENANT_ID?: string | null;
  AZURE_CLIENT_ID?: string | null;
  AZURE_SUBSCRIPTION_ID: string;
  subscription_name?: string | null;
  owner_tag?: string | null;
  created_at?: string | null;
}

export interface KimiStoredResponse {
  accounts: KimiStoredAccount[];
}

export interface KimiDropStoredResponse {
  ok: boolean;
  dropped: number;
}

export interface KimiSecretsRow {
  ok: boolean;
  name?: string | null;
  account_holder?: string | null;
  AZURE_TENANT_ID?: string | null;
  AZURE_CLIENT_ID?: string | null;
  AZURE_CLIENT_SECRET?: string | null;
  AZURE_SUBSCRIPTION_ID?: string | null;
  subscription_name?: string | null;
  error?: string | null;
}

export interface KimiRegenerateResponse {
  ok_count: number;
  fail_count: number;
  results: KimiSecretsRow[];
}

export interface KimiCreditSnapshot {
  ok: boolean;
  name?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  credits_limit?: number | null;
  credits_remaining?: number | null;
  credits_used?: number | null;
  credits_currency?: string | null;
  credits_label?: string | null;
  credits_available?: boolean;
  error?: string | null;
}

export interface KimiCreditsResponse {
  results: KimiCreditSnapshot[];
}

export interface KimiDeployResult {
  ok: boolean;
  name?: string | null;
  email?: string | null;
  azure_openai_endpoint?: string | null;
  deployment_name?: string | null;
  model?: string | null;
  sku?: string | null;
  tpm?: number | null;
  rpm?: number | null;
  capacity?: number | null;
  quota_limit?: number | null;
  tpm_available?: number | null;
  rpm_available?: number | null;
  tpm_upgrade_available?: boolean;
  quota_id?: string | null;
  account_tier?: string | null;
  account_tier_available?: string | null;
  quota_tier_upgrade_available?: boolean;
  region?: string | null;
  account_name?: string | null;
  resource_group?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  credits_limit?: number | null;
  credits_remaining?: number | null;
  credits_used?: number | null;
  credits_currency?: string | null;
  credits_label?: string | null;
  credits_available?: boolean;
  error?: string | null;
  owner_tag?: string | null;
  new_api_present?: boolean;
  new_api_created?: boolean;
  new_api_channel_id?: number | null;
  new_api_name?: string | null;
  new_api_status?: number | null;
  new_api_status_label?: string | null;
  new_api_priority?: number | null;
  new_api_weight?: number | null;
  new_api_error?: string | null;
  rai_policy_name?: string | null;
  deployed_at?: string | null;
  removed?: boolean;
  deleted_resources?: string[];
  deleted_message?: string | null;
  pending?: boolean;
}

export interface KimiDeployDefaults {
  priority: number;
  weight: number;
}

export interface KimiDeployJob {
  running: boolean;
  job_id?: string | null;
  total: number;
  error?: string | null;
  results?: KimiDeployResult[] | null;
}

export interface KimiDeleteResult {
  ok: boolean;
  name?: string | null;
  account_name?: string | null;
  resource_group?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  deleted?: string[];
  message?: string | null;
  error?: string | null;
}

export interface KimiDeleteResponse {
  ok_count: number;
  fail_count: number;
  results: KimiDeleteResult[];
}

export interface KimiContentFilterResult {
  ok: boolean;
  name?: string | null;
  account_name?: string | null;
  resource_group?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  deployment_name?: string | null;
  rai_policy_name?: string | null;
  previous_rai_policy_name?: string | null;
  message?: string | null;
  error?: string | null;
}

export interface KimiContentFilterResponse {
  ok_count: number;
  fail_count: number;
  results: KimiContentFilterResult[];
}

export interface KimiTestResult {
  ok: boolean;
  name?: string | null;
  account_name?: string | null;
  deployment_name?: string | null;
  endpoint?: string | null;
  latency_ms?: number | null;
  reply?: string | null;
  error?: string | null;
}

export interface KimiTestResponse {
  ok_count: number;
  fail_count: number;
  results: KimiTestResult[];
}

export interface KimiDeployResponse {
  ok_count: number;
  fail_count: number;
  results: KimiDeployResult[];
}

export interface KimiDeployProgressEvent {
  type: "start" | "account" | "phase" | "done" | "error";
  total?: number;
  index?: number;
  done?: number;
  phase?: string;
  message?: string;
  result?: KimiDeployResult;
  results?: KimiDeployResult[];
  detail?: string;
}

export interface KimiNewApiChannel {
  id?: number | null;
  name?: string | null;
  status?: number | null;
  status_label?: string | null;
  tag?: string | null;
  group?: string | null;
  priority?: number | null;
  weight?: number | null;
  base_url?: string | null;
  resource_name?: string | null;
  models?: string | null;
}

export interface KimiCapacitySummary {
  tpm: number;
  rpm: number;
  accounts: number;
}

export interface KimiNewApiPool {
  ok: boolean;
  gateway?: string | null;
  next_name?: string | null;
  channels: KimiNewApiChannel[];
  error?: string | null;
  auth_expired?: boolean;
}

export interface KimiNewApiAuth {
  ok: boolean;
  gateway?: string | null;
  auth_expired: boolean;
  error?: string | null;
}

export interface SubmitSubscription {
  subscription_id: string;
  name: string;
  tenant_id: string;
  is_default: boolean;
}

export interface SubmitSessionSnapshot {
  type?: string;
  session_id: string;
  status: string;
  account_holder?: string | null;
  person_associated?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  group_tag?: string;
  device_user_code?: string | null;
  device_verification_uri?: string | null;
  subscriptions?: SubmitSubscription[];
  error?: string | null;
  billing_error?: string | null;
  message?: string | null;
  user_code?: string;
  verification_uri?: string;
  detail?: string;
  phase?: string;
}

export interface PendingSubmitRequest {
  id: number;
  status: string;
  person_associated?: string | null;
  group_tag?: string;
  account_holder?: string | null;
  name?: string | null;
  subscription_id?: string | null;
  subscription_name?: string | null;
  tenant_id?: string | null;
  billing_error?: string | null;
  error_message?: string | null;
  error_kind?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  can_retry_deploy?: boolean;
  credits_limit?: number | null;
  credits_remaining?: number | null;
  credits_used?: number | null;
  credits_currency?: string | null;
  credits_label?: string | null;
  credits_available?: boolean;
  credits_fetched_at?: string | null;
  credits_error?: string | null;
  grant_usd?: number | null;
  grant_tier?: "1k" | "10k" | "other" | null;
}

export interface PendingGrantSummary {
  total: number;
  fetched: number;
  missing: number;
  failed: number;
  pool_usd: number;
  count_10k: number;
  count_1k: number;
  count_other: number;
  pool_10k_usd: number;
  pool_1k_usd: number;
  fetched_at?: string | null;
}

export interface PendingGrantAccount {
  id: number;
  email?: string | null;
  name?: string | null;
  person_associated?: string | null;
  group_tag?: string;
  subscription_id?: string | null;
  credits_limit?: number | null;
  credits_remaining?: number | null;
  credits_currency?: string | null;
  credits_available: boolean;
  credits_fetched_at?: string | null;
  credits_error?: string | null;
  grant_usd?: number | null;
  grant_tier?: "1k" | "10k" | "other" | null;
}

export interface PendingGrantsResponse {
  ok: boolean;
  summary: PendingGrantSummary;
  accounts: PendingGrantAccount[];
}

export interface PendingListResponse {
  requests: PendingSubmitRequest[];
  pending_count: number;
  failed_count: number;
  grant_summary?: PendingGrantSummary;
}

export interface PendingApproveAccepted {
  ok: boolean;
  request_id: number;
  status: string;
}

export interface PendingBatchApproveResponse {
  ok: boolean;
  started: number[];
  skipped: { id: number; error: string }[];
}

export interface JoinEnrollee {
  id: number;
  name: string;
  group_tag: string;
  banned: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BanSettings {
  auto_approve: boolean;
  auto_approve_vcs: boolean;
  names: JoinEnrollee[];
}

export interface BanAutoApproveResponse {
  group: string;
  auto_approve: boolean;
  started: number[];
  skipped: number[];
}

export interface SyncNotification {
  id: number;
  kind: string;
  status: string;
  email: string | null;
  owner_tag: string | null;
  account_name: string | null;
  resource_name: string | null;
  subscription_id: string | null;
  new_api_name: string | null;
  detail: string;
  error: string | null;
  created_at: string;
}

export interface NotificationListResponse {
  items: SyncNotification[];
  total: number;
}

export interface ResourceMetric {
  used_bytes: number;
  total_bytes: number;
  available_bytes: number;
  percent: number;
}

export interface SystemStats {
  cpu_percent: number | null;
  cpu_count: number;
  load_avg_1: number;
  load_avg_5: number;
  load_avg_15: number;
  memory: ResourceMetric;
  storage: ResourceMetric;
  collected_at: string;
}

