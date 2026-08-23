import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EstimateCurrency, formatCurrency } from "@/lib/format";
import { BreakdownItem } from "@/types";

interface CostComparisonChartProps {
  data: BreakdownItem[];
  currency: EstimateCurrency;
  usdInr: number;
}

interface Row {
  name: string;
  estimated: number;
  actual: number;
  newapi: number;
  limit: number;
}

const SERIES = [
  { key: "estimated", label: "Estimated", color: "#818cf8" },
  { key: "actual", label: "Actual (Azure)", color: "#34d399" },
  { key: "newapi", label: "NewAPI", color: "#f0abfc" },
  { key: "limit", label: "Azure limit", color: "#fbbf24" },
] as const;

function toSelected(amount: number, fromCurrency: string | null | undefined, currency: EstimateCurrency, usdInr: number) {
  const from = (fromCurrency || "USD").toUpperCase();
  if (currency === "INR") return from === "INR" ? amount : amount * usdInr;
  return from === "INR" && usdInr > 0 ? amount / usdInr : amount;
}

export default function CostComparisonChart({ data, currency, usdInr }: CostComparisonChartProps) {
  const rows: Row[] = data
    .map((item) => ({
      name: item.name,
      estimated: toSelected(item.estimated_cost_usd ?? 0, "USD", currency, usdInr),
      actual: item.actual_cost == null ? 0 : toSelected(item.actual_cost, item.actual_cost_currency, currency, usdInr),
      newapi: toSelected(item.new_api_cost ?? 0, "USD", currency, usdInr),
      limit:
        item.credits_limit == null
          ? 0
          : toSelected(item.credits_limit, item.credits_currency || "USD", currency, usdInr),
    }))
    .filter((row) => row.estimated > 0 || row.actual > 0 || row.newapi > 0 || row.limit > 0)
    .sort((a, b) => Math.max(b.estimated, b.actual, b.newapi, b.limit) - Math.max(a.estimated, a.actual, a.newapi, a.limit));

  const fmt = (value: number) => formatCurrency(value, currency);

  if (rows.length === 0) {
    return <p className="py-16 text-center text-sm text-gray-500">No cost data in this range</p>;
  }

  return (
    <div className="w-full min-w-0" style={{ height: Math.max(280, rows.length * 76) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }} barCategoryGap="18%">
          <CartesianGrid stroke="#242836" strokeDasharray="4 4" horizontal={false} />
          <XAxis type="number" tickFormatter={fmt} stroke="#4b5563" fontSize={11} tickLine={false} axisLine={false} />
          <YAxis
            type="category"
            dataKey="name"
            stroke="#4b5563"
            fontSize={11}
            width={96}
            tickLine={false}
            axisLine={false}
            tickFormatter={(name: string) => (name.length > 13 ? `${name.slice(0, 13)}…` : name)}
          />
          <Tooltip
            cursor={{ fill: "rgba(99,102,241,0.06)" }}
            contentStyle={{ background: "#12151f", border: "1px solid #2b3040", borderRadius: 10, fontSize: 12 }}
            formatter={(value: number, key) => [fmt(value), SERIES.find((s) => s.key === key)?.label ?? key]}
            labelFormatter={(label) => String(label)}
          />
          <Legend
            formatter={(key: string) => (
              <span style={{ color: "#9ca3af", fontSize: 12 }}>{SERIES.find((s) => s.key === key)?.label ?? key}</span>
            )}
          />
          {SERIES.map((series) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              fill={series.color}
              radius={[0, 5, 5, 0]}
              maxBarSize={10}
              fillOpacity={series.key === "limit" ? 0.45 : 1}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
