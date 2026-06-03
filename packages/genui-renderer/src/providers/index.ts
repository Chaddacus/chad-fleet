/**
 * LLM backend selection for genui-renderer.
 *
 * Two providers, chosen by env so the renderer can ship without Claude Code:
 *   - claude-cli (default): local `claude` binary (Pro/Max OAuth). Dev / single-operator.
 *   - api: OpenAI-compatible HTTP endpoint via LLM_API_KEY + LLM_BASE_URL. Distribution.
 *
 * Pick exactly one via GENUI_LLM_PROVIDER; the two are not blended.
 */
import type { ClaudeCompleteOptions } from '../subprocess.js';
import { claudeCliProvider } from './claude-cli.js';
import { apiProvider } from './api.js';

export type LLMCompleteOptions = ClaudeCompleteOptions;

export interface LLMProvider {
  readonly name: string;
  complete(prompt: string, options?: LLMCompleteOptions): Promise<string>;
}

export function getProvider(): LLMProvider {
  const choice = (process.env['GENUI_LLM_PROVIDER'] ?? 'claude-cli').trim().toLowerCase();
  switch (choice) {
    case '':
    case 'claude-cli':
      return claudeCliProvider;
    case 'api':
      return apiProvider;
    default:
      throw new Error(
        `unknown GENUI_LLM_PROVIDER '${choice}' (expected 'claude-cli' or 'api')`,
      );
  }
}

export { claudeCliProvider } from './claude-cli.js';
export { apiProvider } from './api.js';
