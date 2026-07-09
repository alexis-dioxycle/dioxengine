import React, { useEffect, useState } from 'react';
import { api, setToken } from '../utils/api';
import type { Me } from '../types';

export default function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [health, setHealth] = useState<{ azure_configured: boolean; allow_dev_login: boolean } | null>(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => { api.get('/health').then(setHealth).catch(() => setHealth(null)); }, []);

  async function devLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr('');
    try {
      const r = await api.post('/auth/dev-login', { email });
      setToken(r.access_token);
      const me = await api.get<Me>('/me');
      onLogin(me);
      window.location.hash = '#/';
    } catch (e: any) {
      setErr(e.message || 'Login failed');
    } finally { setBusy(false); }
  }

  return (
    <div className="login-hero">
      <div className="card login-card">
        <div className="logo-lg">D</div>
        <h1 style={{ fontSize: 22, margin: '0 0 4px' }}>Dio<span style={{ color: 'var(--accent-mid)' }}>X</span>engine</h1>
        <p className="soft" style={{ margin: '0 0 26px', fontSize: 13.5 }}>
          Engineering document workflows — authored by you,<br />assisted by Claude.
        </p>

        {health?.azure_configured && (
          <a className="btn" style={{ width: '100%', justifyContent: 'center', padding: '11px' }}
             href="/auth/microsoft/login">
            <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true">
              <rect x="1" y="1" width="9" height="9" fill="#f25022" />
              <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
              <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
              <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
            </svg>
            Sign in with Microsoft
          </a>
        )}

        {health?.allow_dev_login && (
          <form onSubmit={devLogin} style={{ marginTop: health?.azure_configured ? 18 : 0, textAlign: 'left' }}>
            <label className="lbl">Dev login (local only)</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="input" type="email" required placeholder="you@dioxycle.com"
                     value={email} onChange={e => setEmail(e.target.value)} />
              <button className="btn primary" disabled={busy || !email}>
                {busy ? <span className="spin" /> : 'Enter'}
              </button>
            </div>
          </form>
        )}

        {err && <p style={{ color: 'var(--bad)', fontSize: 13, marginTop: 12 }}>{err}</p>}
        {health && !health.azure_configured && !health.allow_dev_login && (
          <p className="muted small">No sign-in method configured. Set AZURE_* or ALLOW_DEV_LOGIN=1.</p>
        )}
      </div>
    </div>
  );
}
