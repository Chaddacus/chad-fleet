import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { GET } from '../app/api/inbox/route';

// Helper to write a temp inbox file
let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'inbox-test-'));
});

afterEach(async () => {
  delete process.env.CHAD_NOTIFIER_INBOX_PATH;
  await fs.rm(tmpDir, { recursive: true, force: true });
});

function setInboxPath(filePath: string) {
  process.env.CHAD_NOTIFIER_INBOX_PATH = filePath;
}

describe('GET /api/inbox', () => {
  it('returns parsed items in reverse-chrono order (last 50)', async () => {
    const inboxPath = path.join(tmpDir, 'inbox.jsonl');
    const items = [
      { ts: '2025-01-01T00:00:00Z', channel: 'app/a', severity: 'info', title: 'First', body: '' },
      { ts: '2025-01-02T00:00:00Z', channel: 'app/a', severity: 'warn', title: 'Second', body: '' },
      { ts: '2025-01-03T00:00:00Z', channel: 'app/a', severity: 'critical', title: 'Third', body: '' },
    ];
    await fs.writeFile(inboxPath, items.map((i) => JSON.stringify(i)).join('\n'));
    setInboxPath(inboxPath);

    const response = await GET();
    const body = (await response.json()) as { items: Array<{ title: string }> };

    expect(body.items).toHaveLength(3);
    // reverse-chrono: most recent first
    expect(body.items[0].title).toBe('Third');
    expect(body.items[1].title).toBe('Second');
    expect(body.items[2].title).toBe('First');
  });

  it('returns empty items array when file does not exist', async () => {
    setInboxPath(path.join(tmpDir, 'nonexistent.jsonl'));

    const response = await GET();
    const body = (await response.json()) as { items: unknown[] };

    expect(body.items).toEqual([]);
  });

  it('skips malformed JSON lines and returns valid items', async () => {
    const inboxPath = path.join(tmpDir, 'inbox.jsonl');
    const lines = [
      JSON.stringify({ ts: '2025-01-01T00:00:00Z', channel: 'app/a', severity: 'info', title: 'Good', body: '' }),
      'NOT VALID JSON }{',
      JSON.stringify({ ts: '2025-01-02T00:00:00Z', channel: 'app/b', severity: 'warn', title: 'Also good', body: '' }),
    ];
    await fs.writeFile(inboxPath, lines.join('\n'));
    setInboxPath(inboxPath);

    const response = await GET();
    const body = (await response.json()) as { items: Array<{ title: string }> };

    // malformed line is filtered out
    expect(body.items).toHaveLength(2);
    const titles = body.items.map((i) => i.title);
    expect(titles).toContain('Good');
    expect(titles).toContain('Also good');
  });

  it('returns only the last 50 lines (reversed)', async () => {
    const inboxPath = path.join(tmpDir, 'inbox.jsonl');
    const lines = Array.from({ length: 60 }, (_, i) =>
      JSON.stringify({ ts: `2025-01-${String(i + 1).padStart(2, '0')}T00:00:00Z`, channel: 'app/x', severity: 'info', title: `Item ${i + 1}`, body: '' }),
    );
    await fs.writeFile(inboxPath, lines.join('\n'));
    setInboxPath(inboxPath);

    const response = await GET();
    const body = (await response.json()) as { items: Array<{ title: string }> };

    // last 50 lines are items 11-60, reversed so item 60 is first
    expect(body.items).toHaveLength(50);
    expect(body.items[0].title).toBe('Item 60');
    expect(body.items[49].title).toBe('Item 11');
  });
});
