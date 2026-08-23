import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatDateTime, formatTokens } from "@/lib/format";
import { TimeseriesPoint } from "@/types";

export interface SplitSeries {
  key: string;
  name: string;
  color: string;
}

interface UsageAreaChartProps {
  data: Array<TimeseriesPoint & { o1_only_tokens?: number; both_tokens?: number; o2_only_tokens?: number }>;
  split?: SplitSeries[];
}

export default function UsageAreaChart({ data, split }: UsageAreaChartProps) {
  return (
    <div className="h-52 w-full min-w-0 sm:h-64 lg:h-72">
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="tokenGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#242836" strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="bucket" tickFormatter={formatDateTime} stroke="#4b5563" fontSize={11} tickLine={false} axisLine={false} />
        <YAxis tickFormatter={formatTokens} stroke="#4b5563" fontSize={11} tickLine={false} axisLine={false} />
        <Tooltip
          contentStyle={{ background: "#161923", border: "1px solid #242836", borderRadius: 8, fontSize: 12 }}
          labelFormatter={(value) => formatDateTime(String(value))}
          formatter={(value: number, name: string) => [formatTokens(value), name]}
        />
        {split && split.length > 0 && <Legend wrapperStyle={{ fontSize: 11 }} iconType="plainline" />}
        <Area type="monotone" dataKey="total_tokens" name="Total tokens" stroke="#6366f1" fill="url(#tokenGradient)" strokeWidth={2} />
        {(split ?? []).map((series) => (
          <Line
            key={series.key}
            type="monotone"
            dataKey={series.key}
            name={series.name}
            stroke={series.color}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
    </div>
  );
}
