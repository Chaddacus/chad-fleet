import { NextResponse } from 'next/server';
import { SESSION_COOKIE } from '@/lib/config';

export async function POST() {
  const res = NextResponse.json({ ok: true });
  // Stateless tokens can't be revoked server-side; clearing the cookie is the invalidation.
  res.cookies.set(SESSION_COOKIE, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  });
  return res;
}
