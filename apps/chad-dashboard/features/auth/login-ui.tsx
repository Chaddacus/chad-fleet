'use client';

import { useState } from 'react';

/** Logout control for the shell nav. Clears the session cookie, returns to /login. */
export function LogoutButton() {
  async function onClick() {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.assign('/login');
  }
  return (
    <button
      onClick={onClick}
      className="text-xs text-gray-500 hover:text-gray-300 transition-colors shrink-0"
    >
      Sign out
    </button>
  );
}

/** Single-operator login form. Posts to /api/auth/login, then navigates to `next`. */
export function LoginForm({ next }: { next: string }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        window.location.assign(next || '/');
        return;
      }
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      setError(data.error ?? `login failed (${res.status})`);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-lg border border-gray-800 bg-gray-900 p-6 flex flex-col gap-4"
      >
        <div>
          <h1 className="font-mono text-xl font-bold text-gray-100">chad-fleet</h1>
          <p className="mt-1 text-sm text-gray-400">Sign in to the hub.</p>
        </div>
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-gray-500 focus:outline-none"
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={busy || !password}
          className="rounded bg-gray-100 px-3 py-2 text-sm font-medium text-gray-900 hover:bg-white disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
