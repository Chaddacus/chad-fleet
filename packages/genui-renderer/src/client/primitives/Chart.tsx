import React from 'react';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

export interface ChartDataPoint {
  x?: unknown;
  y: number;
}

export interface ChartProps {
  kind: 'line' | 'bar';
  data: ChartDataPoint[];
  xLabel?: string;
  yLabel?: string;
}

export function Chart({ kind, data, xLabel, yLabel }: ChartProps): React.ReactElement {
  // Recharts expects objects with named keys
  const rechartsData = data.map((d) => ({ x: String(d.x), y: d.y }));

  const commonProps = {
    data: rechartsData,
    margin: { top: 8, right: 16, bottom: xLabel != null ? 24 : 8, left: yLabel != null ? 24 : 8 },
  };

  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
      <XAxis
        dataKey="x"
        tick={{ fontSize: 11 }}
        label={xLabel != null ? { value: xLabel, position: 'insideBottom', offset: -8 } : undefined}
      />
      <YAxis
        tick={{ fontSize: 11 }}
        label={
          yLabel != null
            ? { value: yLabel, angle: -90, position: 'insideLeft', offset: 8 }
            : undefined
        }
      />
      <Tooltip />
    </>
  );

  return (
    <div className="w-full h-48">
      <ResponsiveContainer width="100%" height="100%">
        {kind === 'bar' ? (
          <BarChart {...commonProps}>
            {axes}
            <Bar dataKey="y" fill="#3b82f6" radius={[2, 2, 0, 0]} />
          </BarChart>
        ) : (
          <LineChart {...commonProps}>
            {axes}
            <Line type="monotone" dataKey="y" stroke="#3b82f6" dot={false} strokeWidth={2} />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
