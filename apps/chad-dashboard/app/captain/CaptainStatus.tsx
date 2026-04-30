'use client';

// Polling indicator + auto-refresh trigger for any captain view.
// Renders the "captain api" health dot + "updated 3s ago" stamp.
// Calls router.refresh() on the configured cadence to refetch the
// surrounding server component.

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

interface Props {
  cadenceMs: number;
  apiOk: boolean;
}

export default function CaptainStatus({ cadenceMs, apiOk }: Props) {
  const router = useRouter();
  const lastRefresh = useRef(Date.now());
  const [stamp, setStamp] = useState('just now');

  useEffect(() => {
    const tick = setInterval(() => {
      const s = Math.round((Date.now() - lastRefresh.current) / 1000);
      setStamp(s < 5 ? 'just now' : `${s}s ago`);
    }, 1000);
    return () => clearInterval(tick);
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      router.refresh();
      lastRefresh.current = Date.now();
      setStamp('just now');
    }, cadenceMs);
    return () => clearInterval(id);
  }, [router, cadenceMs]);

  return (
    <div className="cap-bar-row" style={{ marginLeft: 'auto', justifyContent: 'flex-end', border: 0, padding: 0, height: 'auto' }}>
      <span className={`cap-status-dot${apiOk ? '' : ' err'}`}>
        {apiOk ? 'captain api' : 'unreachable'}
      </span>
      <span className="cap-tick-stamp">{stamp}</span>
    </div>
  );
}
