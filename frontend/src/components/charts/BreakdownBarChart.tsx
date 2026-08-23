import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatCurrency, formatRate, formatTokens } from "@/lib/format";
import { BreakdownItem } from "@/types";

interface BreakdownBarChartProps {
  data: BreakdownItem[];
  metric: "total_tokens" | "estimated_cost_usd" | "estimated_cost" | "actual_cost" | "new_api_cost" | "avg_tpm";
  currency?: string;
}

export default function BreakdownBarChart({ data, metric, currency = "USD" }: BreakdownBarChartProps) {
  const formatter = (value: number) => {
    if (metric === "avg_tpm") return formatRate(value);
    if (metric === "total_tokens") return formatTokens(value);
    return formatCurrency(value, currency);
  };
  return (
    <div className="h-56 w-full min-w-0 sm:h-64">
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 12, left: 4, bottom: 4 }}>
        <CartesianGrid stroke="#242836" strokeDasharray="4 4" horizontal={false} />
        <XAxis type="number" tickFormatter={formatter} stroke="#4b5563" fontSize={11} tickLine={false} axisLine={false} />
        <YAxis
          type="category"
          dataKey="name"
          stroke="#4b5563"
          fontSize={11}
          width={88}
          tickLine={false}
          axisLine={false}
          tickFormatter={(name: string) => (name.length > 12 ? `${name.slice(0, 12)}…` : name)}
        />
        <Tooltip
          contentStyle={{ background: "#161923", border: "1px solid #242836", borderRadius: 8, fontSize: 12 }}
          formatter={(value: number) => formatter(value)}
          labelFormatter={(label) => String(label)}
        />
        <Bar dataKey={metric} fill="#6366f1" radius={[0, 6, 6, 0]} />
      </BarChart>
    </ResponsiveContainer>
    </div>
  );
}
