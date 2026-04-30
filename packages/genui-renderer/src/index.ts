// Server exports
export { createApp } from './server.js';
export { generateView } from './llm.js';
export type { StreamEvent, PartialEvent, FinalEvent, ErrorEvent, LLMOptions, EventCallback } from './llm.js';

// Schema exports
export { ViewSpecSchema, ViewNodeSchema } from './schema.js';
export type { ViewSpec, ViewNode, CardNode, TimelineItem } from './schema.js';

// Primitives exports
export { PRIMITIVE_NAMES, PRIMITIVE_SCHEMAS, isPrimitiveName } from './primitives.js';
export type { PrimitiveName } from './primitives.js';

// Prompt exports
export { buildSystemPrompt, buildRetryPrompt } from './prompt.js';

// NOTE: client-side renderer (GenView, renderViewNode) is exported from a
// separate entry point — `@chad-fleet/genui-renderer/client` — to keep the
// server-side Express stack out of the browser bundle.
