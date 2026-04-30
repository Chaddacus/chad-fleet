/**
 * Client-only entry point for @chad-fleet/genui-renderer.
 *
 * Exports the React renderer (GenView) and primitive router (renderViewNode)
 * plus the schema types needed by callers. No server-side imports — safe to
 * import from a browser bundle.
 */

export { GenView, renderViewNode } from './GenView.js';
export type { GenViewProps } from './GenView.js';

export type {
  ViewSpec,
  ViewNode,
  CardNode,
  TimelineItem,
} from '../schema.js';

export { ViewSpecSchema, ViewNodeSchema } from '../schema.js';

export { PRIMITIVE_NAMES, isPrimitiveName } from '../primitives.js';
export type { PrimitiveName } from '../primitives.js';
