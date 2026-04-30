/**
 * Tests for the /api/views proxy routes (list, create, get, delete, pin, unpin).
 *
 * Mocks global fetch to simulate the upstream view-registry on :8108.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('next/server', () => ({
  NextResponse: {
    json: (body: unknown, init?: ResponseInit) => ({
      json: () => Promise.resolve(body),
      status: init?.status ?? 200,
    }),
  },
}));

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('/api/views (list)', () => {
  it('GET returns items array on upstream success', async () => {
    const items = [{ id: 'a', name: 'A', prompt: 'p', pinned: false }];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve(items),
    });
    const { GET } = await import('../app/api/views/route');
    const res = await GET();
    const body = await res.json();
    expect(body).toEqual({ items });
  });

  it('GET returns empty + error when upstream is down', async () => {
    mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED'));
    const { GET } = await import('../app/api/views/route');
    const res = await GET();
    const body = await res.json();
    expect(body.items).toEqual([]);
    expect(body.error).toContain('ECONNREFUSED');
  });
});

describe('/api/views (create)', () => {
  it('POST forwards the JSON body to upstream and returns 201 with view', async () => {
    const created = { id: 'my-view', name: 'My View', prompt: 'show stuff', pinned: false };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: () => Promise.resolve(created),
      text: () => Promise.resolve(''),
    });
    const { POST } = await import('../app/api/views/route');
    const req = new Request('http://localhost/api/views', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'My View', prompt: 'show stuff' }),
    });
    const res = await POST(req);
    const body = await res.json();
    expect(body).toEqual({ view: created });
    expect(res.status).toBe(201);
  });

  it('POST returns 400 on invalid JSON body', async () => {
    const { POST } = await import('../app/api/views/route');
    const req = new Request('http://localhost/api/views', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: 'not-json{{',
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it('POST surfaces upstream non-2xx with original status code', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('validation error'),
    });
    const { POST } = await import('../app/api/views/route');
    const req = new Request('http://localhost/api/views', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '', prompt: '' }),
    });
    const res = await POST(req);
    expect(res.status).toBe(422);
  });
});

describe('/api/views/[id]', () => {
  it('GET returns the view on success', async () => {
    const view = { id: 'x', name: 'X', prompt: 'p', pinned: true };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve(view),
    });
    const { GET } = await import('../app/api/views/[id]/route');
    const res = await GET(new Request('http://localhost'), {
      params: Promise.resolve({ id: 'x' }),
    });
    const body = await res.json();
    expect(body).toEqual({ view });
  });

  it('GET returns 404 when upstream returns 404', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    });
    const { GET } = await import('../app/api/views/[id]/route');
    const res = await GET(new Request('http://localhost'), {
      params: Promise.resolve({ id: 'missing' }),
    });
    expect(res.status).toBe(404);
  });

  it('DELETE returns ok:true on success', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204, json: () => Promise.resolve({}) });
    const { DELETE } = await import('../app/api/views/[id]/route');
    const res = await DELETE(new Request('http://localhost'), {
      params: Promise.resolve({ id: 'x' }),
    });
    const body = await res.json();
    expect(body).toEqual({ ok: true });
  });
});

describe('/api/views/[id]/pin and /unpin', () => {
  it('POST /pin proxies to upstream pin endpoint', async () => {
    const view = { id: 'x', name: 'X', prompt: 'p', pinned: true };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve(view),
    });
    const { POST } = await import('../app/api/views/[id]/pin/route');
    const res = await POST(new Request('http://localhost'), {
      params: Promise.resolve({ id: 'x' }),
    });
    const body = await res.json();
    expect(body).toEqual({ view });
    expect(mockFetch.mock.calls[0]?.[0]).toContain('/views/x/pin');
  });

  it('POST /unpin proxies to upstream unpin endpoint', async () => {
    const view = { id: 'x', name: 'X', prompt: 'p', pinned: false };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve(view),
    });
    const { POST } = await import('../app/api/views/[id]/unpin/route');
    const res = await POST(new Request('http://localhost'), {
      params: Promise.resolve({ id: 'x' }),
    });
    const body = await res.json();
    expect(body).toEqual({ view });
    expect(mockFetch.mock.calls[0]?.[0]).toContain('/views/x/unpin');
  });

  it('POST /pin returns 404 when upstream returns 404', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    });
    const { POST } = await import('../app/api/views/[id]/pin/route');
    const res = await POST(new Request('http://localhost'), {
      params: Promise.resolve({ id: 'missing' }),
    });
    expect(res.status).toBe(404);
  });
});
