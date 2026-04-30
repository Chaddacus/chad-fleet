// Server-component-safe chips: verdict colour pill + delta number.

import type { CaptainVerdict } from '@/lib/captainTypes';
import { verdictCls, verdictLabel } from '../lib/captainFormat';

export function VChip({ verdict }: { verdict: CaptainVerdict | null | undefined }) {
  if (!verdict) return null;
  return <span className={`verdict-chip ${verdictCls(verdict)}`}>{verdictLabel(verdict)}</span>;
}

export function Delta({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return null;
  return (
    <span className={`delta ${v >= 0 ? 'pos' : 'neg'}`}>
      {v >= 0 ? '+' : ''}
      {v.toFixed(1)}pp
    </span>
  );
}
