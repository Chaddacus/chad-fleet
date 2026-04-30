/**
 * Tests for /views (library) and /views/[id] (detail) pages.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('a', { href, ...rest }, children),
}));

vi.mock('next/headers', () => ({
  headers: async () =>
    new Map([
      ['host', 'localhost:3000'],
      ['x-forwarded-proto', 'http'],
    ]),
}));

vi.mock('next/server', () => ({
  NextResponse: {
    json: (body: unknown, init?: ResponseInit) => ({
      json: () => Promise.resolve(body),
      status: init?.status ?? 200,
    }),
  },
}));

// Stub ViewClient to avoid rendering its useEffect-driven streaming logic in
// happy-dom — the page-level tests just need to know the component is mounted.
vi.mock('../app/views/[id]/ViewClient', () => ({
  default: ({ viewId }: { viewId: string }) =>
    React.createElement('div', { 'data-testid': `viewclient-${viewId}` }, 'view-client-stub'),
}));

const mockFetch = vi.fn();
beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('ViewsPage (library)', () => {
  const sampleView = {
    id: 'v-1',
    name: 'My View',
    description: 'first',
    prompt: 'show fleet status',
    app_scope: [],
    pinned: false,
    tags: ['ops'],
    created_at: '2026-04-30T00:00:00Z',
    updated_at: '2026-04-30T00:00:00Z',
  };

  it('renders one row per saved view', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [sampleView] }),
    });
    const { default: ViewsPage } = await import('../app/views/page');
    const jsx = await ViewsPage();
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('views-list')).toBeDefined();
    expect(screen.getByTestId('view-row-v-1')).toBeDefined();
    expect(screen.getByText('My View')).toBeDefined();
    expect(screen.getByText('show fleet status')).toBeDefined();
  });

  it('orders pinned views first', async () => {
    const pinned = { ...sampleView, id: 'v-pinned', name: 'Pinned!', pinned: true };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [sampleView, pinned] }),
    });
    const { default: ViewsPage } = await import('../app/views/page');
    const jsx = await ViewsPage();
    const { container } = render(jsx as React.ReactElement);
    const rows = container.querySelectorAll('[data-testid^="view-row-"]');
    expect(rows[0]?.getAttribute('data-testid')).toBe('view-row-v-pinned');
    expect(rows[1]?.getAttribute('data-testid')).toBe('view-row-v-1');
  });

  it('renders empty state when no views', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [] }),
    });
    const { default: ViewsPage } = await import('../app/views/page');
    const jsx = await ViewsPage();
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('views-empty')).toBeDefined();
  });

  it('renders error state when upstream proxy returns error', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [], error: 'connection refused' }),
    });
    const { default: ViewsPage } = await import('../app/views/page');
    const jsx = await ViewsPage();
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('views-error')).toBeDefined();
  });
});

describe('ViewDetailPage', () => {
  const sampleView = {
    id: 'v-2',
    name: 'Detail View',
    description: 'detail desc',
    prompt: 'render the inbox',
    app_scope: [],
    pinned: false,
    tags: [],
    created_at: '2026-04-30T00:00:00Z',
    updated_at: '2026-04-30T00:00:00Z',
  };

  it('renders the view name, prompt, and ViewClient', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ view: sampleView }),
    });
    const { default: ViewDetailPage } = await import('../app/views/[id]/page');
    const jsx = await ViewDetailPage({ params: Promise.resolve({ id: 'v-2' }) });
    render(jsx as React.ReactElement);

    expect(screen.getByText('Detail View')).toBeDefined();
    expect(screen.getByText('render the inbox')).toBeDefined();
    expect(screen.getByTestId('viewclient-v-2')).toBeDefined();
  });

  it('renders not-found state when proxy returns 404', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ view: null, error: 'not found' }),
    });
    const { default: ViewDetailPage } = await import('../app/views/[id]/page');
    const jsx = await ViewDetailPage({ params: Promise.resolve({ id: 'missing' }) });
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('view-not-found')).toBeDefined();
  });
});
