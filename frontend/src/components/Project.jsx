import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../utils/api';
import { timeAgo } from '../App';

export default function ProjectPage({ id, me }) {
  const [p, setP] = useState(null);
  const [err, setErr] = useState('');
  const [showMembers, setShowMembers] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncReport, setSyncReport] = useState(null);

  const load = () => api.get(`api/projects/${id}`).then(setP).catch(e => setErr(e.message));
  useEffect(() => { load(); }, [id]);

  async function syncSharePoint() {
    setSyncing(true); setErr('');
    try {
      const r = await api.post(`api/projects/${id}/sharepoint/sync`);
      setSyncReport(r);
      load(); // pulls may have created drafts
    } catch (e) { setErr(e.message); }
    finally { setSyncing(false); }
  }

  if (err) return <div className="page"><p style={{ color: 'var(--bad)' }}>{err}</p></div>;
  if (!p) return <div className="page" style={{ textAlign: 'center', paddingTop: 80 }}><span className="spin dark" /></div>;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="crumb small"><a href="#/">Projects</a> / <b>{p.name}</b></div>
          <h1 className="title" style={{ marginTop: 6 }}>{p.name}</h1>
          <p className="soft small" style={{ margin: '4px 0 0' }}>
            {p.template_name} · v{p.template_version}{p.description ? ` — ${p.description}` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={syncSharePoint} disabled={syncing}
                  title="Two-way sync: push documents to DioXengine/<project>/ on the SharePoint site, pull back edits made there">
            {syncing ? <span className="spin dark" /> : '⇅ SharePoint'}
          </button>
          <button className="btn" onClick={() => setShowMembers(true)}>
            Members <span className="muted">({p.members.length})</span>
          </button>
          {p.can_manage_members && (
            <button className="btn danger" title="Delete this project and all its documents"
                    onClick={async () => {
                      if (!window.confirm(`Delete the project “${p.name}” and ALL its documents?\nThis cannot be undone.`)) return;
                      try { await api.del(`api/projects/${p.id}`); window.location.hash = '#/'; }
                      catch (e) { setErr(e.message); }
                    }}>Delete</button>
          )}
        </div>
      </div>

      <div className="card dag-box">
        <Dag project={p} />
      </div>

      <h2 className="subtitle">Documents</h2>
      <div className="card">
        {p.documents.map(d => <DocRow key={d.id} d={d} />)}
      </div>

      {showMembers && <Members p={p} onClose={() => setShowMembers(false)} onDone={load} />}
      {syncReport && <SyncReport r={syncReport} onClose={() => setSyncReport(null)} />}
    </div>
  );
}

const SYNC_BADGE = {
  pushed: { label: 'pushed', color: 'var(--ok)' },
  pulled: { label: 'pulled', color: 'var(--accent)' },
  up_to_date: { label: 'up to date', color: 'var(--ink-faint)' },
  skipped: { label: 'skipped', color: 'var(--ink-faint)' },
  locked: { label: 'locked', color: 'var(--warn)' },
  conflict: { label: 'conflict', color: 'var(--bad)' },
  error: { label: 'error', color: 'var(--bad)' },
};

function SyncReport({ r, onClose }) {
  return (
    <div className="overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 640 }}>
        <h2>SharePoint sync</h2>
        {r.folder && (
          <p className="soft small" style={{ marginTop: -8 }}>
            Folder: {r.folder_url
              ? <a href={r.folder_url} target="_blank" rel="noreferrer">{r.folder}</a>
              : r.folder}
          </p>
        )}
        {(r.report || []).map((row, i) => {
          const b = SYNC_BADGE[row.action] || { label: row.action, color: 'var(--ink-soft)' };
          return (
            <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '6px 0',
                                  borderBottom: '1px solid var(--line)' }}>
              <span className="pill" style={{ color: b.color, borderColor: 'currentColor', flexShrink: 0 }}>{b.label}</span>
              <span style={{ fontWeight: 600, fontSize: 13.5, flexShrink: 0 }}>{row.document}</span>
              <span className="muted small" style={{ minWidth: 0 }}>
                {row.web_url ? <a href={row.web_url} target="_blank" rel="noreferrer">{row.detail}</a> : row.detail}
              </span>
            </div>
          );
        })}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function statusOf(d) {
  const s = d.latest?.status;
  if (!s) return { cls: 'empty', label: 'not started' };
  if (s === 'superseded') return { cls: 'approved', label: 'approved' };
  return { cls: s, label: s };
}

