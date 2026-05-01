/**
 * Captain dashboard tests.
 *
 * Server components are called as async functions with `fetch` mocked,
 * returned JSX rendered via @testing-library/react.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { AppStateBundle, FleetBundle } from '@/lib/captainTypes';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('a', { href, ...rest }, children),
}));

vi.mock('next/navigation', () => ({
  notFound: () => {
    throw new Error('NEXT_NOT_FOUND');
  },
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
});

// Helper: build a minimally-populated AppStateBundle.
function bundle(overrides: Partial<AppStateBundle>): AppStateBundle {
  return {
    app_id: 'sample-app',
    name: 'Sample App',
    mode: 'autonomous',
    repo_path: '/code/sample',
    current_slice: null,
    roadmap: null,
    captain_log_tail: [],
    progress_tail: [],
    unread_admiral_notes: [],
    scorecard: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// L1 — fleet overview
// ---------------------------------------------------------------------------
describe('CaptainL1 (fleet overview)', () => {
  it('renders empty state when no apps registered', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ generated_at: '', count: 0, apps: [] } satisfies FleetBundle),
    });

    const { default: CaptainL1 } = await import('../app/captain/page');
    const jsx = await CaptainL1();
    render(jsx as React.ReactElement);

    expect(screen.getByText(/No apps registered/i)).toBeDefined();
    expect(screen.getByText(/chad-captain register/i)).toBeDefined();
  });

  it('renders unreachable state when API fails', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });

    const { default: CaptainL1 } = await import('../app/captain/page');
    const jsx = await CaptainL1();
    render(jsx as React.ReactElement);

    expect(screen.getByText(/Captain API unreachable/i)).toBeDefined();
    expect(screen.getByText(/uv run chad-captain-api/i)).toBeDefined();
  });

  it('renders one row per registered app', async () => {
    const fleet: FleetBundle = {
      generated_at: '2026-04-29T12:00:00Z',
      count: 2,
      apps: [
        bundle({ app_id: 'spark-of-defiance', name: 'Spark of Defiance' }),
        bundle({
          app_id: 'author-toolkit',
          name: 'Author Toolkit',
          mode: 'observe_only',
          unread_admiral_notes: ['note-1'],
          // escalation surfaces via a captain_log validate entry with verdict=escalate
          captain_log_tail: [
            {
              ts: '2026-04-29T12:00:00Z',
              app_id: 'author-toolkit',
              slice_id: null,
              kind: 'validate',
              verdict: 'escalate',
              rubric_delta_pp: null,
              rationale: 'human review needed',
              references: {},
            },
          ],
        }),
      ],
    };

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(fleet),
    });

    const { default: CaptainL1 } = await import('../app/captain/page');
    const jsx = await CaptainL1();
    render(jsx as React.ReactElement);

    expect(screen.getByTestId('fleet-list')).toBeDefined();
    // L1 row title is the app_id (slug), not display name
    expect(screen.getByText('spark-of-defiance')).toBeDefined();
    expect(screen.getByText('author-toolkit')).toBeDefined();
    expect(screen.getByText(/2 apps registered/i)).toBeDefined();
    // Escalating app surfaces NEEDS YOU marker
    expect(screen.getByText(/NEEDS YOU/i)).toBeDefined();
    // Unread pip shows count
    expect(screen.getByText('1')).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// L2 — app detail (server shell)
// ---------------------------------------------------------------------------
describe('CaptainL2 (app detail shell)', () => {
  it('renders the app shell with back link, app id, and mode', async () => {
    const app = bundle({
      app_id: 'spark-of-defiance',
      name: 'Spark of Defiance',
      mode: 'autonomous',
    });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(app),
    });

    const { default: CaptainL2 } = await import('../app/captain/[appId]/page');
    const jsx = await CaptainL2({ params: { appId: 'spark-of-defiance' } });
    render(jsx as React.ReactElement);

    expect(screen.getByText('spark-of-defiance')).toBeDefined();
    expect(screen.getByText(/← fleet/)).toBeDefined();
    expect(screen.getByText('autonomous')).toBeDefined();
  });

  it('calls notFound when app id is missing', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404 });

    const { default: CaptainL2 } = await import('../app/captain/[appId]/page');
    await expect(
      CaptainL2({ params: { appId: 'missing-app' } }),
    ).rejects.toThrow('NEXT_NOT_FOUND');
  });
});

// ---------------------------------------------------------------------------
// L2 PR status banner — derived from captain_log_tail
// ---------------------------------------------------------------------------

function logEntry(overrides: {
  kind: AppStateBundle['captain_log_tail'][number]['kind'];
  rationale?: string;
  references?: Record<string, string>;
  ts?: string;
}): AppStateBundle['captain_log_tail'][number] {
  return {
    ts: overrides.ts ?? new Date().toISOString(),
    app_id: 'sample-app',
    slice_id: null,
    kind: overrides.kind,
    verdict: null,
    rubric_delta_pp: null,
    rationale: overrides.rationale ?? '',
    references: overrides.references ?? {},
  };
}

describe('CaptainL2 PR status banner', () => {
  it('shows pr_open banner with link when pull_request_opened is most recent', async () => {
    const app = bundle({
      app_id: 'sample-app',
      captain_log_tail: [
        logEntry({
          kind: 'pull_request_opened',
          rationale: 'PR opened: https://github.com/owner/repo/pull/42',
          references: { pr_url: 'https://github.com/owner/repo/pull/42' },
        }),
      ],
    });

    const { default: L2Client } = await import('../app/captain/[appId]/L2Client');
    const { container } = render(React.createElement(L2Client, { initial: app }));
    const banner = container.querySelector('.pr-status-banner');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain('PR open');
    expect(banner!.textContent).toContain('ready for review');
    const link = banner!.querySelector('a');
    expect(link?.getAttribute('href')).toContain('pull/42');
  });

  it('shows pr_merged banner when most recent event is pull_request_merged', async () => {
    const app = bundle({
      app_id: 'sample-app',
      captain_log_tail: [
        logEntry({ kind: 'pull_request_merged', references: { pr_url: 'https://x' } }),
        logEntry({ kind: 'pull_request_opened', references: { pr_url: 'https://x' } }),
      ],
    });
    const { default: L2Client } = await import('../app/captain/[appId]/L2Client');
    const { container } = render(React.createElement(L2Client, { initial: app }));
    const banner = container.querySelector('.pr-status-banner');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain('PR merged');
  });

  it('shows post_merge banner when post_merge_cycle is most recent', async () => {
    const app = bundle({
      app_id: 'sample-app',
      captain_log_tail: [
        logEntry({ kind: 'post_merge_cycle' }),
        logEntry({ kind: 'pull_request_merged' }),
      ],
    });
    const { default: L2Client } = await import('../app/captain/[appId]/L2Client');
    const { container } = render(React.createElement(L2Client, { initial: app }));
    const banner = container.querySelector('.pr-status-banner');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain('Post-merge');
  });

  it('shows roadmap_complete banner when only roadmap_complete logged', async () => {
    const app = bundle({
      app_id: 'sample-app',
      captain_log_tail: [logEntry({ kind: 'roadmap_complete' })],
    });
    const { default: L2Client } = await import('../app/captain/[appId]/L2Client');
    const { container } = render(React.createElement(L2Client, { initial: app }));
    const banner = container.querySelector('.pr-status-banner');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain('Roadmap complete');
  });

  it('renders no banner when no PR-lifecycle events present', async () => {
    const app = bundle({
      app_id: 'sample-app',
      captain_log_tail: [logEntry({ kind: 'validate' })],
    });
    const { default: L2Client } = await import('../app/captain/[appId]/L2Client');
    const { container } = render(React.createElement(L2Client, { initial: app }));
    expect(container.querySelector('.pr-status-banner')).toBeNull();
  });
});
