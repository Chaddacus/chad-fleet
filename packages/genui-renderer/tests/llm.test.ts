import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { StreamEvent } from '../src/llm.js';

// Mock the subprocess wrapper module. generateView in llm.ts imports
// claudeComplete from subprocess.js, so this replacement is picked up cleanly.
vi.mock('../src/subprocess.js', () => ({
  claudeComplete: vi.fn(),
  CLAUDE_BIN: '/fake/claude',
  LLMError: class LLMError extends Error {},
}));

const subprocess = await import('../src/subprocess.js');
const claudeComplete = vi.mocked(subprocess.claudeComplete);

const { generateView } = await import('../src/llm.js');

const VALID_VIEW_JSON = JSON.stringify({
  view: [
    { primitive: 'Badge', tone: 'success', label: 'All good' },
    {
      primitive: 'Card',
      title: 'Summary',
      children: [{ primitive: 'Stat', label: 'Count', value: 5 }],
    },
  ],
  narrative: 'Everything is fine.',
});

beforeEach(() => {
  claudeComplete.mockReset();
});

describe('generateView', () => {
  it('calls claudeComplete and emits a final event on valid output', async () => {
    claudeComplete.mockResolvedValueOnce(VALID_VIEW_JSON);

    const events: StreamEvent[] = [];
    await generateView({ projects: [] }, 'show me status', (e) => events.push(e), {
      skipDesign: true,
    });

    expect(claudeComplete).toHaveBeenCalledOnce();
    const finalEvent = events.find((e) => e.type === 'final');
    expect(finalEvent).toBeDefined();
    if (finalEvent?.type === 'final') {
      expect(finalEvent.narrative).toBe('Everything is fine.');
      expect(finalEvent.view).toHaveLength(2);
    }
  });

  it('includes the user state and request in the user message', async () => {
    claudeComplete.mockResolvedValueOnce(VALID_VIEW_JSON);

    await generateView({ foo: 'bar' }, 'my question', () => {}, { skipDesign: true });

    const callArgs = claudeComplete.mock.calls[0];
    expect(callArgs).toBeDefined();
    const userPrompt = callArgs![0];
    expect(userPrompt).toContain('"foo": "bar"');
    expect(userPrompt).toContain('my question');
  });

  it('passes a system prompt with all primitive names', async () => {
    claudeComplete.mockResolvedValueOnce(VALID_VIEW_JSON);

    await generateView({}, 'test', () => {}, { skipDesign: true });

    const callArgs = claudeComplete.mock.calls[0];
    expect(callArgs).toBeDefined();
    const opts = callArgs![1] ?? {};
    const system = (opts as { system?: string }).system ?? '';
    expect(system).toContain('Card');
    expect(system).toContain('Table');
    expect(system).toContain('Badge');
    expect(system).toContain('Timeline');
    expect(system).toContain('Stat');
    expect(system).toContain('Chart');
  });

  it('emits a partial event with the raw model output', async () => {
    claudeComplete.mockResolvedValueOnce(VALID_VIEW_JSON);

    const events: StreamEvent[] = [];
    await generateView({}, 'partial test', (e) => events.push(e), { skipDesign: true });

    const partial = events.find((e) => e.type === 'partial');
    expect(partial).toBeDefined();
    if (partial?.type === 'partial') {
      expect(partial.text).toContain('"primitive"');
    }
  });

  it('emits error event when both attempts return non-JSON', async () => {
    claudeComplete
      .mockResolvedValueOnce('not json at all %%%')
      .mockResolvedValueOnce('still not json');

    const events: StreamEvent[] = [];
    await generateView({}, 'bad test', (e) => events.push(e), { skipDesign: true });

    expect(claudeComplete).toHaveBeenCalledTimes(2);
    const errorEvent = events.find((e) => e.type === 'error');
    expect(errorEvent).toBeDefined();
  });

  it('retries once when schema validation fails and succeeds on retry', async () => {
    const badJson = JSON.stringify({
      view: [{ primitive: 'Badge', tone: 'purple', label: 'bad' }],
    });
    claudeComplete
      .mockResolvedValueOnce(badJson)
      .mockResolvedValueOnce(VALID_VIEW_JSON);

    const events: StreamEvent[] = [];
    await generateView({}, 'retry test', (e) => events.push(e), { skipDesign: true });

    expect(claudeComplete).toHaveBeenCalledTimes(2);
    const finalEvent = events.find((e) => e.type === 'final');
    expect(finalEvent).toBeDefined();
  });

  it('strips fenced code blocks from claude output', async () => {
    const fenced = '```json\n' + VALID_VIEW_JSON + '\n```';
    claudeComplete.mockResolvedValueOnce(fenced);

    const events: StreamEvent[] = [];
    await generateView({}, 'fenced test', (e) => events.push(e), { skipDesign: true });

    const finalEvent = events.find((e) => e.type === 'final');
    expect(finalEvent).toBeDefined();
  });

  it('emits error when claudeComplete throws on both attempts', async () => {
    claudeComplete
      .mockRejectedValueOnce(new Error('claude timeout after 90s'))
      .mockRejectedValueOnce(new Error('claude exit 1: rate limited'));

    const events: StreamEvent[] = [];
    await generateView({}, 'subprocess fail', (e) => events.push(e), { skipDesign: true });

    const errorEvent = events.find((e) => e.type === 'error');
    expect(errorEvent).toBeDefined();
  });
});
