import { Check, Copy, KeyRound } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import JoinTerminal, { JoinTermLine } from "@/components/join/JoinTerminal";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import Spinner from "@/components/ui/Spinner";
import { joinPickerName } from "@/lib/ownerTag";
import { toastDismiss, toastError } from "@/lib/toast";
import {
  JOIN_PASSWORD_KEY,
  SUBMIT_SESSION_KEY,
  cancelSubmitSession,
  commitSubmitSession,
  fetchSubmitNames,
  fetchSubmitSnapshot,
  startSubmitSession,
  streamSubmitEvents,
  unlockJoin,
} from "@/lib/submitApi";
import { SubmitSessionSnapshot, SubmitSubscription } from "@/types";

type Step = "gate" | "welcome" | "signin" | "subscription" | "name" | "working" | "done";

async function copyText(value: string): Promise<boolean> {
  const text = value.trim();
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to execCommand */
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.top = "0";
  field.style.left = "0";
  field.style.width = "1px";
  field.style.height = "1px";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.focus();
  field.select();
  field.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(field);
  return ok;
}

function subscriptionLabel(item: SubmitSubscription) {
  const subSlice = item.subscription_id.slice(0, 8);
  const tenantSlice = item.tenant_id ? item.tenant_id.slice(0, 8) : "—";
  return `${item.name || "Subscription"} · ${subSlice} · tenant ${tenantSlice}`;
}

function isSubmitComplete(status: string | undefined) {
  return status === "pending_approval" || status === "approved" || status === "approving";
}

async function pollCreatingSp(sessionId: string, stillThisSession: () => boolean) {
  while (stillThisSession()) {
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
    if (!stillThisSession()) return null;
    const next = await fetchSubmitSnapshot(sessionId);
    if (!next) return { kind: "missing" as const };
    if (isSubmitComplete(next.status)) return { kind: "done" as const, snap: next };
    if (next.status === "failed" || next.status === "expired" || next.status === "rejected") {
      return { kind: "failed" as const, snap: next };
    }
    if (next.status !== "creating_sp") return { kind: "other" as const, snap: next };
  }
  return null;
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    const ok = await copyText(value);
    setStatus(ok ? "copied" : "failed");
    window.setTimeout(() => setStatus("idle"), 1800);
  }
  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={`Copy ${label.toLowerCase()}`}
      className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-surface px-3 py-2.5 text-left hover:border-white/[0.14] hover:bg-white/[0.03]"
    >
      <div className="min-w-0">
        <p className="text-[11px] uppercase tracking-wide text-gray-500">{label}</p>
        <p className="select-all truncate font-mono text-sm text-gray-100">{value}</p>
        {status === "failed" && <p className="mt-0.5 text-[11px] text-amber-300">Could not copy. Select the text and press Ctrl+C.</p>}
      </div>
      <span className="flex shrink-0 items-center gap-1.5 rounded-lg px-1.5 py-1 text-gray-400">
        {status === "copied" ? (
          <>
            <Check size={16} className="text-emerald-400" />
            <span className="text-xs text-emerald-400">Copied</span>
          </>
        ) : (
          <>
            <Copy size={16} />
            <span className="text-xs">Copy</span>
          </>
        )}
      </span>
    </button>
  );
}

