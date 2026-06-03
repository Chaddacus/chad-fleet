import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { isAuthed, isPublicPath } from '@/features/auth/gate';

// The shell's only gate: delegate the decision to features/auth, redirect/401 on failure.
export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  // Forward the path so server components (e.g. the layout) can be route-aware.
  const headers = new Headers(req.headers);
  headers.set('x-pathname', pathname);
  const pass = () => NextResponse.next({ request: { headers } });

  if (isPublicPath(pathname)) return pass();
  if (await isAuthed(req)) return pass();

  // Unauthenticated: API routes get 401, page routes redirect to /login.
  if (pathname.startsWith('/api/')) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const url = req.nextUrl.clone();
  url.pathname = '/login';
  url.searchParams.set('next', pathname);
  return NextResponse.redirect(url);
}

export const config = {
  // Gate everything except Next internals and static files.
  matcher: ['/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)'],
};
