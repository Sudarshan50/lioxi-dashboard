import { useState } from "react";
import { Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from "recharts";

import Modal from "@/components/ui/Modal";
import { formatDateTime, formatRate } from "@/lib/format";
import { AccountTpmPoint } from "@/types";

const COLORS = ["#6366f1", "#22d3ee", "#f59e0b", "#34d399", "#f43f5e", "#a78bfa", "#38bdf8", "#fb7185", "#84cc16", "#e879f9"];

export default function TpmByAccountChart({ data }: { data: AccountTpmPoint[] }) {
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null);
  const accounts = [...new Map(data.map((point) => [point.account_name, point.account_id])).keys()];
  const buckets = [...new Set(data.map((point) => point.bucket))];
  const lookup = new Map(data.map((point) => [`${point.bucket}:${point.account_name}`, point.tpm]));
  const chartData = buckets.map((bucket) => {
    const row: Record<string, string | number> = { bucket };
    for (const name of accounts) {
      row[name] = lookup.get(`${bucket}:${name}`) ?? 0;
    }
    return row;
  });
  const selectedRows = selectedBucket
    ? accounts
        .map((name, index) => ({
          name,
          color: COLORS[index % COLORS.length],
          tpm: Number(chartData.find((row) => row.bucket === selectedBucket)?.[name] ?? 0),
        }))
        .sort((a, b) => b.tpm - a.tpm)
    : [];

  return (
    <>
      <div className="h-56 w-full min-w-0 cursor-pointer sm:h-64 lg:h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={chartData}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
            onClick={(state) => {
              if (state?.activeLabel) setSelectedBucket(String(state.activeLabel));
            }}
          >
            <CartesianGrid stroke="#242836" strokeDasharray="4 4" vertical={false} />
            <XAxis dataKey="bucket" tickFormatter={formatDateTime} stroke="#4b5563" fontSize={11} tickLine={false} axisLine={false} />
            <YAxis tickFormatter={formatRate} stroke="#4b5563" fontSize={11} tickLine={false} axisLine={false} />
            <Tooltip content={() => null} cursor={{ stroke: "#6366f1", strokeWidth: 1, strokeDasharray: "4 4" }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {accounts.map((name, index) => (
              <Line
                key={name}
                type="monotone"
                dataKey={name}
                stroke={COLORS[index % COLORS.length]}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      <Modal
        title={selectedBucket ? `TPM by account · ${formatDateTime(selectedBucket)}` : "TPM by account"}
        isOpen={selectedBucket != null}
        onClose={() => setSelectedBucket(null)}
        widthClassName="max-w-xl"
      >
        <p className="mb-3 text-xs text-gray-500">Hourly tokens ÷ 60 for this hour. Scroll to see every account.</p>
        <ul className="max-h-[60vh] space-y-2">
          {selectedRows.map((row) => (
            <li key={row.name} className="flex items-start justify-between gap-4 border-b border-surface-border/60 py-2 last:border-0">
              <span className="flex min-w-0 items-start gap-2">
                <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: row.color }} />
                <span className="break-words text-sm text-gray-200">{row.name}</span>
              </span>
              <span className="shrink-0 text-sm tabular-nums text-gray-100">{formatRate(row.tpm)}</span>
            </li>
          ))}
        </ul>
      </Modal>
    </>
  );
}
