import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import type { StreamEvent } from '../src/llm.js';

// Mock llm.ts before importing server
vi.mock('../src/llm.js', () => ({
  generateView: vi.fn(),
}));

const { generateView } = await import('../src/llm.js');
const { createApp } = await import('../src/server.js');

const mockGenerateView = vi.mocked(generateView);

beforeEach(() => {
  mockGenerateView.mockReset();
});

describe('GET /health', () => {
  it('returns { ok: true }', async () => {
    const app = createApp();
    const res = await request(app).get('/health');
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true });
  });
});

describe('POST /render', () => {
  it('returns 400 if state is missing', async () => {
    const app = createApp();
    const res = await request(app)
      .post('/render')
      .send({ request: 'show me stuff' })
      .set('Content-Type', 'application/json');
    expect(res.status).toBe(400);
  });

  it('returns 400 if request is missing', async () => {
    const app = createApp();
    const res = await request(app)
      .post('/render')
      .send({ state: { foo: 1 } })
      .set('Content-Type', 'application/json');
    expect(res.status).toBe(400);
  });

  it('returns 400 if state is not an object', async () => {
    const app = createApp();
    const res = await request(app)
      .post('/render')
      .send({ state: 'a string', request: 'test' })
      .set('Content-Type', 'application/json');
    expect(res.status).toBe(400);
  });

  it('streams SSE events for a valid request', async () => {
    mockGenerateView.mockImplementation(async (_state, _req, onEvent: (e: StreamEvent) => void) => {
      onEvent({
        type: 'final',
        view: [{ primitive: 'Badge', tone: 'success', label: 'Done' }],
        narrative: 'All good.',
      });
    });

    const app = createApp();
    const res = await request(app)
      .post('/render')
      .send({ state: { x: 1 }, request: 'what is x?' })
      .set('Content-Type', 'application/json')
      .buffer(true)
      .parse((res, callback) => {
        let data = '';
        res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
        res.on('end', () => callback(null, data));
      });

    expect(res.status).toBe(200);
    expect(res.headers['content-type']).toContain('text/event-stream');

    const body = res.body as string;
    expect(body).toContain('data:');
    expect(body).toContain('"type":"final"');
    expect(body).toContain('"label":"Done"');
  });

  it('streams error event when generateView emits error', async () => {
    mockGenerateView.mockImplementation(async (_state, _req, onEvent: (e: StreamEvent) => void) => {
      onEvent({ type: 'error', message: 'LLM blew up' });
    });

    const app = createApp();
    const res = await request(app)
      .post('/render')
      .send({ state: {}, request: 'test error' })
      .set('Content-Type', 'application/json')
      .buffer(true)
      .parse((res, callback) => {
        let data = '';
        res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
        res.on('end', () => callback(null, data));
      });

    expect(res.status).toBe(200);
    const body = res.body as string;
    expect(body).toContain('"type":"error"');
    expect(body).toContain('LLM blew up');
  });

  it('calls generateView with the correct state and request', async () => {
    mockGenerateView.mockImplementation(async () => { /* no-op */ });

    const app = createApp();
    await request(app)
      .post('/render')
      .send({ state: { project: 'alpha' }, request: 'launch status' })
      .set('Content-Type', 'application/json')
      .buffer(true)
      .parse((res, callback) => {
        let data = '';
        res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
        res.on('end', () => callback(null, data));
      });

    expect(mockGenerateView).toHaveBeenCalledOnce();
    const [stateArg, requestArg] = mockGenerateView.mock.calls[0] ?? [];
    expect(stateArg).toEqual({ project: 'alpha' });
    expect(requestArg).toBe('launch status');
  });
});
