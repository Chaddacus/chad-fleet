/**
 * Page tests for chad-dashboard.
 *
 * Next.js App Router server components are async functions that can't be rendered
 * directly with @testing-library/react in a vitest/happy-dom environment (they rely on
 * Next.js internals for async context, Link, headers, etc.).
 *
 * Strategy:
 * - ChatPanel (client component) is rendered directly via RTL.
 * - Server page components (AppsPage, InboxPage, AppDetailPage) are called as plain async
 *   functions with `fetch` mocked, and their returned JSX is rendered with RTL's `render`.
 *   `next/link` is mocked to a plain <a> to avoid Next.js router dependency.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// --- Mock next/link so server component JSX renders without Next.js router ---
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('a', { href, ...rest }, children),
}));

// --- Mock next/server for the API route module (NextResponse) ---
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

// -------------------------------------------------------------------------
// ChatPanel (client component) — renders the chat input
// -------------------------------------------------------------------------
describe('HomePage / ChatPanel', () => {
  it('renders the chat textarea input', async () => {
    // ChatPanel calls fetch('/api/state') on mount — return a minimal response
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ apps: [], inbox_recent: [], summary: {}, generated_at: '' }),
    });

    const { default: ChatPanel } = await import('../app/ChatPanel');
    render(React.createElement(ChatPanel));

    expect(screen.getByTestId('chat-input')).toBeDefined();
  });
});

// -------------------------------------------------------------------------
// InboxPage — server component called as async function
// -------------------------------------------------------------------------
describe('InboxPage', () => {
  it('renders a table row per inbox item', async () => {
    const items = [
      { ts: '2025-01-01T00:00:00Z', channel: 'app/a', severity: 'info', title: 'Alert one', body: 'body1' },
      { ts: '2025-01-02T00:00:00Z', channel: 'app/b', severity: 'warn', title: 'Alert two', body: 'body2' },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items }),
    });

    const { default: InboxPage } = await import('../app/inbox/page');
    const jsx = await InboxPage();
    render(jsx as React.ReactElement);

    expect(screen.getByText('Alert one')).toBeDefined();
    expect(screen.getByText('Alert two')).toBeDefined();
    expect(screen.getByTestId('inbox-table')).toBeDefined();
  });

  it('renders empty state when no items', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [] }),
    });

    const { default: InboxPage } = await import('../app/inbox/page');
    const jsx = await InboxPage();
    render(jsx as React.ReactElement);

    expect(screen.getByText(/No notifications yet/i)).toBeDefined();
  });
});

// -------------------------------------------------------------------------
// AppsPage — server component called as async function
// -------------------------------------------------------------------------
describe('AppsPage', () => {
  it('renders a card per app', async () => {
    const apps = [
      {
        id: 'my-app',
        name: 'My App',
        state: 'active',
        mode: 'auto',
        cadence: 'hourly',
        owner_brand: 'test-owner',
        last_progress_at: new Date().toISOString(),
        obsessive_loop_runs: [],
        metadata: {},
      },
      {
        id: 'other-app',
        name: 'Other App',
        state: 'idle',
        mode: 'manual',
        cadence: 'daily',
        owner_brand: 'team',
        last_progress_at: new Date().toISOString(),
        obsessive_loop_runs: [],
        metadata: {},
      },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ apps, inbox_recent: [], summary: {}, generated_at: '' }),
    });

    const { default: AppsPage } = await import('../app/apps/page');
    const jsx = await AppsPage();
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('apps-list')).toBeDefined();
    expect(screen.getByText('My App')).toBeDefined();
    expect(screen.getByText('Other App')).toBeDefined();
  });

  it('renders empty-state message when no apps', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ apps: [], inbox_recent: [], summary: {}, generated_at: '' }),
    });

    const { default: AppsPage } = await import('../app/apps/page');
    const jsx = await AppsPage();
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('empty-state')).toBeDefined();
    expect(screen.getByText(/No apps tracked yet/i)).toBeDefined();
  });
});

// -------------------------------------------------------------------------
// AppDetailPage — server component called as async function
// -------------------------------------------------------------------------
describe('AppDetailPage', () => {
  const sampleApp = {
    id: 'fleet-app',
    name: 'Fleet App',
    state: 'active',
    mode: 'auto',
    cadence: 'hourly',
    owner_brand: 'chadco',
    last_progress_at: '2025-01-01T12:00:00Z',
    blocked_reason: null,
    obsessive_loop_runs: [
      { run_id: 'run-001', branch: 'main', weighted_avg: 0.95, repo: '/code/fleet-app' },
    ],
    metadata: { repo_path: '/code/fleet-app' },
  };

  it('renders metadata section for a found app', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ apps: [sampleApp], inbox_recent: [], summary: {}, generated_at: '' }),
    });

    const { default: AppDetailPage } = await import('../app/apps/[id]/page');
    const jsx = await AppDetailPage({ params: Promise.resolve({ id: 'fleet-app' }) });
    render(jsx as React.ReactElement);

    expect(screen.getByText('Fleet App')).toBeDefined();
    expect(screen.getByTestId('section-metadata')).toBeDefined();
    expect(screen.getByText('chadco')).toBeDefined();
  });

  it('renders obsessive-loop runs table', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ apps: [sampleApp], inbox_recent: [], summary: {}, generated_at: '' }),
    });

    const { default: AppDetailPage } = await import('../app/apps/[id]/page');
    const jsx = await AppDetailPage({ params: Promise.resolve({ id: 'fleet-app' }) });
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('section-runs')).toBeDefined();
    expect(screen.getByText('run-001')).toBeDefined();
    expect(screen.getByText('0.950')).toBeDefined();
  });

  it('renders 404 message when app id not found', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ apps: [], inbox_recent: [], summary: {}, generated_at: '' }),
    });

    const { default: AppDetailPage } = await import('../app/apps/[id]/page');
    const jsx = await AppDetailPage({ params: Promise.resolve({ id: 'missing-app' }) });
    render(jsx as React.ReactElement);

    expect(screen.getByText('App not found')).toBeDefined();
    expect(screen.getByText(/missing-app/)).toBeDefined();
  });

  it('renders inbox section filtered by app id', async () => {
    const inbox = [
      { ts: '2025-01-01T00:00:00Z', channel: 'app/fleet-app', severity: 'info', title: 'Fleet event', body: '' },
      { ts: '2025-01-01T00:00:00Z', channel: 'app/other', severity: 'warn', title: 'Other event', body: '' },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ apps: [sampleApp], inbox_recent: inbox, summary: {}, generated_at: '' }),
    });

    const { default: AppDetailPage } = await import('../app/apps/[id]/page');
    const jsx = await AppDetailPage({ params: Promise.resolve({ id: 'fleet-app' }) });
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('section-inbox')).toBeDefined();
    // should show fleet-app's event but not the other one
    expect(screen.getByText('Fleet event')).toBeDefined();
    expect(screen.queryByText('Other event')).toBeNull();
  });
});
