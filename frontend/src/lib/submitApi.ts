import { PendingSubmitRequest, SubmitSessionSnapshot } from "@/types";

export const SUBMIT_SESSION_KEY = "submit_session_id";
export const JOIN_PASSWORD_KEY = "join_password";

export function submitBaseUrl() {
  return "";
}

function joinHeaders(init?: HeadersInit) {
  const headers = new Headers(init);
  const password = sessionStorage.getItem(JOIN_PASSWORD_KEY);
  if (password) headers.set("X-Join-Password", password);
  return headers;
}

function assertJoinAuth(response: Response) {
  if (response.status === 401) {
    sessionStorage.removeItem(JOIN_PASSWORD_KEY);
    throw new Error("Wrong or missing join password.");
  }
}

async function readError(response: Response, fallback: string) {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    /* ignore */
  }
  return fallback;
}

function waitMs(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

function isTerminalSseType(kind: string) {
  return kind === "done" || kind === "error" || kind === "logged_in";
}

function consumeSseChunk(chunk: string, onEvent: (event: Record<string, unknown>) => void) {
  const line = chunk.split("\n").find((part) => part.startsWith("data: "));
  if (!line) return false;
  try {
    const event = JSON.parse(line.slice(6)) as Record<string, unknown>;
    onEvent(event);
    return isTerminalSseType(String(event.type || ""));
  } catch {
    return false;
  }
}

export async function parseSseStream(response: Response, onEvent: (event: Record<string, unknown>) => void) {
  if (!response.body) throw new Error("No response body.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminal = false;
  while (true) {
    const { done, value } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    if (done) buffer += decoder.decode();
    const chunks = buffer.split("\n\n");
    buffer = done ? "" : (chunks.pop() ?? "");
    for (const chunk of chunks) {
      if (consumeSseChunk(chunk, onEvent)) sawTerminal = true;
    }
    if (done) {
      if (buffer.trim() && consumeSseChunk(buffer, onEvent)) sawTerminal = true;
      break;
    }
  }
  return sawTerminal;
}

export async function unlockJoin(password: string) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/unlock`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ password }),
  });
  if (response.status === 401) throw new Error("Wrong password.");
  if (!response.ok) throw new Error(await readError(response, "Could not unlock join."));
  sessionStorage.setItem(JOIN_PASSWORD_KEY, password);
}

export async function startSubmitSession() {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions`, {
    method: "POST",
    cache: "no-store",
    headers: joinHeaders({ Accept: "application/json" }),
  });
  assertJoinAuth(response);
  if (!response.ok) throw new Error(await readError(response, `Could not start sign-in (${response.status}).`));
  return (await response.json()) as { session_id: string; status: string };
}

export async function fetchSubmitSnapshot(sessionId: string) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}`, {
    cache: "no-store",
    headers: joinHeaders({ Accept: "application/json" }),
  });
  assertJoinAuth(response);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(await readError(response, `Could not load session (${response.status}).`));
  return (await response.json()) as SubmitSessionSnapshot;
}

export async function fetchSubmitNames(group: string) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/names?group=${encodeURIComponent(group)}`, {
    cache: "no-store",
    headers: joinHeaders({ Accept: "application/json" }),
  });
  assertJoinAuth(response);
  if (!response.ok) throw new Error(await readError(response, "Could not load names."));
  const body = (await response.json()) as { names?: string[] };
  return body.names ?? [];
}

