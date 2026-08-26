import apiClient from "@/lib/apiClient";

function filenameFromDisposition(header: string | undefined, fallback: string) {
  const match = String(header ?? "").match(/filename="?([^"]+)"?/i);
  return match?.[1] ?? fallback;
}

export async function downloadDashboardCsv(params: {
  range: string;
  groupId?: number | null;
  owner?: string | null;
  fallbackName?: string;
}) {
  const response = await apiClient.get("/api/dashboard/export", {
    params: {
      range: params.range,
      ...(params.groupId != null ? { group_id: params.groupId } : {}),
      ...(params.owner ? { owner: params.owner } : {}),
    },
    responseType: "blob",
  });
  const filename = filenameFromDisposition(response.headers["content-disposition"], params.fallbackName ?? "usage.csv");
  const blob = new Blob([response.data], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function readApiError(err: unknown, fallback: string) {
  const data = (err as { response?: { data?: unknown; status?: number } })?.response?.data;
  if (data instanceof Blob) {
    const text = await data.text();
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) return parsed.detail;
    } catch {
      if (text) return text;
    }
  }
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail ?? fallback;
}
