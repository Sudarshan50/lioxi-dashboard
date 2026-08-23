import { BellRing, Play, Plus, Send, X } from "lucide-react";
import { useEffect, useState } from "react";

import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import Spinner from "@/components/ui/Spinner";
import {
  useAlertConfig,
  useAlertState,
  useAlertStatus,
  useRunAlertCheck,
  useSaveAlertConfig,
  useSendTestAlert,
} from "@/hooks/useAlerts";
import { formatCurrency } from "@/lib/format";

export default function AlertsPage() {
  const status = useAlertStatus();
  const config = useAlertConfig();
  const state = useAlertState();
  const saveConfig = useSaveAlertConfig();
  const sendTest = useSendTestAlert();
  const runCheck = useRunAlertCheck();

  const [enabled, setEnabled] = useState(true);
  const [thresholds, setThresholds] = useState<number[]>([75, 95]);
  const [rearmMargin, setRearmMargin] = useState("5");
  const [newThreshold, setNewThreshold] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!config.data) return;
    setEnabled(config.data.enabled);
    setThresholds(config.data.thresholds);
    setRearmMargin(String(config.data.rearm_margin));
  }, [config.data]);

  const dirty =
    config.data != null &&
    (enabled !== config.data.enabled ||
      thresholds.join(",") !== config.data.thresholds.join(",") ||
      Number(rearmMargin) !== config.data.rearm_margin);

  function addThreshold() {
    const value = Math.round(Number(newThreshold));
    if (!Number.isFinite(value) || value < 1 || value > 99) {
      setError("Thresholds must be between 1 and 99 percent — 100% triggers the auto-disable.");
      return;
    }
    setError(null);
    setThresholds((prev) => (prev.includes(value) ? prev : [...prev, value].sort((a, b) => a - b)));
    setNewThreshold("");
  }

  async function handleSave() {
    setMessage(null);
    setError(null);
    if (thresholds.length === 0) {
      setError("Keep at least one threshold.");
      return;
    }
    const margin = Number(rearmMargin);
    if (!Number.isFinite(margin) || margin < 0 || margin > 100) {
      setError("Re-arm margin must be between 0 and 100 points.");
      return;
    }
    try {
      await saveConfig.mutateAsync({ enabled, thresholds, rearm_margin: margin });
      setMessage("Alert rules saved.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not save alert rules.");
    }
  }

  async function handleTest() {
    setMessage(null);
    setError(null);
    try {
      await sendTest.mutateAsync();
      setMessage("Test message sent to the Telegram group.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not send the test message.");
    }
  }

  async function handleRunCheck() {
    setMessage(null);
    setError(null);
    try {
      const result = await runCheck.mutateAsync();
      if (result.skipped) setMessage(`Check skipped: ${result.skipped}.`);
      else setMessage(result.sent > 0 ? `Check complete — ${result.sent} alert${result.sent === 1 ? "" : "s"} sent.` : "Check complete — no new thresholds crossed.");
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Alert check failed.");
    }
  }

  return (
    <div className="flex flex-col gap-5 sm:gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="gradient-title text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="mt-1 text-sm text-gray-500">
            Telegram alerts when Azure credit consumption crosses your thresholds · channels auto-disable when
            remaining hits zero or outstanding charges already consume the grant
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {status.data && (
            <>
              <Badge tone={status.data.telegram_configured ? "success" : "error"}>
                {status.data.telegram_configured ? "bot connected" : "bot not configured"}
              </Badge>
              <Badge tone={status.data.alerts_enabled ? "success" : "warning"}>
                {status.data.alerts_enabled ? "alerts on" : "alerts paused"}
              </Badge>
              <Badge tone="neutral">{status.data.admin_count} admin{status.data.admin_count === 1 ? "" : "s"}</Badge>
            </>
          )}
        </div>
      </div>

      {message && <Banner tone="success">{message}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-6">
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-200">Alert rules</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              Thresholds are % of Azure credits consumed (from Azure's own balance). Each fires once per account and
              re-arms after consumption drops below it by the re-arm margin (e.g. a credit top-up). Channels auto-disable
              when remaining hits zero, or when outstanding/pending already meets the Azure credit grant.
            </p>
          </div>

          <button
            type="button"
            onClick={() => setEnabled((v) => !v)}
            className="flex w-fit items-center gap-2.5 rounded-xl border border-surface-border bg-surface px-3 py-2 text-sm"
          >
            <span
              className={`relative h-5 w-9 rounded-full transition-colors ${enabled ? "bg-emerald-500/80" : "bg-gray-600/60"}`}
            >
              <span
                className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${enabled ? "translate-x-4" : ""}`}
              />
            </span>
            <span className={enabled ? "text-emerald-300" : "text-gray-400"}>
              {enabled ? "Alerts enabled" : "Alerts paused"}
            </span>
          </button>

          <div>
            <p className="mb-1.5 text-xs font-medium text-gray-400">Thresholds (% of Azure credits consumed)</p>
            <div className="flex flex-wrap items-center gap-1.5">
              {thresholds.map((threshold) => (
                <span
                  key={threshold}
                  className="inline-flex items-center gap-1 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-2 py-1 text-xs font-medium text-indigo-200"
                >
                  {threshold}%
                  <button
                    type="button"
                    onClick={() => setThresholds((prev) => prev.filter((t) => t !== threshold))}
                    className="text-indigo-300/70 hover:text-red-300"
                    aria-label={`Remove ${threshold}% threshold`}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
              <div className="flex items-center gap-1">
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={newThreshold}
                  onChange={(e) => setNewThreshold(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addThreshold();
                  }}
                  placeholder="e.g. 90"
                  className="w-24 py-1 text-xs"
                />
                <Button variant="secondary" className="px-2 py-1 text-xs" onClick={addThreshold}>
                  <Plus size={13} /> Add
                </Button>
              </div>
            </div>
          </div>

          <div className="flex items-end gap-3">
            <div className="w-32">
              <Input
                label="Re-arm margin (pts)"
                type="number"
                min={0}
                max={100}
                value={rearmMargin}
                onChange={(e) => setRearmMargin(e.target.value)}
              />
            </div>
            <Button onClick={handleSave} isLoading={saveConfig.isPending} disabled={!dirty || config.isLoading}>
              Save rules
            </Button>
          </div>
        </Card>

        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-200">Bot actions</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              The bot also answers /usage, /enable and /disable in the group for whitelisted admins.
            </p>
          </div>
          <div className="flex flex-col gap-2 text-xs text-gray-400">
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface px-3 py-2">
              <span>Telegram bot token</span>
              <Badge tone={status.data?.telegram_configured ? "success" : "error"}>
                {status.data?.telegram_configured ? "configured" : "missing"}
              </Badge>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface px-3 py-2">
              <span>Group chat</span>
              <Badge tone={status.data?.chat_id_set ? "success" : "error"}>
                {status.data?.chat_id_set ? "linked" : "not linked"}
              </Badge>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface px-3 py-2">
              <span>Whitelisted admins</span>
              <span className="text-gray-200">{status.data?.admin_count ?? "—"}</span>
            </div>
          </div>
          <div className="mt-auto flex flex-wrap gap-2">
            <Button variant="secondary" onClick={handleTest} isLoading={sendTest.isPending}>
              <Send size={15} /> Send test message
            </Button>
            <Button variant="secondary" onClick={handleRunCheck} isLoading={runCheck.isPending}>
              <Play size={15} /> Run check now
            </Button>
          </div>
        </Card>
      </div>

      <Card className="min-w-0 overflow-hidden">
        <div className="flex items-center gap-2">
          <BellRing size={15} className="text-indigo-300" />
          <h2 className="text-sm font-semibold text-gray-200">Account alert state</h2>
        </div>
        <p className="mt-0.5 text-xs text-gray-500">
          Azure credit consumption per account · auto-stop when remaining is 0 or outstanding ≥ grant · “announced” is
          the highest threshold already alerted
        </p>
        <div className="mt-4 overflow-x-auto">
          {state.isLoading ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : state.isError ? (
            <p className="py-8 text-center text-sm text-gray-500">Could not load alert state.</p>
          ) : (state.data ?? []).length === 0 ? (
            <p className="py-8 text-center text-sm text-gray-500">No accounts with NewAPI data yet — run a sync.</p>
          ) : (
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead>
                <tr className="border-b border-surface-border text-[11px] uppercase tracking-wider text-gray-500">
                  <th className="py-2 pr-3 font-medium">Account</th>
                  <th className="py-2 pr-3 font-medium">Gateway</th>
                  <th className="py-2 pr-3 text-right font-medium">NewAPI spend</th>
                  <th className="py-2 pr-3 text-right font-medium">Left / grant</th>
                  <th className="py-2 pr-3 text-right font-medium">Outstanding</th>
                  <th className="py-2 pr-3 font-medium">Consumed</th>
                  <th className="py-2 font-medium">Announced</th>
                </tr>
              </thead>
              <tbody>
                {(state.data ?? []).map((item) => (
                  <tr key={item.id} className="border-b border-surface-border/60 last:border-0">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-gray-200">{item.name}</span>
                      {item.exhausted && (
                        <Badge tone="error" className="ml-2">
                          {item.exhausted_reason === "overspent" ? "overspent" : "zero left"}
                        </Badge>
                      )}
                      {!item.gateway_enabled && (
                        <Badge tone="warning" className="ml-2">
                          disabled
                        </Badge>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-gray-400">{item.gateway ?? "—"}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-violet-300">
                      {formatCurrency(item.spend_usd, "USD")}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-gray-300">
                      {item.credits_remaining != null
                        ? formatCurrency(item.credits_remaining, item.credits_currency || "USD")
                        : "—"}
                      <span className="text-gray-600">
                        {" "}
                        / {item.credits_limit != null ? formatCurrency(item.credits_limit, item.credits_currency || "USD") : "—"}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-gray-300">
                      {item.credits_outstanding != null
                        ? formatCurrency(item.credits_outstanding, item.credits_currency || "USD")
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-3">
                      {item.percent == null ? (
                        <span className="text-gray-600">n/a</span>
                      ) : (
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-border">
                            <div
                              className={`h-full rounded-full ${
                                item.exhausted || item.percent >= 95
                                  ? "bg-red-500"
                                  : item.percent >= 75
                                    ? "bg-amber-400"
                                    : "bg-emerald-400"
                              }`}
                              style={{ width: `${Math.min(item.percent, 100)}%` }}
                            />
                          </div>
                          <span className="tabular-nums text-gray-300">{item.percent.toFixed(1)}%</span>
                        </div>
                      )}
                    </td>
                    <td className="py-2.5">
                      {item.alert_level >= 100 ? (
                        <Badge tone="error">auto-disabled</Badge>
                      ) : item.alert_level > 0 ? (
                        <Badge tone={item.alert_level >= 95 ? "error" : "warning"}>{item.alert_level}%</Badge>
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}
