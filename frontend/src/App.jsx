import React, { useEffect, useState } from 'react';
import { api } from './utils/api';
import Home from './components/Home';
import ProjectPage from './components/Project';
import DocumentEditor from './components/DocumentEditor';

function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, '');
  const [path, query] = h.split('?');
  const params = {};
  if (query) new URLSearchParams(query).forEach((v, k) => (params[k] = v));
  const seg = path.split('/').filter(Boolean);
  if (seg[0] === 'projects' && seg[1]) return { name: 'project', params: { ...params, id: seg[1] } };
  if (seg[0] === 'documents' && seg[1]) return { name: 'document', params: { ...params, id: seg[1] } };
  return { name: 'home', params };
}

export default function App() {
  const [route, setRoute] = useState(parseHash());
  const [me, setMe] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    // Identity comes from the portal — if this fails, we're not behind it.
    api.get('api/me').then(setMe).catch(e => setErr(e.message));
  }, []);

  if (err) {
    return (
      <div className="login-hero">
        <div className="card login-card">
          <div className="logo-lg">D</div>
          <h1 style={{ fontSize: 20, margin: '0 0 8px' }}>DioXengine</h1>
          <p className="soft" style={{ fontSize: 13.5 }}>
            Couldn't verify your Dioxycle identity ({err}).<br />
            Open this app through <b>apps.dioxycle.com</b>.
          </p>
        </div>
      </div>
    );
  }
  if (!me) return null;

  return (
    <>
      <nav className="topnav">
        <a className="brand" href="#/">
          <span className="logo">D</span>
          <span>Dio<span className="x">X</span>engine</span>
        </a>
        <div style={{ flex: 1 }} />
        <span className="soft small">{me.name || me.email}</span>
      </nav>
      {route.name === 'project' && <ProjectPage id={Number(route.params.id)} me={me} />}
      {route.name === 'document' && <DocumentEditor id={Number(route.params.id)} me={me} />}
      {route.name === 'home' && <Home me={me} />}
    </>
  );
}

export function timeAgo(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function initials(email) {
  const base = email.split('@')[0];
  const parts = base.split(/[._-]/).filter(Boolean);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || parts[0]?.[1] || '')).toUpperCase();
}
