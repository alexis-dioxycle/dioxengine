import React, { useEffect, useState } from 'react';
import { api, getToken, setToken, logout } from './utils/api';
import type { Me } from './types';
import Login from './components/Login';
import Home from './components/Home';
import ProjectPage from './components/Project';
import DocumentEditor from './components/DocumentEditor';

export interface Route { name: string; params: Record<string, string> }

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, '');
  const [path, query] = h.split('?');
  const params: Record<string, string> = {};
  if (query) new URLSearchParams(query).forEach((v, k) => (params[k] = v));
  const seg = path.split('/').filter(Boolean);
  if (seg[0] === 'login') return { name: 'login', params };
  if (seg[0] === 'auth' && seg[1] === 'callback') return { name: 'auth-callback', params };
  if (seg[0] === 'projects' && seg[1]) return { name: 'project', params: { ...params, id: seg[1] } };
  if (seg[0] === 'documents' && seg[1]) return { name: 'document', params: { ...params, id: seg[1] } };
  return { name: 'home', params };
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash());
  const [me, setMe] = useState<Me | null>(null);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    // OAuth landing: #/auth/callback?token=…&next=…
    if (route.name === 'auth-callback') {
      const t = route.params.token;
      if (t) setToken(t);
      window.location.hash = route.params.next && route.params.next !== '/' ? `#${route.params.next}` : '#/';
      return;
    }
    if (!getToken()) { setBooting(false); return; }
    api.get<Me>('/me')
      .then(setMe)
      .catch(() => setToken(null))
      .finally(() => setBooting(false));
  }, [route.name === 'auth-callback' ? route.params.token : '']);

  if (booting) return null;
  if (!me) return <Login onLogin={setMe} />;

  return (
    <>
      <nav className="topnav">
        <a className="brand" href="#/">
          <span className="logo">D</span>
          <span>Dio<span className="x">X</span>engine</span>
        </a>
        <div style={{ flex: 1 }} />
        <span className="soft small">{me.name || me.email}</span>
        <button className="btn ghost sm" onClick={logout}>Sign out</button>
      </nav>
      {route.name === 'project' && <ProjectPage id={Number(route.params.id)} me={me} />}
      {route.name === 'document' && <DocumentEditor id={Number(route.params.id)} me={me} />}
      {(route.name === 'home' || route.name === 'login') && <Home me={me} />}
    </>
  );
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function initials(email: string): string {
  const base = email.split('@')[0];
  const parts = base.split(/[._-]/).filter(Boolean);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || parts[0]?.[1] || '')).toUpperCase();
}
