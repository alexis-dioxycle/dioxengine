import React, { useEffect, useState } from 'react';
import { api } from '../utils/api';
import { timeAgo } from '../App';

export default function Home({ me }) {
  const [projects, setProjects] = useState(null);
  const [templates, setTemplates] = useState(null);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState('');

  const load = () => {
    api.get('api/projects').then(setProjects).catch(e => setErr(e.message));
    api.get('api/templates').then(setTemplates).catch(e => setErr(e.message));
  };
  useEffect(load, []);

  async function seed(path) {
    try { await api.post(path); load(); }
    catch (e) { setErr(e.message); }
  }

  async function deleteTemplate(e, t) {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm(`Delete the workflow template “${t.name}” (all its versions)?\nThis is refused if projects still use it.`)) return;
    try { await api.del(`api/templates/${t.id}`); load(); }
    catch (err) { setErr(err.message); }
  }

  async function newTemplate() {
    const name = window.prompt('Template name (e.g. "BOS Full Workflow"):');
    if (!name) return;
    try {
      const r = await api.post('api/templates', { name, description: '' });
      window.location.hash = `#/templates/${r.template_version_id}`;
    } catch (e) { setErr(e.message); }
  }

  const published = (templates || []).flatMap(t =>
    t.versions.filter(v => v.status === 'published').map(v => ({
      template: t, version: v,
    })));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="title">Projects</h1>
          <p className="soft small" style={{ margin: '4px 0 0' }}>
            Each project instantiates a workflow template — a DAG of documents that feed each other.
          </p>
        </div>
        <button className="btn primary" onClick={() => setCreating(true)}
                disabled={!published.length}>+ New project</button>
      </div>

      {err && <p style={{ color: 'var(--bad)' }}>{err}</p>}

      <div className="card">
        {projects === null ? (
          <div style={{ padding: 30, textAlign: 'center' }}><span className="spin dark" /></div>
        ) : projects.length === 0 ? (
          <div style={{ padding: '36px 24px', textAlign: 'center' }}>
            <p className="soft" style={{ margin: 0 }}>No projects yet.</p>
            {published.length === 0 && (
              <p className="muted small">
                Start from a seed:&nbsp;
                <button className="btn sm" onClick={() => seed('api/seed-workflow1')}>Seed “Workflow 1 — Procurement”</button>
                &nbsp;<button className="btn sm" onClick={() => seed('api/seed-workflow2')}>Seed “Workflow 2 — Control & Safety”</button>
                &nbsp;<button className="btn sm" onClick={() => seed('api/seed-example')}>Seed “Electrolyzer Basic Engineering”</button>
              </p>
            )}
          </div>
        ) : projects.map(p => (
          <a key={p.id} className="rowlink" href={`#/projects/${p.id}`}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{p.name}</div>
              <div className="muted small">
                {p.template_name} · v{p.template_version} · created {timeAgo(p.created_at)} by {p.created_by.split('@')[0]}
              </div>
            </div>
            <Progress done={p.n_approved} total={p.n_documents} />
            <span className="doc-no">{p.n_approved}/{p.n_documents} approved</span>
          </a>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h2 className="subtitle">Workflow templates</h2>
        <button className="btn sm" onClick={newTemplate}>+ New template</button>
      </div>
      <div className="card">
        {templates === null ? (
          <div style={{ padding: 24, textAlign: 'center' }}><span className="spin dark" /></div>
        ) : templates.length === 0 ? (
          <div style={{ padding: '26px 24px' }}>
            <span className="soft">No templates visible to you. </span>
            <button className="btn sm" onClick={() => seed('api/seed-workflow1')}>Seed Workflow 1</button>
          </div>
        ) : templates.map(t => {
          const latest = t.versions[t.versions.length - 1];
          return (
            <a key={t.id} className="rowlink" href={`#/templates/${latest?.id}`}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{t.name}</div>
                <div className="muted small">{t.description}</div>
              </div>
              {t.versions.map(v => (
                <span key={v.id} className={`pill ${v.status === 'published' ? 'approved' : 'draft'}`}>
                  v{v.version_number} {v.status}
                </span>
              ))}
              {t.is_owner && (
                <button className="icon-btn" title="Delete this template (all versions)"
                        onClick={e => deleteTemplate(e, t)}>✕</button>
              )}
            </a>
          );
        })}
      </div>

      {creating && <NewProject published={published} onClose={() => setCreating(false)} onDone={load} />}
    </div>
  );
}

function Progress({ done, total }) {
  const pct = total ? (done / total) * 100 : 0;
  return (
    <div style={{ width: 90, height: 5, borderRadius: 3, background: '#e6eaf2', overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: pct === 100 ? 'var(--ok)' : 'var(--accent-mid)', transition: 'width .3s' }} />
    </div>
  );
}

function NewProject({ published, onClose, onDone }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tv, setTv] = useState(published[0]?.version.id ?? 0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function create(e) {
    e.preventDefault();
    setBusy(true); setErr('');
    try {
      const r = await api.post('api/projects', { name, description, template_version_id: tv });
      onDone(); onClose();
      window.location.hash = `#/projects/${r.project_id}`;
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return (
    <div className="overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <form className="modal" onSubmit={create}>
        <h2>New project</h2>
        <label className="lbl">Name</label>
        <input className="input" autoFocus required value={name} onChange={e => setName(e.target.value)}
               placeholder="e.g. BOS Pilot — Fos-sur-Mer" style={{ marginBottom: 14 }} />
        <label className="lbl">Description</label>
        <input className="input" value={description} onChange={e => setDescription(e.target.value)}
               style={{ marginBottom: 14 }} />
        <label className="lbl">Workflow template</label>
        <select className="input" value={tv} onChange={e => setTv(Number(e.target.value))} style={{ marginBottom: 20 }}>
          {published.map(p => (
            <option key={p.version.id} value={p.version.id}>
              {p.template.name} — v{p.version.version_number}
            </option>
          ))}
        </select>
        {err && <p style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</p>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !name || !tv}>
            {busy ? <span className="spin" /> : 'Create project'}
          </button>
        </div>
      </form>
    </div>
  );
}
