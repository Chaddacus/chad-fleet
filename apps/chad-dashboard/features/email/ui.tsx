import type { EmailMessage } from '@/lib/types';
import { getEmail } from './client';

/** Email tab — read-fast inbox list. Actions (reply/archive) route through the admiral, which
 * dispatches a captain holding the email-mcp tools (read via projection, act via agent). */
export async function EmailFeature() {
  const { email, error } = await getEmail();
  const unread = email.filter((m) => m.unread).length;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Email</h1>
        <p className="mt-1 text-sm text-gray-400">
          Recent inbox{unread > 0 ? ` — ${unread} unread` : ''}. Ask the admiral to reply,
          archive, or triage; it dispatches a captain that holds the email tools.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400">
          Couldn’t reach the aggregator: {error}
        </div>
      )}

      {email.length === 0 && !error ? (
        <p className="text-sm text-gray-500">
          No mail. Configure <code className="text-gray-400">EMAIL_IMAP_*</code> to connect an account.
        </p>
      ) : (
        <ul className="flex flex-col gap-1" data-testid="email-list">
          {email.map((m: EmailMessage) => (
            <li
              key={m.id}
              className={`rounded border px-4 py-3 flex items-center gap-3 ${
                m.unread ? 'border-gray-700 bg-gray-900' : 'border-gray-800 bg-gray-950'
              }`}
            >
              {m.unread && <span className="h-2 w-2 rounded-full bg-blue-400 shrink-0" />}
              <div className="flex flex-col min-w-0 flex-1">
                <span className={`text-sm truncate ${m.unread ? 'text-gray-100 font-medium' : 'text-gray-300'}`}>
                  {m.subject || '(no subject)'}
                </span>
                <span className="text-xs text-gray-500 truncate">{m.from_}</span>
              </div>
              <span className="text-xs text-gray-600 shrink-0">{m.date}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
