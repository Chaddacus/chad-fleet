import { z } from 'zod';

// Recursive ViewNode schema — each node references a primitive by name
// and carries primitive-specific props plus optional children.

const BadgeNodeSchema = z.object({
  primitive: z.literal('Badge'),
  tone: z.enum(['info', 'success', 'warning', 'error']),
  label: z.string(),
});

const StatNodeSchema = z.object({
  primitive: z.literal('Stat'),
  label: z.string(),
  value: z.union([z.string(), z.number()]),
  delta: z.string().optional(),
  tone: z.enum(['info', 'success', 'warning', 'error']).optional(),
});

const TableNodeSchema = z.object({
  primitive: z.literal('Table'),
  headers: z.array(z.string()),
  rows: z.array(z.array(z.union([z.string(), z.number(), z.null()]))),
});

const ChartNodeSchema = z.object({
  primitive: z.literal('Chart'),
  kind: z.enum(['line', 'bar']),
  data: z.array(z.object({ x: z.unknown(), y: z.number() })),
  xLabel: z.string().optional(),
  yLabel: z.string().optional(),
});

const TimelineItemSchema = z.object({
  ts: z.string(),
  label: z.string(),
  body: z.string().optional(),
});

const TimelineNodeSchema = z.object({
  primitive: z.literal('Timeline'),
  items: z.array(TimelineItemSchema),
});

// Card is recursive — its children can be any ViewNode
export type ViewNode =
  | z.infer<typeof BadgeNodeSchema>
  | z.infer<typeof StatNodeSchema>
  | z.infer<typeof TableNodeSchema>
  | z.infer<typeof ChartNodeSchema>
  | z.infer<typeof TimelineNodeSchema>
  | CardNode;

export interface CardNode {
  primitive: 'Card';
  title: string;
  subtitle?: string;
  tone?: 'info' | 'success' | 'warning' | 'error';
  children?: ViewNode[];
}

// Leaf nodes for Zod (no children)
const LeafNodeSchema = z.discriminatedUnion('primitive', [
  BadgeNodeSchema,
  StatNodeSchema,
  TableNodeSchema,
  ChartNodeSchema,
  TimelineNodeSchema,
]);

// Card schema — children are leaf nodes (one level of nesting is enough for the DSL)
const CardNodeSchema: z.ZodType<CardNode> = z.lazy(() =>
  z.object({
    primitive: z.literal('Card'),
    title: z.string(),
    subtitle: z.string().optional(),
    tone: z.enum(['info', 'success', 'warning', 'error']).optional(),
    children: z.array(ViewNodeSchema).optional(),
  }),
);

export const ViewNodeSchema: z.ZodType<ViewNode> = z.union([
  CardNodeSchema,
  LeafNodeSchema,
]);

export const ViewSpecSchema = z.object({
  view: z.array(ViewNodeSchema),
  narrative: z.string().optional(),
});

export type ViewSpec = z.infer<typeof ViewSpecSchema>;
export type TimelineItem = z.infer<typeof TimelineItemSchema>;
