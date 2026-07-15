import React, { useEffect, useState } from 'react';
import { api } from '../utils/api';

/* Template editor: define the workflow DAG — document types (with their
   section schemas) and the edges between them. Draft versions are editable;
   published versions are frozen (create a new version to change them). */

const EMPTY_NODE = () => ({
  node_key: '', name: '', description: '', author_role: '', reviewer_role: '',
  receiver_roles: [], content_schema: { sections: [] },
});

export default function TemplateEditor({ tvid, me }) {
  const [tv, setTv] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]); // [{from_key, to_key}]
  const [sel, setSel] = useState(0);      // selected node index
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  const load = () => api.get(`api/template-versions/${tvid}`).then(d => {
    setTv(d);
    const keyOf = Object.fromEntries(d.nodes.map(n => [n.id, n.node_key]));
    setNodes(d.nodes.map(n => ({
      node_key: n.node_key, name: n.name, description: n.description,
      author_role: n.author_role, reviewer_role: n.reviewer_role,
      receiver_roles: n.receiver_roles || [],
      content_schema: n.content_schema?.sections ? n.content_schema : { sections: [] },
    })));
    setEdges(d.edges.map(e => ({ from_key: keyOf[e.from_node_id], to_key: keyOf[e.to_node_id] })));
    setDirty(false);
  }).catch(e => setErr(e.message));

  useEffect(() => { load(); }, [tvid]);

  if (err && !tv) return <div className="page"><p style={{ color: 'var(--bad)' }}>{err}</p></div>;
  if (!tv) return <div className="page" style={{ textAlign: 'center', paddingTop: 80 }}><span className="spin dark" /></div>;

  const editable = tv.can_edit;
  const node = nodes[sel];

  function patchNode(patch) {
    setNodes(nodes.map((n, i) => (i === sel ? { ...n, ...patch } : n)));
    setDirty(true);
  }
  function patchSections(sections) {
    patchNode({ content_schema: { sections } });
  }
  function addNode() {
    setNodes([...nodes, EMPTY_NODE()]);
    setSel(nodes.length);
    setDirty(true);
  }
  function removeNode(i) {
    const key = nodes[i].node_key;
    setNodes(nodes.filter((_, x) => x !== i));
    setEdges(edges.filter(e => e.from_key !== key && e.to_key !== key));
    setSel(Math.max(0, Math.min(sel, nodes.length - 2)));
    setDirty(true);
  }

  async function save() {
    setBusy(true); setErr(''); setMsg('');
    try {
      await api.put(`api/template-versions/${tvid}`, { nodes, edges });
      setDirty(false);
      setMsg('Saved.');
      setTimeout(() => setMsg(''), 2500);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function publish() {
    if (dirty) await save();
    if (!window.confirm('Publish this version? Published versions are frozen — later changes need a new version.')) return;
    setBusy(true); setErr('');
    try { await api.post(`api/template-versions/${tvid}/publish`); load(); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function newVersion() {
    setBusy(true); setErr('');
    try {
      const r = await api.post(`api/templates/${tv.template_id}/versions`);
      window.location.hash = `#/templates/${r.template_version_id}`;
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return (
    <div className="page" style={{ maxWidth: 1240 }}>
      <div className="page-head" style={{ alignItems: 'center' }}>
        <div>
          <div className="crumb small"><a href="#/">Projects</a> / <b>{tv.template_name}</b></div>
          <h1 className="title" style={{ marginTop: 6 }}>
            {tv.template_name} <span className="muted" style={{ fontWeight: 400 }}>· v{tv.version_number}</span>
            <span className={`pill ${tv.status === 'published' ? 'approved' : 'draft'}`} style={{ marginLeft: 10, verticalAlign: 'middle' }}>
              {tv.status}
            </span>
          </h1>
          <p className="soft small" style={{ margin: '4px 0 0' }}>{tv.template_description}</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          {msg && <span className="soft small" style={{ alignSelf: 'center' }}>{msg}</span>}
          {editable && <button className="btn" onClick={save} disabled={busy || !dirty}>Save</button>}
          {editable && <button className="btn primary" onClick={publish} disabled={busy || !nodes.length}>Publish</button>}
          {tv.is_owner && tv.status === 'published' && (
            <button className="btn" onClick={newVersion} disabled={busy}>New draft version</button>
          )}
        </div>
      </div>

      {!editable && tv.status === 'published' && (
        <div className="card" style={{ padding: '10px 16px', marginBottom: 14, background: 'var(--accent-soft)', borderColor: '#c7d2fe' }}>
          <span className="small" style={{ color: 'var(--accent)' }}>
            This version is published and frozen. {tv.is_owner ? 'Create a new draft version to change it.' : ''}
          </span>
        </div>
      )}
      {err && <p style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: '260px minmax(0,1fr)', gap: 18, alignItems: 'start' }}>
        {/* node list */}
        <div className="card" style={{ overflow: 'hidden' }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', fontWeight: 600, fontSize: 13 }}>
            Documents <span className="muted">({nodes.length})</span>
          </div>
          {nodes.map((n, i) => (
            <div key={i} className="rowlink" style={{ padding: '9px 14px', cursor: 'pointer', background: i === sel ? 'var(--accent-soft)' : undefined }}
                 onClick={() => setSel(i)}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: i === sel ? 600 : 450, fontSize: 13.5 }}>{n.name || <i className="muted">unnamed</i>}</div>
                <div className="doc-no">{n.node_key || '—'}</div>
              </div>
              {editable && <button className="icon-btn" onClick={e => { e.stopPropagation(); removeNode(i); }}>✕</button>}
            </div>
          ))}
          {editable && (
            <div style={{ padding: 10 }}>
              <button className="add-row" style={{ width: '100%' }} onClick={addNode}>+ Add document type</button>
            </div>
          )}
        </div>

        {/* node detail + edges */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {node ? (
            <NodeForm node={node} editable={editable} onChange={patchNode} onSections={patchSections} />
          ) : (
            <div className="card" style={{ padding: 30, textAlign: 'center' }} >
              <span className="soft">No document types yet — add one on the left.</span>
            </div>
          )}
          <EdgesEditor nodes={nodes} edges={edges} editable={editable}
                       onChange={e => { setEdges(e); setDirty(true); }} />
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return <div><label className="lbl">{label}</label>{children}</div>;
}

function NodeForm({ node, editable, onChange, onSections }) {
  const sections = node.content_schema.sections || [];

  function patchSection(i, patch) {
    onSections(sections.map((s, x) => (x === i ? { ...s, ...patch } : s)));
  }
  function addSection(type) {
    onSections([...sections, { key: '', title: '', type, ...(type === 'table' ? { columns: [] } : {}) }]);
  }

  return (
    <div className="card" style={{ padding: '18px 20px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr 1fr', gap: 12, marginBottom: 12 }}>
        <Field label="Key (id)">
          <input className="input mono" style={{ fontSize: 13 }} value={node.node_key} readOnly={!editable}
                 placeholder="ex: el" onChange={e => onChange({ node_key: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '') })} />
        </Field>
        <Field label="Name">
          <input className="input" value={node.name} readOnly={!editable}
                 placeholder="ex: Sized Equipment List" onChange={e => onChange({ name: e.target.value })} />
        </Field>
        <Field label="Description">
          <input className="input" value={node.description} readOnly={!editable}
                 onChange={e => onChange({ description: e.target.value })} />
        </Field>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <Field label="Author role">
          <input className="input" value={node.author_role} readOnly={!editable}
                 placeholder="ex: Process Engineer" onChange={e => onChange({ author_role: e.target.value })} />
        </Field>
        <Field label="Reviewer role">
          <input className="input" value={node.reviewer_role} readOnly={!editable}
                 placeholder="ex: Director of Engineering" onChange={e => onChange({ reviewer_role: e.target.value })} />
        </Field>
      </div>

      <h2 className="subtitle" style={{ margin: '4px 0 8px' }}>Sections</h2>
      {sections.map((s, i) => (
        <div key={i} style={{ border: '1px solid var(--line)', borderRadius: 8, padding: '10px 12px', marginBottom: 10 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '130px 1fr 110px 30px', gap: 10, alignItems: 'end' }}>
            <Field label="Key">
              <input className="input mono" style={{ fontSize: 12.5 }} value={s.key} readOnly={!editable}
                     onChange={e => patchSection(i, { key: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '') })} />
            </Field>
            <Field label="Title">
              <input className="input" value={s.title} readOnly={!editable}
                     onChange={e => patchSection(i, { title: e.target.value })} />
            </Field>
            <Field label="Type">
              <select className="input" value={s.type} disabled={!editable}
                      onChange={e => patchSection(i, { type: e.target.value, ...(e.target.value === 'table' && !s.columns ? { columns: [] } : {}) })}>
                <option value="text">text</option>
                <option value="table">table</option>
              </select>
            </Field>
            {editable && (
              <button className="icon-btn" title="Remove section"
                      onClick={() => onSections(sections.filter((_, x) => x !== i))}>✕</button>
            )}
          </div>
          {s.type === 'table' && (
            <ColumnsEditor columns={s.columns || []} editable={editable}
                           onChange={cols => patchSection(i, { columns: cols })} />
          )}
        </div>
      ))}
      {editable && (
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="add-row" onClick={() => addSection('text')}>+ Text section</button>
          <button className="add-row" onClick={() => addSection('table')}>+ Table section</button>
        </div>
      )}
    </div>
  );
}

function ColumnsEditor({ columns, editable, onChange }) {
  function patch(i, p) { onChange(columns.map((c, x) => (x === i ? { ...c, ...p } : c))); }
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed var(--line)' }}>
      <div className="doc-no" style={{ marginBottom: 6 }}>COLUMNS</div>
      {columns.map((c, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '150px 1fr 110px 30px', gap: 8, marginBottom: 6 }}>
          <input className="input mono" style={{ fontSize: 12.5, padding: '5px 9px' }} placeholder="key"
                 value={c.key} readOnly={!editable}
                 onChange={e => patch(i, { key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '') })} />
          <input className="input" style={{ fontSize: 13, padding: '5px 9px' }} placeholder="Label (with unit)"
                 value={c.label} readOnly={!editable} onChange={e => patch(i, { label: e.target.value })} />
          <select className="input" style={{ fontSize: 13, padding: '5px 9px' }} value={c.type} disabled={!editable}
                  onChange={e => patch(i, { type: e.target.value })}>
            <option value="text">text</option>
            <option value="number">number</option>
          </select>
          {editable && (
            <button className="icon-btn" onClick={() => onChange(columns.filter((_, x) => x !== i))}>✕</button>
          )}
        </div>
      ))}
      {editable && (
        <button className="add-row" style={{ fontSize: 12 }}
                onClick={() => onChange([...columns, { key: '', label: '', type: 'text' }])}>+ Column</button>
      )}
    </div>
  );
}

