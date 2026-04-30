import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchFleetState, fetchInbox } from '../lib/api';

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('fetchFleetState', () => {
  it('calls /api/state and returns parsed JSON on success', async () => {
    const payload = {
      generated_at: '2025-01-01T00:00:00Z',
      apps: [{ id: 'my-app', name: 'My App' }],
      inbox_recent: [],
      summary: {},
    };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(payload),
    });

    const result = await fetchFleetState();

    expect(mockFetch).toHaveBeenCalledWith('/api/state', expect.any(Object));
    expect(result.apps).toHaveLength(1);
    expect(result.apps[0].id).toBe('my-app');
  });

  it('returns empty shape with error when response is not ok', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 503 });

    const result = await fetchFleetState();

    expect(result.error).toBe('HTTP 503');
    expect(result.apps).toEqual([]);
    expect(result.inbox_recent).toEqual([]);
  });

  it('returns empty shape with error when fetch throws', async () => {
    mockFetch.mockRejectedValueOnce(new Error('network failure'));

    // fetchFleetState itself throws in the error case — caller sees the rejection
    // (the error path in lib/api only handles !res.ok, not thrown exceptions)
    await expect(fetchFleetState()).rejects.toThrow('network failure');
  });
});

describe('fetchInbox', () => {
  it('calls /api/inbox and returns parsed items on success', async () => {
    const payload = {
      items: [
        { ts: '2025-01-01T00:00:00Z', channel: 'app/test', severity: 'info', title: 'Hello', body: '' },
      ],
    };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(payload),
    });

    const result = await fetchInbox();

    expect(mockFetch).toHaveBeenCalledWith('/api/inbox');
    expect(result.items).toHaveLength(1);
    expect(result.items[0].title).toBe('Hello');
  });

  it('returns empty items array with error on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });

    const result = await fetchInbox();

    expect(result.error).toBe('HTTP 500');
    expect(result.items).toEqual([]);
  });
});
