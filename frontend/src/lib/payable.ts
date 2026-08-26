export const PAYABLE_RATE = 0.12;
export const BROKERAGE_RATE_1K = 1 / 3;
export const BROKERAGE_RATE_10K = 1 / 12;

export function amountPayableUsd(spendUsd: number | null | undefined): number {
  return Math.round(Math.max(Number(spendUsd) || 0, 0) * PAYABLE_RATE * 100) / 100;
}

export function payablePercentLabel(): string {
  return `${Math.round(PAYABLE_RATE * 100)}%`;
}

/** 1k-class grants: 33.3% of payable. 10k-class grants: 8.3% of payable. */
export function brokerageRate(grantUsd: number | null | undefined): number {
  const grant = Math.max(Number(grantUsd) || 0, 0);
  if (grant <= 0) return 0;
  return Math.round(Math.log10(grant)) <= 3 ? BROKERAGE_RATE_1K : BROKERAGE_RATE_10K;
}

export function brokerageUsd(
  grantUsd: number | null | undefined,
  payableUsd: number | null | undefined
): number {
  const payable = Math.max(Number(payableUsd) || 0, 0);
  if (payable <= 0) return 0;
  return Math.round(payable * brokerageRate(grantUsd) * 100) / 100;
}

function csvCell(value: string | number): string {
  const text = String(value);
  if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function payableOrNull(spend: number | null | undefined): number | null {
  if (spend == null || !Number.isFinite(Number(spend))) return null;
  return amountPayableUsd(spend);
}

function payableCell(value: number | null): string {
  return value == null ? "" : value.toFixed(2);
}

export function rowPayableUsd(row: {
  spendUsd?: number | null;
  spendO1Usd?: number | null;
  spendO2Usd?: number | null;
}): number {
  const o1 = payableOrNull(row.spendO1Usd);
  const o2 = payableOrNull(row.spendO2Usd);
  const combined = payableOrNull(row.spendUsd);
  if (o1 != null || o2 != null) return (o1 ?? 0) + (o2 ?? 0);
  return combined ?? 0;
}

export function downloadPayableCsv(
  rows: {
    name: string;
    newApiName?: string | null;
    owner?: string | null;
    endpoint?: string | null;
    spendUsd?: number | null;
    spendO1Usd?: number | null;
    spendO2Usd?: number | null;
    settled?: boolean;
  }[],
  filename = "amount-payable.csv",
  extras?: { unsettled?: number; settled?: number }
) {
  const o1Total = rows.reduce((sum, row) => sum + (payableOrNull(row.spendO1Usd) ?? 0), 0);
  const o2Total = rows.reduce((sum, row) => sum + (payableOrNull(row.spendO2Usd) ?? 0), 0);
  const o1O2Total = o1Total + o2Total;
  const grandTotal = rows.reduce((sum, row) => sum + rowPayableUsd(row), 0);

  const lines = [
    ["name", "owner", "newapi_name", "endpoint", "o1", "o2", "o1+o2", "grandtotal", "settled"].map(csvCell).join(","),
    ...rows.map((row) => {
      const o1 = payableOrNull(row.spendO1Usd);
      const o2 = payableOrNull(row.spendO2Usd);
      const combined = payableOrNull(row.spendUsd);
      const o1o2 = o1 != null || o2 != null ? (o1 ?? 0) + (o2 ?? 0) : null;
      const grand = combined ?? o1o2;
      return [
        csvCell(row.name),
        csvCell(row.owner || ""),
        csvCell(row.newApiName || ""),
        csvCell(row.endpoint || ""),
        csvCell(payableCell(o1)),
        csvCell(payableCell(o2)),
        csvCell(payableCell(o1o2)),
        csvCell(payableCell(grand)),
        csvCell(row.settled ? "settled" : ""),
      ].join(",");
    }),
    ["TOTAL", "", "", "", o1Total.toFixed(2), o2Total.toFixed(2), o1O2Total.toFixed(2), grandTotal.toFixed(2), ""]
      .map(csvCell)
      .join(","),
  ];
  if (extras?.unsettled != null) {
    lines.push(["UNSETTLED", "", "", "", "", "", "", extras.unsettled.toFixed(2), ""].map(csvCell).join(","));
  }
  if (extras?.settled != null) {
    lines.push(["SETTLED", "", "", "", "", "", "", extras.settled.toFixed(2), ""].map(csvCell).join(","));
  }
  const blob = new Blob(["\ufeff" + lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
