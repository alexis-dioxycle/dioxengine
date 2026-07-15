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

      {/* the workflow itself: an interactive DAG */}
      <div className="card dag-box" style={{ marginBottom: 18 }}>
        <TemplateGraph nodes={nodes} edges={edges} sel={sel} editable={editable}
                       onSelect={setSel}
                       onEdges={e => { setEdges(e); setDirty(true); }}
                       onAddNode={addNode} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '240px minmax(0,1fr)', gap: 18, alignItems: 'start' }}>
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

        {/* node detail */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {node ? (
            <NodeForm node={node} editable={editable} onChange={patchNode} onSections={patchSections} />
          ) : (
            <div className="card" style={{ padding: 30, textAlign: 'center' }} >
              <span className="soft">No document types yet — add one on the left.</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---- interactive DAG: click a node to select it; in link mode, click a
   source then a target to create a link; hover a link to delete it ---- */
function TemplateGraph({ nodes, edges, sel, editable, onSelect, onEdges, onAddNode }) {
  const [linkFrom, setLinkFrom] = useState(null); // node_key being linked, or null
  const [hoverEdge, setHoverEdge] = useState(-1);

  const keyed = nodes.map((n, i) => ({ ...n, _i: i, node_key: n.node_key || `_tmp${i}` }));
  const keys = keyed.map(n => n.node_key);

  // longest-path layering (same as the project DAG)
  const indeg = new Map(keys.map(k => [k, 0]));
  const out = new Map();
  const validEdges = edges.filter(e => keys.includes(e.from_key) && keys.includes(e.to_key));
  for (const e of validEdges) {
    indeg.set(e.to_key, (indeg.get(e.to_key) || 0) + 1);
    out.set(e.from_key, [...(out.get(e.from_key) || []), e.to_key]);
  }
  const layer = new Map();
  const queue = keys.filter(k => (indeg.get(k) || 0) === 0);
  queue.forEach(k => layer.set(k, 0));
  const indegW = new Map(indeg);
  const q2 = [...queue];
  while (q2.length) {
    const n = q2.shift();
    for (const m of out.get(n) || []) {
      layer.set(m, Math.max(layer.get(m) || 0, (layer.get(n) || 0) + 1));
      indegW.set(m, (indegW.get(m) || 0) - 1);
      if ((indegW.get(m) || 0) === 0) q2.push(m);
    }
  }
  const cols = new Map();
  for (const k of keys) {
    const l = layer.get(k) || 0;
    cols.set(l, [...(cols.get(l) || []), k]);
  }
  const W = 164, H = 44, GX = 60, GY = 16;
  let maxRows = 1;
  for (const [, ns] of cols) maxRows = Math.max(maxRows, ns.length);
  const totalH = maxRows * (H + GY);
  const pos = new Map();
  for (const [l, ns] of [...cols.entries()].sort((a, b) => a[0] - b[0])) {
    const colH = ns.length * (H + GY) - GY;
    ns.forEach((k, i) => pos.set(k, { x: l * (W + GX), y: (totalH - colH) / 2 + i * (H + GY) }));
  }
  const width = cols.size ? (Math.max(...[...cols.keys()]) + 1) * (W + GX) - GX : 200;

  function clickNode(k, i) {
    if (linkFrom === null) { onSelect(i); return; }
    if (linkFrom === '') { setLinkFrom(k); return; } // picking source
    if (linkFrom === k) { setLinkFrom(''); return; } // unpick
    if (!edges.some(e => e.from_key === linkFrom && e.to_key === k)) {
      onEdges([...edges, { from_key: linkFrom, to_key: k }]);
    }
    setLinkFrom(null);
  }

  const nameOf = Object.fromEntries(keyed.map(n => [n.node_key, n.name || n.node_key]));

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 6px 10px' }}>
        <span className="doc-no">Workflow</span>
        <span className="spacer" style={{ flex: 1 }} />
        {editable && (
          linkFrom === null ? (
            <button className="btn sm" onClick={() => setLinkFrom('')}>🔗 Add link</button>
          ) : (
            <span className="small" style={{ color: 'var(--accent)', display: 'flex', gap: 8, alignItems: 'center' }}>
              {linkFrom === '' ? 'Click the SOURCE document…' : <>From <b>{nameOf[linkFrom]}</b> — click the TARGET…</>}
              <button className="btn ghost sm" onClick={() => setLinkFrom(null)}>cancel</button>
            </span>
          )
        )}
      </div>
      <svg width={Math.max(width + 4, 200)} height={totalH + 4} style={{ display: 'block', margin: '0 auto' }}>
        <defs>
          <marker id="tarr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0.8 L7,4 L0,7.2" fill="none" stroke="var(--line-strong)" strokeWidth="1.3" />
          </marker>
        </defs>
        {validEdges.map((e, i) => {
          const a = pos.get(e.from_key), b = pos.get(e.to_key);
          if (!a || !b) return null;
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2, y2 = b.y + H / 2;
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          const ei = edges.indexOf(e);
          return (
            <g key={i} onMouseEnter={() => setHoverEdge(ei)} onMouseLeave={() => setHoverEdge(-1)}>
              <path className="dag-edge" markerEnd="url(#tarr)"
                    style={hoverEdge === ei ? { stroke: 'var(--accent-mid)', strokeWidth: 2 } : {}}
                    d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} />
              {/* fat invisible hover target */}
              <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                    stroke="transparent" strokeWidth="14" fill="none" />
              {editable && hoverEdge === ei && (
                <g style={{ cursor: 'pointer' }} onClick={() => onEdges(edges.filter((_, x) => x !== ei))}>
                  <circle cx={mx} cy={my} r={9} fill="var(--paper)" stroke="var(--bad)" strokeWidth="1.3" />
                  <text x={mx} y={my + 3.5} textAnchor="middle" style={{ font: '600 10px sans-serif', fill: 'var(--bad)' }}>✕</text>
                </g>
              )}
            </g>
          );
        })}
        {keyed.map(n => {
          const pt = pos.get(n.node_key);
          if (!pt) return null;
          const isSel = n._i === sel, isFrom = linkFrom === n.node_key;
          return (
            <g key={n.node_key} className="dag-node" transform={`translate(${pt.x},${pt.y})`}
               style={linkFrom !== null ? { cursor: 'crosshair' } : {}}
               onClick={() => clickNode(n.node_key, n._i)}>
              <rect width={W} height={H} rx={8}
                    style={isFrom ? { stroke: 'var(--accent)', strokeWidth: 2, strokeDasharray: '5 3' }
                          : isSel ? { stroke: 'var(--accent-mid)', strokeWidth: 1.8, fill: 'var(--accent-soft)' } : {}} />
              <text x={11} y={19}>{(n.name || n.node_key).length > 23 ? (n.name || n.node_key).slice(0, 22) + '…' : (n.name || n.node_key)}</text>
              <text x={11} y={33} className="sub">{n.node_key} · {(n.content_schema.sections || []).length} sections</text>
            </g>
          );
        })}
      </svg>
      {nodes.length === 0 && editable && (
        <div style={{ textAlign: 'center', padding: 10 }}>
          <button className="add-row" onClick={onAddNode}>+ Add the first document type</button>
        </div>
      )}
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