export async function cancelSubmitSession(sessionId: string) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}/cancel`, {
    method: "POST",
    cache: "no-store",
    headers: joinHeaders({ Accept: "application/json" }),
  });
  assertJoinAuth(response);
  if (response.status === 404) return;
  if (!response.ok) throw new Error(await readError(response, `Could not cancel (${response.status}).`));
}

function emitLoggedInFromSnapshot(
  sessionId: string,
  snap: SubmitSessionSnapshot,
  onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void
) {
  onEvent({
    type: "logged_in",
    session_id: sessionId,
    status: "logged_in",
    account_holder: snap.account_holder,
    subscription_id: snap.subscription_id,
    subscriptions: snap.subscriptions ?? [],
    message: snap.message,
  });
}

function emitErrorFromSnapshot(
  sessionId: string,
  snap: SubmitSessionSnapshot | null,
  fallback: string,
  onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void
) {
  onEvent({
    type: "error",
    session_id: sessionId,
    status: snap?.status || "failed",
    detail: snap?.error || fallback,
    error: snap?.error || fallback,
  });
}

async function streamSubmitEventsOnce(
  sessionId: string,
  onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void,
  alreadyReconnected: boolean
) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}/events`, {
    cache: "no-store",
    headers: joinHeaders({ Accept: "text/event-stream" }),
  });
  assertJoinAuth(response);
  if (!response.ok) throw new Error(await readError(response, `Sign-in stream failed (${response.status}).`));
  const saw = await parseSseStream(response, (event) => onEvent(event as SubmitSessionSnapshot & Record<string, unknown>));
  if (saw) return;

  const deadline = Date.now() + 15 * 60 * 1000;
  let lastCode = "";
  while (Date.now() < deadline) {
    const snap = await fetchSubmitSnapshot(sessionId);
    const nextCode = String(snap?.device_user_code || "").trim();
    if (nextCode && nextCode !== lastCode) {
      lastCode = nextCode;
      onEvent({
        type: "device_code",
        session_id: sessionId,
        status: "login_started",
        user_code: nextCode,
        verification_uri: snap?.device_verification_uri || undefined,
        message: snap?.message || undefined,
      });
    }
    if (snap?.status === "logged_in") {
      emitLoggedInFromSnapshot(sessionId, snap, onEvent);
      return;
    }
    if (snap?.status === "failed" || snap?.status === "expired" || snap?.status === "rejected") {
      emitErrorFromSnapshot(sessionId, snap, "This attempt failed.", onEvent);
      return;
    }
    if (snap?.status === "login_started" && !alreadyReconnected) {
      await streamSubmitEventsOnce(sessionId, onEvent, true);
      return;
    }
    await waitMs(2000);
  }
  throw new Error("Sign-in stream ended before results arrived.");
}

export async function streamSubmitEvents(
  sessionId: string,
  onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void
) {
  await streamSubmitEventsOnce(sessionId, onEvent, false);
}

export async function commitSubmitSession(
  sessionId: string,
  payload: { subscription_id: string; person_associated: string; group_tag: string },
  onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void
) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}/commit`, {
    method: "POST",
    cache: "no-store",
    headers: joinHeaders({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify(payload),
  });
  assertJoinAuth(response);
  if (!response.ok) throw new Error(await readError(response, `Submit failed (${response.status}).`));
  const saw = await parseSseStream(response, (event) => onEvent(event as SubmitSessionSnapshot & Record<string, unknown>));
  if (saw) return;

  const deadline = Date.now() + 10 * 60 * 1000;
  while (true) {
    const snap = await fetchSubmitSnapshot(sessionId);
    if (snap?.status === "pending_approval" || snap?.status === "approved" || snap?.status === "approving") {
      onEvent({
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
    if (snap?.status === "failed" || snap?.status === "expired" || snap?.status === "rejected") {
      emitErrorFromSnapshot(sessionId, snap, "Submit failed.", onEvent);
      return;
    }
    if (Date.now() >= deadline) {
      if (snap?.status === "creating_sp") {
        throw new Error("Still creating the monitor identity. You can refresh this page — it is still running.");
      }
      throw new Error(snap?.error || "Submit stream ended before results arrived.");
    }
    await waitMs(2000);
  }
}

export async function enqueuePendingApprove(
  requestId: number,
  routing?: { new_api_priority?: number; new_api_weight?: number }
) {
  const token = localStorage.getItem("access_token");
  const response = await fetch(`${submitBaseUrl()}/api/pending/${requestId}/approve`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(routing ?? {}),
  });
  if (response.status === 401) {
    localStorage.removeItem("access_token");
    window.location.href = "/login";
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok) throw new Error(await readError(response, `Approve failed (${response.status}).`));
}

export async function enqueuePendingApproveBatch(payload: {
  ids?: number[];
  retry: boolean;
  // Restricts an ids-less sweep to one Join group; omit for every group.
  group?: string;
  new_api_priority?: number;
  new_api_weight?: number;
}) {
  const token = localStorage.getItem("access_token");
  const response = await fetch(`${submitBaseUrl()}/api/pending/approve-batch`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (response.status === 401) {
    localStorage.removeItem("access_token");
    window.location.href = "/login";
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok) throw new Error(await readError(response, `Approve failed (${response.status}).`));
  return (await response.json()) as { started: number[]; skipped: { id: number; error: string }[] };
}

export type { PendingSubmitRequest };
