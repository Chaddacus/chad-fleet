import React from 'react';
import type { ViewNode } from '../../schema.js';

export interface CardProps {
  title: string;
  subtitle?: string;
  tone?: 'info' | 'success' | 'warning' | 'error';
  children?: ViewNode[];
  renderNode: (node: ViewNode) => React.ReactElement | null;
}

const toneHeaderClasses: Record<string, string> = {
  info: 'border-blue-300',
  success: 'border-green-300',
  warning: 'border-yellow-300',
  error: 'border-red-300',
};

export function Card({ title, subtitle, tone, children, renderNode }: CardProps): React.ReactElement {
  const borderClass = tone != null ? (toneHeaderClasses[tone] ?? 'border-gray-200') : 'border-gray-200';

  return (
    <div className={`rounded-lg border ${borderClass} bg-white shadow-sm overflow-hidden`}>
      <div className="px-4 py-3 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        {subtitle != null && (
          <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>
        )}
      </div>
      {children != null && children.length > 0 && (
        <div className="px-4 py-3 flex flex-col gap-3">
          {children.map((child, i) => (
            <React.Fragment key={i}>{renderNode(child)}</React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
