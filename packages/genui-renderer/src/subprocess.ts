/**
 * Subprocess wrapper around `claude -p` (Pro/Max OAuth, no API key).
 * Mirrors voice-drafter/src/voice_drafter/llm.py shape.
 *
 * Kept as a separate module so the high-level generateView() pipeline can
 * mock it cleanly in tests via `vi.mock('./subprocess.js')`.
 */

import { execFileSync, spawn } from 'node:child_process';

interface SpawnResult {
  stdout: string;
  stderr: string;
  code: number | null;
  signal: NodeJS.Signals | null;
  timedOut: boolean;
}

function runWithStdin(
  bin: string,
  args: string[],
  input: string,
  timeoutMs: number,
  maxBytes: number,
): Promise<SpawnResult> {
  return new Promise((resolveP, rejectP) => {
    const child = spawn(bin, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let killed = false;

    const timer = setTimeout(() => {
      timedOut = true;
      killed = true;
      child.kill('SIGTERM');
    }, timeoutMs);

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');

    child.stdout.on('data', (chunk: string) => {
      stdout += chunk;
      if (stdout.length > maxBytes) {
        killed = true;
        child.kill('SIGTERM');
      }
    });
    child.stderr.on('data', (chunk: string) => {
      stderr += chunk;
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      rejectP(err);
    });

    child.on('close', (code, signal) => {
      clearTimeout(timer);
      void killed;
      resolveP({ stdout, stderr, code, signal, timedOut });
    });

    child.stdin.write(input);
    child.stdin.end();
  });
}

function resolveBin(): string {
  const envOverride = process.env['GENUI_CLAUDE_BIN'];
  if (envOverride != null && envOverride.trim() !== '') {
    return envOverride.trim();
  }
  try {
    const result = execFileSync('which', ['claude'], { encoding: 'utf8' }).trim();
    if (result !== '') return result;
  } catch {
    // which failed — fall through
  }
  return `${process.env['HOME'] ?? '~'}/.local/bin/claude`;
}

export const CLAUDE_BIN: string = resolveBin();

export class LLMError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LLMError';
  }
}

export interface ClaudeCompleteOptions {
  /** Model slug passed to --model. Defaults to "sonnet". */
  model?: string;
  /** Appended to Claude's system prompt via --append-system-prompt. */
  system?: string;
  /** Subprocess timeout in seconds. Default 90. */
  timeout?: number;
}

/**
 * Invoke `claude -p` non-interactively and return the assistant's text.
 *
 * Passes `prompt` over stdin. Parses `--output-format json` response.
 * Throws `LLMError` on timeout, non-zero exit, parse failure, or empty output.
 */
export async function claudeComplete(
  prompt: string,
  options: ClaudeCompleteOptions = {},
): Promise<string> {
  const model = options.model ?? 'sonnet';
  const timeoutSec = options.timeout ?? 90;
  const timeoutMs = timeoutSec * 1000;

  const cmd = buildCommand(model, options.system);
  const [bin, ...args] = cmd;

  let result: SpawnResult;
  try {
    result = await runWithStdin(bin!, args, prompt, timeoutMs, 10 * 1024 * 1024);
  } catch (err) {
    throw new LLMError(`claude spawn failed: ${String(err)}`);
  }

  if (result.timedOut) {
    throw new LLMError(`claude timeout after ${timeoutSec}s`);
  }
  if (result.code !== 0) {
    const msg = result.stderr.slice(0, 500).trim();
    throw new LLMError(`claude exit ${String(result.code)}: ${msg}`);
  }
  const { stdout, stderr } = result;

  let payload: unknown;
  try {
    payload = JSON.parse(stdout);
  } catch {
    throw new LLMError(
      `claude returned non-JSON: ${stdout.slice(0, 300)}${stderr ? ` (stderr: ${stderr.slice(0, 200)})` : ''}`,
    );
  }

  if (isErrorPayload(payload)) {
    const result = (payload as Record<string, unknown>)['result'];
    throw new LLMError(`claude is_error=true: ${JSON.stringify(result ?? payload)}`);
  }

  const text = extractText(payload);
  if (text == null || text.trim() === '') {
    throw new LLMError('claude returned empty result');
  }

  return text.trim();
}

function buildCommand(model: string, system?: string): string[] {
  const cmd: string[] = [CLAUDE_BIN, '-p', '--model', model, '--output-format', 'json'];
  if (system != null && system.trim() !== '') {
    cmd.push('--append-system-prompt', system);
  }
  return cmd;
}

function extractText(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object') return null;
  const p = payload as Record<string, unknown>;

  if (typeof p['result'] === 'string') return p['result'];

  if (Array.isArray(p['content'])) {
    for (const block of p['content'] as unknown[]) {
      if (
        block !== null &&
        typeof block === 'object' &&
        (block as Record<string, unknown>)['type'] === 'text' &&
        typeof (block as Record<string, unknown>)['text'] === 'string'
      ) {
        return (block as Record<string, unknown>)['text'] as string;
      }
    }
  }

  return null;
}

function isErrorPayload(payload: unknown): boolean {
  return (
    payload !== null &&
    typeof payload === 'object' &&
    'is_error' in payload &&
    (payload as Record<string, unknown>)['is_error'] === true
  );
}

