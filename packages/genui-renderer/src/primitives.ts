// Canonical list of supported primitives and their argument shape descriptions.
// This is used both by the system prompt (server side) and by GenView (client side)
// to validate/route incoming view nodes.

export const PRIMITIVE_NAMES = [
  'Card',
  'Table',
  'Badge',
  'Timeline',
  'Stat',
  'Chart',
] as const;

export type PrimitiveName = (typeof PRIMITIVE_NAMES)[number];

export const PRIMITIVE_SCHEMAS: Record<PrimitiveName, string> = {
  Card: `{ primitive: "Card", title: string, subtitle?: string, tone?: "info"|"success"|"warning"|"error", children?: ViewNode[] }`,
  Table: `{ primitive: "Table", headers: string[], rows: (string|number|null)[][] }`,
  Badge: `{ primitive: "Badge", tone: "info"|"success"|"warning"|"error", label: string }`,
  Timeline: `{ primitive: "Timeline", items: { ts: string, label: string, body?: string }[] }`,
  Stat: `{ primitive: "Stat", label: string, value: string|number, delta?: string, tone?: "info"|"success"|"warning"|"error" }`,
  Chart: `{ primitive: "Chart", kind: "line"|"bar", data: { x: any, y: number }[], xLabel?: string, yLabel?: string }`,
};

export function isPrimitiveName(name: string): name is PrimitiveName {
  return (PRIMITIVE_NAMES as readonly string[]).includes(name);
}
