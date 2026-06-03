// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { signSession, verifySession } from '@/features/auth/session';
import { hashPassword, verifyPassword } from '@/features/auth/password';

const SECRET = 'test-secret-123';

describe('session token', () => {
  it('round-trips a valid signed session', async () => {
    const token = await signSession(SECRET, 3600);
    expect(await verifySession(token, SECRET)).toBe(true);
  });

  it('rejects a token signed with a different secret', async () => {
    const token = await signSession(SECRET, 3600);
    expect(await verifySession(token, 'other-secret')).toBe(false);
  });

  it('rejects a tampered signature', async () => {
    const token = await signSession(SECRET, 3600);
    const [payload] = token.split('.');
    expect(await verifySession(`${payload}.deadbeef`, SECRET)).toBe(false);
  });

  it('rejects an expired token', async () => {
    const token = await signSession(SECRET, -1); // already expired
    expect(await verifySession(token, SECRET)).toBe(false);
  });

  it('rejects a malformed token', async () => {
    expect(await verifySession('not-a-token', SECRET)).toBe(false);
    expect(await verifySession('', SECRET)).toBe(false);
  });
});

describe('password hashing', () => {
  it('verifies a correct password against its scrypt hash', () => {
    const stored = hashPassword('hunter2');
    expect(stored.startsWith('scrypt$')).toBe(true);
    expect(verifyPassword('hunter2', stored)).toBe(true);
  });

  it('rejects a wrong password', () => {
    const stored = hashPassword('hunter2');
    expect(verifyPassword('wrong', stored)).toBe(false);
  });

  it('rejects malformed stored hashes', () => {
    expect(verifyPassword('x', 'garbage')).toBe(false);
    expect(verifyPassword('x', 'scrypt$only-two')).toBe(false);
    expect(verifyPassword('x', '')).toBe(false);
  });
});
