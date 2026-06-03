/**
 * api provider — OpenAI-compatible chat-completions over HTTP.
 * The distribution backend: runs anywhere with LLM_API_KEY + LLM_BASE_URL, no Claude Code.
 * Works against OpenAI, Anthropic's OpenAI-compatible endpoint, or any compatible gateway.
 */
import { LLMError } from '../subprocess.js';
import type { LLMProvider, LLMCompleteOptions } from './index.js';

const DEFAULT_BASE_URL = 'https://api.openai.com/v1';
const DEFAULT_MODEL = 'gpt-4o-mini';

export const apiProvider: LLMProvider = {
  name: 'api',
  async complete(prompt: string, options: LLMCompleteOptions = {}): Promise<string> {
    const apiKey = process.env['LLM_API_KEY'];
    if (apiKey == null || apiKey.trim() === '') {
      throw new LLMError('LLM_API_KEY not set (required for GENUI_LLM_PROVIDER=api)');
    }
    const baseUrl = (process.env['LLM_BASE_URL'] ?? DEFAULT_BASE_URL).replace(/\/+$/, '');
    const model = options.model ?? process.env['LLM_MODEL'] ?? DEFAULT_MODEL;
    const timeoutMs = (options.timeout ?? 90) * 1000;

    const messages: Array<{ role: string; content: string }> = [];
    if (options.system != null && options.system.trim() !== '') {
      messages.push({ role: 'system', content: options.system });
    }
    messages.push({ role: 'user', content: prompt });

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let res: Response;
    try {
      res = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${apiKey.trim()}`,
        },
        body: JSON.stringify({ model, messages, temperature: 0 }),
        signal: controller.signal,
      });
    } catch (err) {
      const aborted = err instanceof Error && err.name === 'AbortError';
      throw new LLMError(aborted ? `api timeout after ${timeoutMs / 1000}s` : `api request failed: ${String(err)}`);
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      const body = (await res.text().catch(() => '')).slice(0, 500).trim();
      throw new LLMError(`api HTTP ${String(res.status)}: ${body}`);
    }

    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      throw new LLMError('api returned non-JSON');
    }

    const text = extractContent(payload);
    if (text == null || text.trim() === '') {
      throw new LLMError('api returned empty completion');
    }
    return text.trim();
  },
};

function extractContent(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object') return null;
  const choices = (payload as Record<string, unknown>)['choices'];
  if (!Array.isArray(choices) || choices.length === 0) return null;
  const first = choices[0];
  if (first === null || typeof first !== 'object') return null;
  const message = (first as Record<string, unknown>)['message'];
  if (message === null || typeof message !== 'object') return null;
  const content = (message as Record<string, unknown>)['content'];
  return typeof content === 'string' ? content : null;
}
