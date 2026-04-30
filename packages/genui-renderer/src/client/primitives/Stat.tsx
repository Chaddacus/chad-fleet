import React from 'react';

export interface StatProps {
  label: string;
  value: string | number;
  delta?: string;
  tone?: 'info' | 'success' | 'warning' | 'error';
}

const toneValueClasses: Record<string, string> = {
  info: 'text-blue-600',
  success: 'text-green-600',
  warning: 'text-yellow-600',
  error: 'text-red-600',
};

export function Stat({ label, value, delta, tone }: StatProps): React.ReactElement {
  const valueClass = tone != null ? (toneValueClasses[tone] ?? 'text-gray-900') : 'text-gray-900';

  return (
    <div className="flex flex-col gap-1 p-2">
      <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-2xl font-semibold ${valueClass}`}>{value}</span>
      {delta != null && (
        <span className="text-xs text-gray-500">{delta}</span>
      )}
    </div>
  );
}
