/**
 * claude-cli provider — wraps the local `claude -p` subprocess (Pro/Max OAuth, no API key).
 * The default backend; requires Claude Code installed and authed on the host.
 */
import { claudeComplete } from '../subprocess.js';
import type { LLMProvider, LLMCompleteOptions } from './index.js';

export const claudeCliProvider: LLMProvider = {
  name: 'claude-cli',
  complete(prompt: string, options: LLMCompleteOptions = {}): Promise<string> {
    return claudeComplete(prompt, {
      model: options.model ?? 'sonnet',
      ...(options.system !== undefined ? { system: options.system } : {}),
      ...(options.timeout !== undefined ? { timeout: options.timeout } : {}),
    });
  },
};
