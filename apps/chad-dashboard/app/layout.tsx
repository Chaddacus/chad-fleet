import type { Metadata } from 'next';
import Link from 'next/link';
import { headers } from 'next/headers';
import './globals.css';
import { LogoutButton } from '@/features/auth/login-ui';

export const metadata: Metadata = {
  title: 'Chad Dashboard',
  description: 'Fleet state dashboard for chad-fleet',
};

const navLinks = [
  { href: '/', label: 'Chat' },
  { href: '/sessions', label: 'Sessions' },
  { href: '/email', label: 'Email' },
  { href: '/tools', label: 'Tools' },
  { href: '/inbox', label: 'Inbox' },
  { href: '/apps', label: 'Apps' },
  { href: '/views', label: 'Views' },
  { href: '/captain', label: 'Captain' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Route-aware via the x-pathname header set by middleware; hide chrome on /login.
  const pathname = headers().get('x-pathname') ?? '';
  const isLogin = pathname === '/login';

  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-gray-950 text-gray-100 font-mono">
        {!isLogin && (
          <nav className="border-b border-gray-800 bg-gray-900 px-6 py-3">
            <div className="flex items-center gap-8 max-w-5xl mx-auto">
              <span className="text-sm font-semibold tracking-widest text-gray-400 uppercase">
                chad-fleet
              </span>
              <div className="flex gap-6 flex-1">
                {navLinks.map(({ href, label }) => (
                  <Link
                    key={href}
                    href={href}
                    className="text-sm text-gray-300 hover:text-white transition-colors"
                  >
                    {label}
                  </Link>
                ))}
              </div>
              <LogoutButton />
            </div>
          </nav>
        )}
        <main className="max-w-5xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
