'use client';

// L1 client controls: "+ Add app" modal + orphan workspace cleanup.
// The fleet list itself is server-rendered in page.tsx; these controls
// mutate via the captain proxy (/api/captain/apps/register, DELETE
// /api/captain/apps/{id}) and then call router.refresh() to repaint.

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

interface OrphanEntry {
  app_id: string;
  is_registered: boolean;
}

interface AppsListResponse {
  count: number;
  apps: Array<{ app_id: string; is_registered: boolean }>;
}

export default function BridgeControls() {
  const router = useRouter();
  const [showAdd, setShowAdd] = useState(false);
  const [showOrphans, setShowOrphans] = useState(false);
  const [orphans, setOrphans] = useState<OrphanEntry[]>([]);
  const [loadingOrphans, setLoadingOrphans] = useState(false);

  async function loadOrphans() {
    setLoadingOrphans(true);
    try {
      const r = await fetch('/api/captain/apps?include_orphans=true', { cache: 'no-store' });
      if (!r.ok) return;
      const body = (await r.json()) as AppsListResponse;
      setOrphans(body.apps.filter(a => !a.is_registered));
    } finally {
      setLoadingOrphans(false);
    }
  }

  useEffect(() => {
    if (showOrphans) loadOrphans();
  }, [showOrphans]);

  return (
    <div className="bridge-controls">
      <button className="btn-primary" onClick={() => setShowAdd(true)}>+ Add app</button>
      <button className="btn-secondary" onClick={() => setShowOrphans(v => !v)}>
        {showOrphans ? 'Hide cleanup' : 'Cleanup orphans'}
      </button>

      {showOrphans && (
        <div className="orphan-panel">
          <div className="orphan-h">
            Filesystem-only workspaces (not in registry)
            <button className="btn-link" onClick={loadOrphans} disabled={loadingOrphans}>
              {loadingOrphans ? 'loading…' : 'refresh'}
            </button>
          </div>
          {orphans.length === 0 ? (
            <div className="muted">No orphans. Bridge is clean.</div>
          ) : (
            <ul className="orphan-list">
              {orphans.map(o => (
                <OrphanRow
                  key={o.app_id}
                  app_id={o.app_id}
                  onDeleted={() => {
                    loadOrphans();
                    router.refresh();
                  }}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      {showAdd && (
        <AddAppModal
          onClose={() => setShowAdd(false)}
          onCreated={() => {
            setShowAdd(false);
            router.refresh();
          }}
        />
      )}
    </div>
  );
}

function OrphanRow({ app_id, onDeleted }: { app_id: string; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function remove(deleteWorkspace: boolean) {
    if (deleteWorkspace && !confirm(`Delete workspace dir for ${app_id}? This wipes roadmap, logs, and notes on disk.`)) {
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const url = `/api/captain/apps/${encodeURIComponent(app_id)}${deleteWorkspace ? '?delete_workspace=true' : ''}`;
      const r = await fetch(url, { method: 'DELETE' });
      if (!r.ok) {
        const body = await r.text();
        setErr(`delete failed: ${r.status} ${body.slice(0, 120)}`);
        return;
      }
      onDeleted();
    } catch (e) {
      setErr(`network error: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="orphan-row">
      <span className="orphan-id">{app_id}</span>
      <span className="orphan-actions">
        <button className="btn-link danger" onClick={() => remove(true)} disabled={busy}>
          Delete workspace
        </button>
      </span>
      {err && <span className="orphan-err">{err}</span>}
    </li>
  );
}

interface AddState {
  app_id: string;
  name: string;
  repo_path: string;
  mode: 'autonomous' | 'observe_only';
  schedule_hour: number;
  notes: string;
  verify_cmd: string;
}

function AddAppModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [state, setState] = useState<AddState>({
    app_id: '',
    name: '',
    repo_path: '',
    mode: 'observe_only',
    schedule_hour: 9,
    notes: '',
    verify_cmd: '',
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof AddState>(k: K, v: AddState[K]) {
    setState(prev => ({ ...prev, [k]: v }));
  }

  async function submit() {
    setErr(null);
    if (!state.app_id.trim()) return setErr('app_id is required');
    if (!state.repo_path.trim()) return setErr('repo_path is required');
    setBusy(true);
    try {
      const payload = {
        app_id: state.app_id.trim(),
        name: state.name.trim() || state.app_id.trim(),
        repo_path: state.repo_path.trim(),
        mode: state.mode,
        schedule_hour: state.schedule_hour,
        notes: state.notes.trim(),
        verify_cmd: state.verify_cmd.trim() || null,
      };
      const r = await fetch('/api/captain/apps/register', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const body = await r.text();
        setErr(`register failed: ${r.status} ${body.slice(0, 200)}`);
        return;
      }
      onCreated();
    } catch (e) {
      setErr(`network error: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-h">Register new app</div>
        <div className="modal-body">
          <label className="field">
            <span>app_id <em>(slug)</em></span>
            <input
              autoFocus
              value={state.app_id}
              onChange={e => set('app_id', e.target.value)}
              placeholder="author-toolkit"
            />
          </label>
          <label className="field">
            <span>name</span>
            <input
              value={state.name}
              onChange={e => set('name', e.target.value)}
              placeholder="Author Toolkit"
            />
          </label>
          <label className="field">
            <span>repo_path</span>
            <input
              value={state.repo_path}
              onChange={e => set('repo_path', e.target.value)}
              placeholder="/Users/chadsimon/code/personal/author_toolkit"
            />
          </label>
          <div className="field-row">
            <label className="field">
              <span>mode</span>
              <select
                value={state.mode}
                onChange={e => set('mode', e.target.value as AddState['mode'])}
              >
                <option value="observe_only">observe_only</option>
                <option value="autonomous">autonomous</option>
              </select>
            </label>
            <label className="field">
              <span>schedule_hour</span>
              <input
                type="number"
                min={0}
                max={23}
                value={state.schedule_hour}
                onChange={e => set('schedule_hour', Number(e.target.value) || 0)}
              />
            </label>
          </div>
          <label className="field">
            <span>verify_cmd <em>(optional)</em></span>
            <input
              value={state.verify_cmd}
              onChange={e => set('verify_cmd', e.target.value)}
              placeholder="uv run pytest -q"
            />
          </label>
          <label className="field">
            <span>notes</span>
            <textarea
              value={state.notes}
              onChange={e => set('notes', e.target.value)}
              rows={2}
              placeholder="What is this app and what should the captain do?"
            />
          </label>
          {err && <div className="modal-err">{err}</div>}
        </div>
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? 'Registering…' : 'Register'}
          </button>
        </div>
      </div>
    </div>
  );
}