export default function JoinPage() {
  const [step, setStep] = useState<Step>(() => (sessionStorage.getItem(JOIN_PASSWORD_KEY) ? "welcome" : "gate"));
  const [unlocked, setUnlocked] = useState(() => Boolean(sessionStorage.getItem(JOIN_PASSWORD_KEY)));
  const [gatePassword, setGatePassword] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem(SUBMIT_SESSION_KEY));
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  const [snapshot, setSnapshot] = useState<SubmitSessionSnapshot | null>(null);
  const [subscriptions, setSubscriptions] = useState<SubmitSubscription[]>([]);
  const [subscriptionId, setSubscriptionId] = useState("");
  const [person, setPerson] = useState("");
  const [names, setNames] = useState<string[]>([]);
  const [namesLoading, setNamesLoading] = useState(() => Boolean(sessionStorage.getItem(JOIN_PASSWORD_KEY)));
  const [namesError, setNamesError] = useState<string | null>(null);
  const [namesTick, setNamesTick] = useState(0);
  const [doneMessage, setDoneMessage] = useState("Submitted. An admin will deploy Kimi K3.");
  const [phaseMessage, setPhaseMessage] = useState("Working…");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signinHint, setSigninHint] = useState<string | null>(null);
  const [termLines, setTermLines] = useState<JoinTermLine[]>([]);

  const selected = useMemo(
    () => subscriptions.find((item) => item.subscription_id === subscriptionId) ?? null,
    [subscriptions, subscriptionId]
  );

  useEffect(() => {
    if (error) toastError(error, { persist: true, toastId: "join-error" });
    else toastDismiss("join-error");
  }, [error]);

  useEffect(() => {
    if (!unlocked) return;
    let cancelled = false;
    setNamesLoading(true);
    setNamesError(null);
    void fetchSubmitNames()
      .then((rows) => {
        if (cancelled) return;
        setNames(rows.map((name) => joinPickerName(name)).filter((name): name is string => Boolean(name)));
        setNamesError(null);
      })
      .catch((exc) => {
        if (cancelled) return;
        const message = exc instanceof Error ? exc.message : "Could not load names.";
        if (message.includes("join password")) {
          setUnlocked(false);
          setStep("gate");
          return;
        }
        setNamesError(message);
      })
      .finally(() => {
        if (!cancelled) setNamesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [unlocked, namesTick]);

  useEffect(() => {
    if (step === "name") setNamesTick((n) => n + 1);
  }, [step]);

  useEffect(() => {
    if (!unlocked || !sessionId) return;
    let cancelled = false;
    void (async () => {
      try {
        const current = await fetchSubmitSnapshot(sessionId);
        if (cancelled) return;
        if (!current) {
          sessionStorage.removeItem(SUBMIT_SESSION_KEY);
          setSessionId(null);
          return;
        }
        applySnapshot(current);
        if (current.status === "login_started") {
          setStep("signin");
          await streamSubmitEvents(sessionId, (event) => {
            if (cancelled) return;
            applyEvent(event);
          });
        } else if (current.status === "creating_sp") {
          setStep("working");
          setPhaseMessage(current.message || "Creating monitor identity…");
          const settled = await pollCreatingSp(sessionId, () => !cancelled && sessionIdRef.current === sessionId);
          if (cancelled) return;
          if (!settled || settled.kind === "missing") {
            sessionStorage.removeItem(SUBMIT_SESSION_KEY);
            sessionIdRef.current = null;
            setSessionId(null);
            setStep("welcome");
            return;
          }
          if (settled.kind === "done") {
            applyEvent({
              type: "done",
              session_id: sessionId,
              status: settled.snap.status,
              message:
                settled.snap.message ||
                (settled.snap.status === "pending_approval"
                  ? "Submitted. An admin will deploy Kimi K3."
                  : "Submitted. Kimi K3 deploy is starting automatically."),
            });
            return;
          }
          applySnapshot(settled.snap);
        }
      } catch (exc) {
        if (!cancelled) {
          const message = exc instanceof Error ? exc.message : "Could not resume session.";
          if (message.includes("join password")) {
            setUnlocked(false);
            setStep("gate");
            return;
          }
          setError(message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // resume once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unlocked]);

  function pickSubscription(subs: SubmitSubscription[], currentId?: string | null) {
    if (subs.length === 0) {
      setError("No enabled Azure subscriptions on this login.");
      setStep("welcome");
      return;
    }
    const preferred =
      (currentId && subs.some((item) => item.subscription_id === currentId) ? currentId : "") ||
      subs.find((item) => item.is_default)?.subscription_id ||
      subs[0].subscription_id;
    setSubscriptionId(preferred);
    setStep("subscription");
  }

  function applySnapshot(current: SubmitSessionSnapshot) {
    setSnapshot(current);
    if (current.subscriptions?.length) setSubscriptions(current.subscriptions);
    if (current.subscription_id) setSubscriptionId(current.subscription_id);
    const savedName = joinPickerName(current.person_associated);
    if (savedName) setPerson(savedName);
    if (current.error) setError(current.error);
    if (current.status === "logged_in") {
      pickSubscription(current.subscriptions ?? [], current.subscription_id);
    } else if (isSubmitComplete(current.status)) {
      setDoneMessage(
        current.message ||
          (current.status === "pending_approval"
            ? "Submitted. An admin will deploy Kimi K3."
            : "Submitted. Kimi K3 deploy is starting automatically.")
      );
      setStep("done");
    } else if (current.status === "creating_sp") {
      setStep("working");
    } else if (current.status === "failed" || current.status === "expired" || current.status === "rejected") {
      setError(current.error || "This attempt failed. You can start again.");
      clearJoinSession();
      setStep("welcome");
    }
  }

  function applyEvent(event: SubmitSessionSnapshot & Record<string, unknown>) {
    const sid = sessionIdRef.current || "";
    if (!sid) return;
    const eventSid = String(event.session_id || "");
    if (eventSid && eventSid !== sid) return;
    const kind = String(event.type || "");
    if (kind === "snapshot") {
      applySnapshot(event);
      return;
    }
    if (kind === "terminal") {
      const text = String(event.line || "").trim();
      if (!text) return;
      const lineKind = String(event.kind || "out");
      const safeKind: JoinTermLine["kind"] =
        lineKind === "cmd" || lineKind === "err" || lineKind === "ok" ? lineKind : "out";
      setTermLines((prev) => [...prev, { kind: safeKind, text }].slice(-120));
      return;
    }
    if (kind === "device_code_wait") {
      const hint = String(event.message || "").trim();
      setSigninHint(hint || "Microsoft needs one more sign-in. Wait for a new code — do not reuse the previous one.");
      setSnapshot((prev) => ({
        ...(prev ?? { session_id: sid, status: "login_started" }),
        session_id: sid || prev?.session_id || "",
        status: "login_started",
        device_user_code: "",
        user_code: "",
        device_verification_uri: "",
        verification_uri: "",
      }));
      return;
    }
    if (kind === "device_code") {
      const hint = String(event.message || "").trim();
      setSigninHint(hint || null);
      const supplied = String(event.user_code ?? event.device_user_code ?? "").trim();
      setSnapshot((prev) => ({
        ...(prev ?? { session_id: sid, status: "login_started" }),
        session_id: sid || prev?.session_id || "",
        status: "login_started",
        device_user_code: supplied || prev?.device_user_code || "",
        device_verification_uri: String(
          event.verification_uri || event.device_verification_uri || prev?.device_verification_uri || ""
        ),
      }));
      setStep("signin");
      return;
    }
    if (kind === "logged_in") {
      const subs = (event.subscriptions as SubmitSubscription[] | undefined) ?? [];
      setSubscriptions(subs);
      setSnapshot((prev) => ({
        session_id: sid || prev?.session_id || "",
        status: "logged_in",
        account_holder: (event.account_holder as string) || prev?.account_holder,
        subscriptions: subs,
      }));
      pickSubscription(subs, event.subscription_id as string | undefined);
      return;
    }
    if (kind === "phase") {
      setPhaseMessage(String(event.message || "Working…"));
      setStep("working");
      return;
    }
    if (kind === "done") {
      setDoneMessage(
        String(event.message || "").trim() ||
          (event.status === "pending_approval"
            ? "Submitted. An admin will deploy Kimi K3."
            : "Submitted. Kimi K3 deploy is starting automatically.")
      );
      clearJoinSession();
      setStep("done");
      return;
    }
    if (kind === "error") {
      setError(String(event.detail || event.error || "Something went wrong."));
      clearJoinSession();
      setStep("welcome");
    }
  }

  function clearJoinSession() {
    sessionStorage.removeItem(SUBMIT_SESSION_KEY);
    sessionIdRef.current = null;
    setSessionId(null);
    setSnapshot(null);
    setTermLines([]);
  }

  async function handleUnlock(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await unlockJoin(gatePassword.trim());
      setGatePassword("");
      setNamesLoading(true);
      setNamesError(null);
      setUnlocked(true);
      setStep("welcome");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Wrong password.");
    } finally {
      setBusy(false);
    }
  }

  async function startSignIn() {
    const previous = sessionIdRef.current;
    setError(null);
    setBusy(true);
    setSigninHint(null);
    clearJoinSession();
    if (previous) {
      await cancelSubmitSession(previous).catch(() => undefined);
    }
    let createdId: string | null = null;
    try {
      const created = await startSubmitSession();
      createdId = created.session_id;
      sessionStorage.setItem(SUBMIT_SESSION_KEY, created.session_id);
      sessionIdRef.current = created.session_id;
      setSessionId(created.session_id);
      setStep("signin");
      await streamSubmitEvents(created.session_id, applyEvent);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "Could not start Azure sign-in.";
      // Let the server finish login. Cancelling here wiped the row and hid the real error.
      if (createdId && !message.toLowerCase().includes("stream ended")) {
        await cancelSubmitSession(createdId).catch(() => undefined);
      }
      if (createdId && sessionIdRef.current !== createdId) return;
      setError(message);
      if (message.includes("join password")) {
        setUnlocked(false);
        setStep("gate");
      } else {
        clearJoinSession();
        setStep("welcome");
      }
    } finally {
      if (!createdId || sessionIdRef.current === createdId) setBusy(false);
    }
  }

  async function handleCancelSignIn() {
    const sid = sessionIdRef.current;
    try {
      if (sid) await cancelSubmitSession(sid);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "";
      if (message.includes("join password")) {
        clearJoinSession();
        setBusy(false);
        setUnlocked(false);
        setStep("gate");
        return;
      }
    }
    clearJoinSession();
    setSigninHint(null);
    setError(null);
    setBusy(false);
    setStep("welcome");
  }

  function continueFromSub() {
    if (!subscriptionId) {
      setError("Pick a subscription.");
      return;
    }
    setError(null);
    setNamesError(null);
    if (names.length === 0) setNamesLoading(true);
    setStep("name");
  }

  async function handleCommit(event: FormEvent) {
    event.preventDefault();
    if (!sessionId) return;
    const tag = joinPickerName(person);
    if (!tag || !names.some((name) => name.toLowerCase() === tag.toLowerCase())) {
      setError("Pick a name from the dropdown.");
      return;
    }
    if (!subscriptionId) {
      setError("Pick a subscription.");
      return;
    }
    setError(null);
    setBusy(true);
    setStep("working");
    setPhaseMessage("Creating monitor identity…");
    try {
      await commitSubmitSession(sessionId, { subscription_id: subscriptionId, person_associated: tag }, applyEvent);
    } catch (exc) {
      const snap = await fetchSubmitSnapshot(sessionId).catch(() => null);
      if (snap && isSubmitComplete(snap.status)) {
        applyEvent({
          type: "done",
          session_id: sessionId,
          status: snap.status,
          message:
            snap.message ||
            (snap.status === "pending_approval"
              ? "Submitted. An admin will deploy Kimi K3."
              : "Submitted. Kimi K3 deploy is starting automatically."),
        });
        return;
      }
      if (snap?.status === "creating_sp") {
        setError(null);
        setPhaseMessage("Still creating the monitor identity…");
        setStep("working");
        const settled = await pollCreatingSp(sessionId, () => sessionIdRef.current === sessionId);
        if (!settled || settled.kind === "missing") {
          clearJoinSession();
          setStep("welcome");
          return;
        }
        if (settled.kind === "done") {
          applyEvent({
            type: "done",
            session_id: sessionId,
            status: settled.snap.status,
            message:
              settled.snap.message ||
              (settled.snap.status === "pending_approval"
                ? "Submitted. An admin will deploy Kimi K3."
                : "Submitted. Kimi K3 deploy is starting automatically."),
          });
          return;
        }
        applySnapshot(settled.snap);
        return;
      }
      setError(exc instanceof Error ? exc.message : "Submit failed.");
      clearJoinSession();
      setStep("welcome");
    } finally {
      setBusy(false);
    }
  }

  const code = snapshot?.device_user_code || snapshot?.user_code;
  const uri = snapshot?.device_verification_uri || snapshot?.verification_uri || "https://microsoft.com/devicelogin";
  const stepIndex = step === "welcome" || step === "signin" ? 1 : step === "subscription" ? 2 : step === "name" || step === "working" ? 3 : 4;

  return (
    <div className="app-aurora flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <Card className="w-full max-w-md animate-fade-up">
        <div className="mb-5 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-gradient text-white shadow-glow-sm">
            <KeyRound size={20} />
          </div>
          <h1 className="gradient-title text-xl font-semibold">Join Kimi K3</h1>
          <p className="mt-1 text-sm text-gray-500">
            {step === "gate" ? "This page is password protected." : "Sign in with Azure. An admin deploys Kimi K3 after you submit."}
          </p>
        </div>
        {step !== "gate" && (
        <ol className="mb-5 grid grid-cols-4 gap-1.5 text-center text-[10px] uppercase tracking-wide text-gray-500">
          {["Sign in", "Subscription", "Name", "Submitted"].map((label, index) => (
            <li
              key={label}
              className={
                index + 1 <= stepIndex ? "rounded-full bg-accent/20 py-1 text-indigo-200" : "rounded-full bg-white/[0.04] py-1"
              }
            >
              {label}
            </li>
          ))}
        </ol>
        )}
        {step === "gate" && (
          <form onSubmit={(event) => void handleUnlock(event)} className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">Enter the join password to continue.</p>
            <Input
              id="join-password"
              label="Password"
              type="password"
              value={gatePassword}
              onChange={(event) => setGatePassword(event.target.value)}
              autoComplete="current-password"
              required
            />
            <Button type="submit" isLoading={busy} className="w-full">
              Continue
            </Button>
          </form>
        )}
        {step === "welcome" && (
          <div className="flex flex-col gap-4">
            {error ? (
              <p className="whitespace-pre-wrap rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                {error}
              </p>
            ) : (
              <p className="text-sm text-gray-400">
                You will get a one-time Microsoft code. After you approve access, pick your subscription and name. You
                will never see a client secret.
              </p>
            )}
            <Button type="button" isLoading={busy} onClick={() => void startSignIn()} className="w-full">
              {error ? "Try again" : "Start Azure sign-in"}
            </Button>
          </div>
        )}
        {step === "signin" && (
          <div className="flex flex-col gap-4">
            {code ? (
              <>
                <CopyField label="Code" value={code} />
                <CopyField label="Open this page" value={uri} />
                <a
                  href={uri}
                  target="_blank"
                  rel="noreferrer"
                  className="text-center text-xs text-indigo-300 hover:text-indigo-200"
                >
                  Open Microsoft device login
                </a>
              </>
            ) : (
              <p className="text-center text-xs text-gray-400">
                {signinHint || "Waiting for a Microsoft device code…"}
              </p>
            )}
            {code && signinHint && <p className="text-center text-xs text-gray-400">{signinHint}</p>}
            <JoinTerminal lines={termLines} waiting />
            <Button type="button" variant="secondary" onClick={() => void handleCancelSignIn()} className="w-full">
              Cancel
            </Button>
          </div>
        )}
        {step === "subscription" && (
          <div className="flex flex-col gap-4">
            {snapshot?.account_holder && (
              <p className="text-xs text-gray-500">
                Signed in as <span className="text-gray-300">{snapshot.account_holder}</span>
              </p>
            )}
            <label className="flex min-w-0 w-full flex-col gap-1.5">
              <span className="text-xs font-medium text-gray-400">Subscription</span>
              <select
                value={subscriptionId}
                onChange={(event) => setSubscriptionId(event.target.value)}
                className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent"
              >
                <option value="">Select…</option>
                {subscriptions.map((item) => (
                  <option key={item.subscription_id} value={item.subscription_id}>
                    {subscriptionLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            <Button type="button" onClick={continueFromSub} className="w-full">
              Continue
            </Button>
          </div>
        )}
        {step === "name" && (
          <form onSubmit={(event) => void handleCommit(event)} className="flex flex-col gap-4">
            {selected && (
              <p className="text-xs text-gray-500">
                Subscription <span className="text-gray-300">{subscriptionLabel(selected)}</span>
              </p>
            )}
            <label className="flex min-w-0 w-full flex-col gap-1.5">
              <span className="text-xs font-medium text-gray-400">Your name</span>
              {namesLoading && names.length === 0 ? (
                <div className="flex items-center gap-2 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-400">
                  <Spinner className="h-4 w-4" />
                  Loading names…
                </div>
              ) : names.length === 0 && namesError ? (
                <div className="flex flex-col gap-2">
                  <p className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                    {namesError}
                  </p>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setNamesLoading(true);
                      setNamesError(null);
                      setNamesTick((n) => n + 1);
                    }}
                    className="w-full"
                  >
                    Retry
                  </Button>
                </div>
              ) : (
                <select
                  id="join-name"
                  value={person}
                  onChange={(event) => setPerson(event.target.value)}
                  required
                  disabled={names.length === 0}
                  className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent disabled:opacity-60"
                >
                  <option value="">{names.length ? "Select from the dropdown…" : "No names available"}</option>
                  {names.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              )}
              {names.length > 0 && (
                <p className="text-xs text-gray-500">Use the dropdown. Custom names are not allowed.</p>
              )}
            </label>
            <Button
              type="submit"
              isLoading={busy}
              disabled={!person || names.length === 0 || namesLoading || Boolean(namesError)}
              className="w-full"
            >
              Submit for deploy
            </Button>
          </form>
        )}
        {step === "working" && (
          <div className="flex flex-col gap-3">
            <p className="text-center text-xs text-gray-400">{phaseMessage}</p>
            <JoinTerminal lines={termLines} waiting />
            <Button type="button" variant="secondary" onClick={() => void handleCancelSignIn()} className="w-full">
              Cancel
            </Button>
          </div>
        )}
        {step === "done" && (
          <div className="flex flex-col items-center gap-3 py-2 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
              <Check size={22} />
            </div>
            <p className="text-sm font-medium text-gray-100">{doneMessage}</p>
            <p className="text-xs text-gray-500">You can close this page. No secrets were shown.</p>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                clearJoinSession();
                setStep("welcome");
              }}
              className="mt-1 w-full"
            >
              Join another account
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
