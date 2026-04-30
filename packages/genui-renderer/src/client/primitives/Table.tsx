import React from 'react';

export interface TableProps {
  headers: string[];
  rows: (string | number | null)[][];
}

export function Table({ headers, rows }: TableProps): React.ReactElement {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50">
            {headers.map((h, i) => (
              <th
                key={i}
                className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-gray-100 hover:bg-gray-50">
              {row.map((cell, ci) => (
                <td key={ci} className="px-3 py-2 text-gray-700">
                  {cell ?? '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
