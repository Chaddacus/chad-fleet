import express, { type Request, type Response } from 'express';
import { generateView } from './llm.js';

export function createApp(): express.Application {
  const app = express();
  app.use(express.json());

  // Permissive CORS for the dashboard (and any other localhost client).
  // Browser POSTs from :3000 → :8107 require this; without it the preflight
  // succeeds but the actual fetch is blocked → "Failed to fetch".
  app.use((req: Request, res: Response, next) => {
    const origin = req.headers.origin;
    if (typeof origin === 'string') {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
    } else {
      res.setHeader('Access-Control-Allow-Origin', '*');
    }
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    res.setHeader('Access-Control-Max-Age', '600');
    if (req.method === 'OPTIONS') {
      res.status(204).end();
      return;
    }
    next();
  });

  app.get('/health', (_req: Request, res: Response) => {
    res.json({ ok: true });
  });

  app.post('/render', async (req: Request, res: Response) => {
    const { state, request } = req.body as { state?: unknown; request?: unknown };

    if (state === null || state === undefined || typeof state !== 'object') {
      res.status(400).json({ error: '`state` must be a JSON object' });
      return;
    }
    if (typeof request !== 'string' || request.trim() === '') {
      res.status(400).json({ error: '`request` must be a non-empty string' });
      return;
    }

    // Set SSE headers
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders();

    const send = (data: unknown): void => {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    try {
      await generateView(state as object, request, (event) => {
        send(event);
      });
    } catch (err) {
      send({ type: 'error', message: String(err) });
    } finally {
      res.end();
    }
  });

  return app;
}

// Run directly when called as main
const isMain =
  typeof process !== 'undefined' &&
  process.argv[1] != null &&
  process.argv[1].endsWith('server.ts');

if (isMain) {
  const PORT = Number(process.env['PORT'] ?? 8107);
  const app = createApp();
  app.listen(PORT, () => {
    process.stdout.write(`genui-renderer listening on :${PORT}\n`);
  });
}
