import { KimiDeployProgressEvent, PendingSubmitRequest, SubmitSessionSnapshot } from "@/types";

export const SUBMIT_SESSION_KEY = "submit_session_id";

export function submitBaseUrl() {
  return "";
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

export async function parseSseStream(response: Response, onEvent: (event: Record<string, unknown>) => void) {
  if (!response.body) throw new Error("No response body.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminal = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((part) => part.startsWith("data: "));
      if (!line) continue;
      try {
        const event = JSON.parse(line.slice(6)) as Record<string, unknown>;
        onEvent(event);
        const kind = String(event.type || "");
        if (kind === "done" || kind === "error" || kind === "logged_in") sawTerminal = true;
      } catch {
        /* truncated keep-alive */
      }
    }
  }
  return sawTerminal;
}

export async function startSubmitSession() {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions`, {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(await readError(response, `Could not start sign-in (${response.status}).`));
  return (await response.json()) as { session_id: string; status: string };
}

export async function fetchSubmitSnapshot(sessionId: string) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(await readError(response, `Could not load session (${response.status}).`));
  return (await response.json()) as SubmitSessionSnapshot;
}

export async function fetchSubmitNames() {
  const response = await fetch(`${submitBaseUrl()}/api/submit/names`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(await readError(response, "Could not load names."));
  const body = (await response.json()) as { names?: string[] };
  return body.names ?? [];
}

export async function streamSubmitEvents(sessionId: string, onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}/events`, {
    cache: "no-store",
    headers: { Accept: "text/event-stream" },
  });
  if (!response.ok) throw new Error(await readError(response, `Sign-in stream failed (${response.status}).`));
  await parseSseStream(response, (event) => onEvent(event as SubmitSessionSnapshot & Record<string, unknown>));
}

export async function commitSubmitSession(
  sessionId: string,
  payload: { subscription_id: string; person_associated: string },
  onEvent: (event: SubmitSessionSnapshot & Record<string, unknown>) => void
) {
  const response = await fetch(`${submitBaseUrl()}/api/submit/sessions/${sessionId}/commit`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readError(response, `Submit failed (${response.status}).`));
  const saw = await parseSseStream(response, (event) => onEvent(event as SubmitSessionSnapshot & Record<string, unknown>));
  if (saw) return;
  const snap = await fetchSubmitSnapshot(sessionId);
  if (snap?.status === "pending_approval") {
    onEvent({
      type: "done",
      session_id: sessionId,
      status: "pending_approval",
      message: "Submitted. An admin will deploy Kimi K3.",
    });
    return;
  }
  throw new Error(snap?.error || "Submit stream ended before results arrived.");
}

export async function streamPendingApprove(
  requestId: number,
  onEvent: (event: KimiDeployProgressEvent) => void
) {
  const token = localStorage.getItem("access_token");
  const response = await fetch(`${submitBaseUrl()}/api/pending/${requestId}/approve`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ jobs: 1, new_api_priority: 13, new_api_weight: 1 }),
  });
  if (response.status === 401) {
    localStorage.removeItem("access_token");
    window.location.href = "/login";
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok) throw new Error(await readError(response, `Approve failed (${response.status}).`));
  const saw = await parseSseStream(response, (event) => onEvent(event as unknown as KimiDeployProgressEvent));
  if (!saw) throw new Error("Approve stream ended before results arrived.");
}

export type { PendingSubmitRequest };
