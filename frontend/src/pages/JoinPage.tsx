import { Check, Copy, KeyRound } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import { canonicalOwner } from "@/lib/ownerTag";
import {
  SUBMIT_SESSION_KEY,
  commitSubmitSession,
  fetchSubmitNames,
  fetchSubmitSnapshot,
  startSubmitSession,
  streamSubmitEvents,
} from "@/lib/submitApi";
import { SubmitSessionSnapshot, SubmitSubscription } from "@/types";

type Step = "welcome" | "signin" | "subscription" | "name" | "working" | "done";

function WaitOrbit({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-4 py-2">
      <div className="relative h-28 w-28">
        <span className="absolute inset-0 rounded-full border border-accent/25" />
        <span className="absolute inset-2 rounded-full border border-accent/20" />
        <span className="absolute inset-0 animate-pulse-ring rounded-full bg-accent/20" />
        <span className="absolute inset-[18%] animate-pulse-ring rounded-full bg-violet-500/15 [animation-delay:400ms]" />
        <div className="absolute inset-0 animate-orbit">
          <span className="absolute left-1/2 top-0 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-accent shadow-glow-sm" />
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="h-3 w-3 rounded-full bg-accent-gradient shadow-glow-sm" />
        </div>
      </div>
      <p className="text-center text-sm text-gray-300">{label}</p>
    </div>
  );
}

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
  const [step, setStep] = useState<Step>("welcome");
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem(SUBMIT_SESSION_KEY));
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  const [snapshot, setSnapshot] = useState<SubmitSessionSnapshot | null>(null);
  const [subscriptions, setSubscriptions] = useState<SubmitSubscription[]>([]);
  const [subscriptionId, setSubscriptionId] = useState("");
  const [person, setPerson] = useState("");
  const [names, setNames] = useState<string[]>([]);
  const [phaseMessage, setPhaseMessage] = useState("Working…");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const selected = useMemo(
    () => subscriptions.find((item) => item.subscription_id === subscriptionId) ?? null,
    [subscriptions, subscriptionId]
  );

  useEffect(() => {
    void fetchSubmitNames()
      .then(setNames)
      .catch(() => setNames([]));
  }, []);

  useEffect(() => {
    if (!sessionId) return;
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
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Could not resume session.");
      }
    })();
    return () => {
      cancelled = true;
    };
    // resume once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applySnapshot(current: SubmitSessionSnapshot) {
    setSnapshot(current);
    if (current.subscriptions?.length) setSubscriptions(current.subscriptions);
    if (current.subscription_id) setSubscriptionId(current.subscription_id);
    if (current.person_associated) setPerson(current.person_associated);
    if (current.error) setError(current.error);
    if (current.status === "logged_in") {
      const subs = current.subscriptions ?? [];
      if (subs.length === 1) {
        setSubscriptionId(subs[0].subscription_id);
        setStep("name");
      } else if (subs.length === 0) {
        setError("No enabled Azure subscriptions on this login.");
        setStep("welcome");
      } else {
        setStep("subscription");
      }
    } else if (current.status === "pending_approval" || current.status === "approved") {
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
    const kind = String(event.type || "");
    if (kind === "snapshot") {
      applySnapshot(event);
      return;
    }
    if (kind === "device_code") {
      setSnapshot((prev) => ({
        ...(prev ?? { session_id: sid, status: "login_started" }),
        session_id: sid || prev?.session_id || "",
        status: "login_started",
        device_user_code: String(event.user_code || event.device_user_code || prev?.device_user_code || ""),
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
      if (subs.length === 1) {
        setSubscriptionId(subs[0].subscription_id);
        setStep("name");
      } else if (subs.length === 0) {
        setError("No enabled Azure subscriptions on this login.");
        setStep("welcome");
      } else {
        if (subs[0]) setSubscriptionId(subs.find((item) => item.is_default)?.subscription_id || subs[0].subscription_id);
        setStep("subscription");
      }
      return;
    }
    if (kind === "phase") {
      setPhaseMessage(String(event.message || "Working…"));
      setStep("working");
      return;
    }
    if (kind === "done") {
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
  }

  async function startSignIn() {
    setError(null);
    setBusy(true);
    clearJoinSession();
    try {
      const created = await startSubmitSession();
      sessionStorage.setItem(SUBMIT_SESSION_KEY, created.session_id);
      sessionIdRef.current = created.session_id;
      setSessionId(created.session_id);
      setStep("signin");
      await streamSubmitEvents(created.session_id, applyEvent);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start Azure sign-in.");
      setStep("welcome");
    } finally {
      setBusy(false);
    }
  }

  function continueFromSub() {
    if (!subscriptionId) {
      setError("Pick a subscription.");
      return;
    }
    setError(null);
    setStep("name");
  }

  async function handleCommit(event: FormEvent) {
    event.preventDefault();
    if (!sessionId) return;
    const tag = canonicalOwner(person);
    if (!tag) {
      setError("Enter a name.");
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
      if (snap?.status === "pending_approval") {
        applyEvent({
          type: "done",
          session_id: sessionId,
          status: "pending_approval",
          message: "Submitted. An admin will deploy Kimi K3.",
        });
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
          <p className="mt-1 text-sm text-gray-500">Sign in with Azure. An admin deploys Kimi K3 after you submit.</p>
        </div>
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
        {error && (
          <div className="mb-4">
            <Banner tone="error">{error}</Banner>
          </div>
        )}
        {step === "welcome" && (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">
              {error
                ? "This attempt failed. Sign in again to reapply — you do not need an admin to decline first."
                : "You will get a one-time Microsoft code. After you approve access, pick your subscription and name. You will never see a client secret."}
            </p>
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
                <WaitOrbit label="Waiting for you to finish sign-in in the browser…" />
              </>
            ) : (
              <WaitOrbit label="Starting Azure sign-in…" />
            )}
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
                    {item.name || item.subscription_id}
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
                Subscription <span className="text-gray-300">{selected.name || selected.subscription_id}</span>
              </p>
            )}
            <div>
              <Input
                id="join-name"
                label="Your name"
                list="join-name-options"
                value={person}
                onChange={(event) => setPerson(event.target.value)}
                placeholder="e.g. Ritesh"
                autoComplete="off"
                maxLength={64}
                required
              />
              <datalist id="join-name-options">
                {names.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <p className="mt-1 text-xs text-gray-500">Pick an existing name or type a new one. This tags the account.</p>
            </div>
            <Button type="submit" isLoading={busy} className="w-full">
              Submit for deploy
            </Button>
          </form>
        )}
        {step === "working" && <WaitOrbit label={phaseMessage} />}
        {step === "done" && (
          <div className="flex flex-col items-center gap-3 py-2 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
              <Check size={22} />
            </div>
            <p className="text-sm font-medium text-gray-100">Submitted. An admin will deploy Kimi K3.</p>
            <p className="text-xs text-gray-500">You can close this page. No secrets were shown.</p>
          </div>
        )}
      </Card>
    </div>
  );
}
