/**
 * System prompt construction for the JSON-DSL view generator.
 *
 * Composes:
 *   1. chad-fleet/DESIGN.md  — design language, color/spacing, anti-AI-slop
 *      guardrails, brand posture.
 *   2. Primitive schemas     — the JSON DSL allowlist (Card/Table/Badge/...).
 *   3. Output contract       — strict JSON, single ViewSpec, no prose.
 *
 * The DESIGN.md content is loaded once and cached.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PRIMITIVE_NAMES, PRIMITIVE_SCHEMAS } from './primitives.js';

function moduleDir(): string {
  const url = import.meta.url;
  if (url.startsWith('file:')) {
    return dirname(fileURLToPath(url));
  }
  // Vitest / non-file environments: fall back to cwd. The env override is the
  // expected resolution path in tests anyway.
  return process.cwd();
}

let designCache: string | null = null;

/**
 * Returns the contents of DESIGN.md as a string.
 * Resolution: GENUI_DESIGN_PATH env override, then monorepo root.
 * Cached after first read. Throws if the file cannot be read.
 */
export function loadDesignPrompt(): string {
  if (designCache !== null) return designCache;

  const envPath = process.env['GENUI_DESIGN_PATH'];
  // src/ at runtime via tsx, dist/ after build — both two levels deep from package root.
  // Monorepo root is three levels up from package root.
  const designPath =
    envPath != null && envPath.trim() !== ''
      ? envPath.trim()
      : resolve(moduleDir(), '../../../DESIGN.md');

  try {
    designCache = readFileSync(designPath, 'utf8');
    return designCache;
  } catch (err) {
    throw new Error(
      `genui-renderer: cannot read DESIGN.md at ${designPath}: ${String(err)}`,
    );
  }
}

/** Clear the cached design prompt (useful in tests). */
export function clearDesignPromptCache(): void {
  designCache = null;
}

/**
 * Build the full system prompt for a generation call.
 *
 * @param includeDesign — when false, skips DESIGN.md (used in tests where the
 *                        file may not exist or where verifying primitive content
 *                        is the only goal).
 */
export function buildSystemPrompt(includeDesign: boolean = true): string {
  const sections: string[] = [];

  if (includeDesign) {
    try {
      sections.push('# Design Language\n\n' + loadDesignPrompt());
    } catch {
      // DESIGN.md missing — fall through; primitive contract alone is enough
      // for the generator to function. This matches the behavior tests need
      // when the file is absent.
    }
  }

  sections.push('# JSON DSL Output Contract');
  sections.push(
    [
      'You output a SINGLE JSON object matching this TypeScript interface:',
      '',
      '```ts',
      'interface ViewSpec {',
      '  view: ViewNode[];        // ordered top-level nodes',
      "  narrative?: string;      // 1-2 sentence summary, plain prose",
      '}',
      '```',
      '',
      'Each `ViewNode` is a discriminated union over `primitive`:',
      '',
      ...PRIMITIVE_NAMES.map((name) => `- **${name}** — \`${PRIMITIVE_SCHEMAS[name]}\``),
      '',
      'Rules:',
      '- Output JSON ONLY. No prose, no markdown fences, no commentary.',
      '- Use only the primitives listed above.',
      '- Apply the design language (color tones, spacing posture, anti-AI-slop)',
      '  by choosing tones and structure thoughtfully — never invent new fields.',
      '- Tone vocabulary is fixed: "info" | "success" | "warning" | "error".',
      '- Prefer Card grouping for related nodes; nest leaf primitives as children.',
      '- Stat for KPIs, Table for rows, Timeline for time-ordered events,',
      '  Chart for line/bar series, Badge for status flags.',
    ].join('\n'),
  );

  return sections.join('\n\n');
}

/**
 * Build a corrective retry prompt when the first generation failed validation.
 * Caller passes the user's original request and the validation error.
 */
export function buildRetryPrompt(originalRequest: string, errorDetail: string): string {
  return [
    'Your previous response failed validation. Fix it.',
    '',
    'Original request:',
    originalRequest,
    '',
    'Validation error:',
    errorDetail,
    '',
    'Output a single corrected ViewSpec JSON object. JSON only, no prose.',
  ].join('\n');
}
