/* Stateless signed-session token — Edge-runtime safe (Web Crypto only, no node:crypto).
 *
 * Token format:  base64url(payload) "." base64url(HMAC-SHA256(payload, secret))
 * payload = { exp: <unix-seconds> }
 *
 * Used by both the middleware (Edge) to gate routes and the login route (Node) to mint a
 * cookie. Web Crypto is available in both runtimes, so this module works everywhere.
 *
 * Stateless => "logout" clears the cookie; tokens cannot be revoked server-side without a
 * store. Short TTL + clear-on-logout is the documented single-operator posture (see README).
 */

const enc = new TextEncoder();

function b64urlEncode(bytes: Uint8Array): string {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlDecode(s: string): Uint8Array {
  const pad = s.length % 4 === 0 ? '' : '='.repeat(4 - (s.length % 4));
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/') + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmac(payload: string, secret: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(payload));
  return new Uint8Array(sig);
}

/** Constant-time string compare to avoid signature-timing leaks. */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function signSession(secret: string, ttlSeconds: number): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
  const payload = b64urlEncode(enc.encode(JSON.stringify({ exp })));
  const sig = b64urlEncode(await hmac(payload, secret));
  return `${payload}.${sig}`;
}

export async function verifySession(token: string, secret: string): Promise<boolean> {
  const parts = token.split('.');
  if (parts.length !== 2) return false;
  const [payload, sig] = parts;
  const expected = b64urlEncode(await hmac(payload, secret));
  if (!timingSafeEqual(sig, expected)) return false;
  try {
    const { exp } = JSON.parse(new TextDecoder().decode(b64urlDecode(payload)));
    return typeof exp === 'number' && exp > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}