function EdgesEditor({ nodes, edges, editable, onChange }) {
  const keys = nodes.map(n => n.node_key).filter(Boolean);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const nameOf = Object.fromEntries(nodes.map(n => [n.node_key, n.name || n.node_key]));

  return (
    <div className="card" style={{ padding: '14px 18px' }}>
      <h2 className="subtitle" style={{ margin: '0 0 10px' }}>Links (upstream → downstream)</h2>
      {edges.length === 0 && <p className="muted small" style={{ margin: '0 0 8px' }}>No links yet — a document's upstream links define what its content is derived from (and drive staleness).</p>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: editable ? 12 : 0 }}>
        {edges.map((e, i) => (
          <span key={i} className="pill draft" style={{ textTransform: 'none', fontSize: 12, letterSpacing: 0, padding: '4px 10px' }}>
            {nameOf[e.from_key] || e.from_key} → {nameOf[e.to_key] || e.to_key}
            {editable && (
              <button className="icon-btn" style={{ width: 16, height: 16, marginLeft: 2 }}
                      onClick={() => onChange(edges.filter((_, x) => x !== i))}>✕</button>
            )}
          </span>
        ))}
      </div>
      {editable && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select className="input" style={{ width: 'auto' }} value={from} onChange={e => setFrom(e.target.value)}>
            <option value="">from…</option>
            {keys.map(k => <option key={k} value={k}>{nameOf[k]}</option>)}
          </select>
          <span className="muted">→</span>
          <select className="input" style={{ width: 'auto' }} value={to} onChange={e => setTo(e.target.value)}>
            <option value="">to…</option>
            {keys.map(k => <option key={k} value={k}>{nameOf[k]}</option>)}
          </select>
          <button className="btn sm" disabled={!from || !to || from === to
                    || edges.some(e => e.from_key === from && e.to_key === to)}
                  onClick={() => { onChange([...edges, { from_key: from, to_key: to }]); setFrom(''); setTo(''); }}>
            Add link
          </button>
        </div>
      )}
    </div>
  );
}
