/* Password hashing/verification — Node runtime only (uses node:crypto scrypt).
 *
 * Imported solely by the login route handler (Node runtime), never by the middleware (Edge).
 * Stored form: `scrypt$<saltHex>$<derivedHex>`. scrypt resists brute force; comparison is
 * timing-safe. Generate a hash with `node features/auth/hash-password.mjs <password>`.
 */

import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

const KEYLEN = 64;

export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const derived = scryptSync(password, salt, KEYLEN);
  return `scrypt$${salt.toString('hex')}$${derived.toString('hex')}`;
}

export function verifyPassword(password: string, stored: string): boolean {
  const parts = stored.split('$');
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false;
  const [, saltHex, hashHex] = parts;
  let salt: Buffer;
  let expected: Buffer;
  try {
    salt = Buffer.from(saltHex, 'hex');
    expected = Buffer.from(hashHex, 'hex');
  } catch {
    return false;
  }
  if (expected.length !== KEYLEN) return false;
  const derived = scryptSync(password, salt, KEYLEN);
  return timingSafeEqual(derived, expected);
}
