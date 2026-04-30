import { NextResponse } from 'next/server';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

export async function GET() {
  const p =
    process.env.CHAD_NOTIFIER_INBOX_PATH ??
    path.join(os.homedir(), '.chad', 'notifier', 'inbox.jsonl');
  try {
    const raw = await fs.readFile(p, 'utf-8');
    const lines = raw.trim().split('\n').filter(Boolean);
    const items = lines
      .slice(-50)
      .reverse()
      .map((line) => {
        try {
          return JSON.parse(line) as unknown;
        } catch {
          return null;
        }
      })
      .filter(Boolean);
    return NextResponse.json({ items });
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') {
      return NextResponse.json({ items: [] });
    }
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
