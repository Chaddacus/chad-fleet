import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getProvider, claudeCliProvider, apiProvider } from '../src/providers/index.js';

const ORIG = { ...process.env };

beforeEach(() => {
  delete process.env['GENUI_LLM_PROVIDER'];
  delete process.env['LLM_API_KEY'];
  delete process.env['LLM_BASE_URL'];
  delete process.env['LLM_MODEL'];
});

afterEach(() => {
  process.env = { ...ORIG };
  vi.restoreAllMocks();
});

describe('getProvider', () => {
  it('defaults to claude-cli when unset', () => {
    expect(getProvider()).toBe(claudeCliProvider);
  });

  it('selects api when GENUI_LLM_PROVIDER=api', () => {
    process.env['GENUI_LLM_PROVIDER'] = 'api';
    expect(getProvider()).toBe(apiProvider);
  });

  it('throws on an unknown provider name', () => {
    process.env['GENUI_LLM_PROVIDER'] = 'bogus';
    expect(() => getProvider()).toThrow(/unknown GENUI_LLM_PROVIDER/);
  });
});

describe('apiProvider', () => {
  it('throws when LLM_API_KEY is missing', async () => {
    await expect(apiProvider.complete('hi')).rejects.toThrow(/LLM_API_KEY not set/);
  });

  it('posts OpenAI-compatible chat completions and returns the content', async () => {
    process.env['LLM_API_KEY'] = 'sk-test';
    process.env['LLM_BASE_URL'] = 'https://gw.example/v1';
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ choices: [{ message: { content: '  hello world  ' } }] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const out = await apiProvider.complete('prompt text', { system: 'sys', model: 'gpt-x' });
    expect(out).toBe('hello world');

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('https://gw.example/v1/chat/completions');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.model).toBe('gpt-x');
    expect(body.messages).toEqual([
      { role: 'system', content: 'sys' },
      { role: 'user', content: 'prompt text' },
    ]);
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer sk-test' });
  });

  it('throws LLMError on non-2xx', async () => {
    process.env['LLM_API_KEY'] = 'sk-test';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('nope', { status: 500 })),
    );
    await expect(apiProvider.complete('x')).rejects.toThrow(/api HTTP 500/);
  });
});