function DocRow({ d }) {
  const st = statusOf(d);
  return (
    <a className="rowlink" href={`#/documents/${d.id}`}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>
          {d.name}
          {d.open_comments > 0 && (
            <span className="comment-dot" style={{ display: 'inline-flex', marginLeft: 8, verticalAlign: 'middle' }}>
              {d.open_comments}
            </span>
          )}
        </div>
        <div className="muted small">
          {d.author_role}{d.author_email ? ` · ${d.author_email.split('@')[0]}` : ''}
          {d.latest ? ` · rev ${d.latest.version_number} ${timeAgo(d.latest.updated_at || d.latest.created_at)}` : ''}
        </div>
      </div>
      {d.stale && <span className="pill stale" title={d.stale_reasons.join('\n')}>stale</span>}
      <span className={`pill ${st.cls}`}>{st.label}</span>
    </a>
  );
}

/* ---- DAG: topological layers, left → right ---- */
function Dag({ project }) {
  const layout = useMemo(() => {
    const docs = project.documents;
    const byNode = new Map(docs.map(d => [d.node_id, d]));
    const indeg = new Map(docs.map(d => [d.node_id, 0]));
    const out = new Map();
    for (const e of project.edges) {
      indeg.set(e.to_node_id, (indeg.get(e.to_node_id) || 0) + 1);
      out.set(e.from_node_id, [...(out.get(e.from_node_id) || []), e.to_node_id]);
    }
    // longest-path layering
    const layer = new Map();
    const q = docs.filter(d => (indeg.get(d.node_id) || 0) === 0).map(d => d.node_id);
    q.forEach(n => layer.set(n, 0));
    const indegW = new Map(indeg);
    const queue = [...q];
    while (queue.length) {
      const n = queue.shift();
      for (const m of out.get(n) || []) {
        layer.set(m, Math.max(layer.get(m) || 0, (layer.get(n) || 0) + 1));
        indegW.set(m, (indegW.get(m) || 0) - 1);
        if ((indegW.get(m) || 0) === 0) queue.push(m);
      }
    }
    const cols = new Map();
    for (const d of docs) {
      const l = layer.get(d.node_id) || 0;
      cols.set(l, [...(cols.get(l) || []), d.node_id]);
    }
    const W = 168, H = 46, GX = 56, GY = 14;
    const pos = new Map();
    let maxRows = 0;
    for (const [, nodes] of cols) maxRows = Math.max(maxRows, nodes.length);
    const totalH = maxRows * (H + GY);
    for (const [l, nodes] of [...cols.entries()].sort((a, b) => a[0] - b[0])) {
      const colH = nodes.length * (H + GY) - GY;
      nodes.forEach((n, i) => {
        pos.set(n, { x: l * (W + GX), y: (totalH - colH) / 2 + i * (H + GY) });
      });
    }
    const width = (Math.max(...[...cols.keys()]) + 1) * (W + GX) - GX;
    return { pos, byNode, W, H, width, height: totalH };
  }, [project]);

  const { pos, byNode, W, H } = layout;

  return (
    <svg width={layout.width + 4} height={layout.height + 4} style={{ display: 'block', margin: '0 auto' }}>
      <defs>
        <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0.8 L7,4 L0,7.2" fill="none" stroke="var(--line-strong)" strokeWidth="1.3" />
        </marker>
      </defs>
      {project.edges.map((e, i) => {
        const a = pos.get(e.from_node_id), b = pos.get(e.to_node_id);
        if (!a || !b) return null;
        const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2, y2 = b.y + H / 2;
        const mx = (x1 + x2) / 2;
        return <path key={i} className="dag-edge" markerEnd="url(#arr)"
                     d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} />;
      })}
      {[...pos.entries()].map(([nodeId, pt]) => {
        const d = byNode.get(nodeId);
        if (!d) return null;
        const st = d.stale ? 'stale' : (d.latest?.status === 'superseded' ? 'approved' : d.latest?.status || 'empty');
        return (
          <g key={nodeId} className={`dag-node st-${st}`} transform={`translate(${pt.x},${pt.y})`}
             onClick={() => (window.location.hash = `#/documents/${d.id}`)}>
            <rect width={W} height={H} rx={8} />
            <text x={11} y={19}>{d.name.length > 24 ? d.name.slice(0, 23) + '…' : d.name}</text>
            <text x={11} y={34} className="sub">
              {d.latest ? `rev ${d.latest.version_number} · ${st}` : 'not started'}
              {d.open_comments ? ` · ${d.open_comments}💬` : ''}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function Members({ p, onClose, onDone }) {
  const [emails, setEmails] = useState(p.members.join('\n'));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function save() {
    setBusy(true); setErr('');
    try {
      await api.put(`api/projects/${p.id}/members`, { members: emails.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean) });
      onDone(); onClose();
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return (
    <div className="overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <h2>Project members</h2>
        <p className="soft small" style={{ marginTop: -8 }}>
          One email per line. Members see the project and can work on its documents.
        </p>
        <textarea className="input" rows={6} value={emails} onChange={e => setEmails(e.target.value)}
                  readOnly={!p.can_manage_members} />
        {err && <p style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</p>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
          {p.can_manage_members && (
            <button className="btn primary" onClick={save} disabled={busy}>
              {busy ? <span className="spin" /> : 'Save'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
