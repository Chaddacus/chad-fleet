import { NextResponse } from 'next/server';
import { SESSION_COOKIE, authEnabled, config, signingSecret } from '@/lib/config';
import { verifyPassword } from '@/features/auth/password';
import { signSession } from '@/features/auth/session';

// Node runtime: password verification uses node:crypto (scrypt).
export const runtime = 'nodejs';

export async function POST(req: Request) {
  if (!authEnabled()) {
    return NextResponse.json(
      { error: 'auth not configured — set HUB_AUTH_PASSWORD_HASH to enable login' },
      { status: 400 },
    );
  }

  let password = '';
  try {
    const body = (await req.json()) as { password?: unknown };
    password = typeof body.password === 'string' ? body.password : '';
  } catch {
    return NextResponse.json({ error: 'bad request' }, { status: 400 });
  }

  if (!password || !verifyPassword(password, config.authPasswordHash)) {
    return NextResponse.json({ error: 'invalid credentials' }, { status: 401 });
  }

  const token = await signSession(signingSecret(), config.authTtlSeconds);
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: config.authTtlSeconds,
  });
  return res;
}
