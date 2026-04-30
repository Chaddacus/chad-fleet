import React from 'react';
import type { TimelineItem } from '../../schema.js';

export interface TimelineProps {
  items: TimelineItem[];
}

export function Timeline({ items }: TimelineProps): React.ReactElement {
  return (
    <ol className="relative border-l border-gray-200 ml-3">
      {items.map((item, i) => (
        <li key={i} className="mb-6 ml-4">
          <div className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border border-white bg-gray-400" />
          <time className="mb-1 text-xs text-gray-400">{item.ts}</time>
          <p className="text-sm font-medium text-gray-900">{item.label}</p>
          {item.body != null && (
            <p className="text-xs text-gray-500 mt-0.5">{item.body}</p>
          )}
        </li>
      ))}
    </ol>
  );
}
