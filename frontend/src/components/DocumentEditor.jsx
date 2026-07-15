import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../utils/api';
import { initials, timeAgo } from '../App';
import FlowDiagram from './FlowDiagram';

/* The document IS the interface: a paper sheet with editable prose and
   spreadsheet sections. JSON never appears. Edits made by the assistant (via
   the API, actor_kind='assistant') land in the same draft and show up live
   via polling, attributed in orange. */

const POLL_MS = 4000;

export default function DocumentEditor({ id, me }) {
  const [doc, setDoc] = useState(null);
  const [content, setContent] = useState({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [flashKeys, setFlashKeys] = useState(new Set());
  const [commentDraft, setCommentDraft] = useState(null);
  const [showResolved, setShowResolved] = useState(false);
  const [viewVersion, setViewVersion] = useState(null); // read-only past rev
  const [oldContent, setOldContent] = useState(null);
  const [attachments, setAttachments] = useState([]);
  const [previewId, setPreviewId] = useState(null);      // attachment shown inline
  const [highlight, setHighlight] = useState(null);      // {section, row} from comment hover
  const [railComments, setRailComments] = useState(true);
  const [railActivity, setRailActivity] = useState(false);

  const serverStamp = useRef(null);
  const dirtyRef = useRef(false);
  const savingRef = useRef(false);
  dirtyRef.current = dirty;
  savingRef.current = saving;

  const contentRef = useRef({});
  contentRef.current = content;

  /* ---------- load + poll ---------- */
  const apply = useCallback((d, external) => {
    setDoc(d);
    if (!dirtyRef.current && !savingRef.current) {
      if (external && serverStamp.current && d.latest_updated_at !== serverStamp.current) {
        // Something (assistant or teammate) changed the draft under us.
        const changed = new Set(Object.keys(d.latest_content).filter(
          k => JSON.stringify(d.latest_content[k]) !== JSON.stringify(contentRef.current[k])));
        if (changed.size) {
          setFlashKeys(changed);
          setTimeout(() => setFlashKeys(new Set()), 2300);
          api.get(`api/documents/${id}/activity?limit=1`).then(a => {
            const last = a[0];
            if (last?.actor_kind === 'assistant') setToastTimed('Claude updated this document');
            else if (last && last.actor_email !== me.email) setToastTimed(`${last.actor_email.split('@')[0]} updated this document`);
          }).catch(() => {});
        }
      }
      setContent(d.latest_content || {});
      serverStamp.current = d.latest_updated_at;
    }
  }, [id, me.email]);

  useEffect(() => {
    let alive = true;
    api.get(`api/documents/${id}`).then(d => alive && apply(d, false)).catch(e => setErr(e.message));
    const t = setInterval(() => {
      api.get(`api/documents/${id}`).then(d => alive && apply(d, true)).catch(() => {});
    }, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [id, apply]);

  useEffect(() => {
    api.get(`api/documents/${id}/attachments`).then(a => {
      setAttachments(a);
      // auto-open the deliverable if there is one (the drawing IS the
      // document for a P&ID), else the first PDF
      const pdf = a.find(x => x.kind === 'deliverable')
        || a.find(x => x.content_type === 'application/pdf');
      if (pdf) setPreviewId(prev => prev ?? pdf.id);
    }).catch(() => {});
  }, [id, doc?.latest_updated_at]);

  async function uploadFile(file) {
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch(`api/documents/${id}/attachments`, { method: 'POST', body: form });
      if (!res.ok) throw new Error((await res.json())?.detail || `HTTP ${res.status}`);
      setAttachments(await api.get(`api/documents/${id}/attachments`));
      setToastTimed(`Attached ${file.name}`);
    } catch (e) { setErr(e.message); }
  }

  async function setAttachmentKind(a, kind) {
    try {
      await api.put(`api/documents/${id}/attachments/${a.id}`, { kind });
      setAttachments(await api.get(`api/documents/${id}/attachments`));
      if (kind === 'deliverable') setPreviewId(a.id);
      setToastTimed(kind === 'deliverable'
        ? `${a.filename} is now the deliverable — this file IS the document`
        : `${a.filename} is a reference file again`);
    } catch (e) { setErr(e.message); }
  }

  function jumpTo(section, row) {
    const el = document.getElementById(row !== null && row !== undefined && row >= 0
      ? `sec-${section}-row-${row}` : `sec-${section}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setHighlight({ section, row });
      setTimeout(() => setHighlight(null), 2200);
    }
  }

  function setToastTimed(msg) {
    setToast(msg);
    setTimeout(() => setToast(''), 3500);
  }

  /* ---------- autosave ---------- */
  const saveTimer = useRef(null);
  function edit(next) {
    setContent(next);
    setDirty(true);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => void save(next), 900);
  }

  async function save(c) {
    setSaving(true);
    try {
      const r = await api.put(`api/documents/${id}/draft`, { content: c });
      serverStamp.current = r.updated_at;
      setDirty(false);
      setErr('');
    } catch (e) {
      setErr(e.message);
    } finally { setSaving(false); }
  }

  /* ---------- actions ---------- */
  async function submit() {
    if (dirty) await save(content);
    try {
      await api.post(`api/documents/${id}/submit`);
      apply(await api.get(`api/documents/${id}`), false);
      setToastTimed('Submitted for review');
    } catch (e) { setErr(e.message); }
  }

  async function review(decision) {
    const comment = decision === 'rejected'
      ? (window.prompt('Reason for rejection (sent to the author):') ?? '') : '';
    if (decision === 'rejected' && comment === null) return;
    try {
      await api.post(`api/documents/${id}/review`, { decision, comment });
      apply(await api.get(`api/documents/${id}`), false);
      setToastTimed(decision === 'approved' ? 'Revision approved' : 'Revision rejected');
    } catch (e) { setErr(e.message); }
  }

  async function addComment(section, row, body) {
    try {
      await api.post(`api/documents/${id}/comments`, { section_key: section, row_index: row, body });
      apply(await api.get(`api/documents/${id}`), false);
      setCommentDraft(null);
    } catch (e) { setErr(e.message); }
  }

  async function replyComment(parent, body) {
    try {
      await api.post(`api/documents/${id}/comments`, { parent_id: parent.id, body });
      apply(await api.get(`api/documents/${id}`), false);
    } catch (e) { setErr(e.message); }
  }

  async function resolveComment(c) {
    try {
      await api.post(`api/documents/${id}/comments/${c.id}/resolve`);
      apply(await api.get(`api/documents/${id}`), false);
    } catch (e) { setErr(e.message); }
  }

  async function openVersion(n) {
    setViewVersion(n);
    setOldContent(null);
    if (n !== null) {
      const v = await api.get(`api/documents/${id}/versions/${n}`);
      setOldContent(v.content || {});
    }
  }

  /* ---------- derived ---------- */
  if (err && !doc) return <div className="page"><p style={{ color: 'var(--bad)' }}>{err}</p></div>;
  if (!doc) return <div className="page" style={{ textAlign: 'center', paddingTop: 80 }}><span className="spin dark" /></div>;

  const status = doc.latest_status;
  const readOnly = viewVersion !== null || status === 'submitted' || !doc.can_edit;
  const shown = viewVersion !== null ? (oldContent || {}) : content;
  const openThreads = doc.comments.filter(c => !c.parent_id && c.status === 'open');
  const shownThreads = doc.comments.filter(c => !c.parent_id && (showResolved || c.status === 'open'));
  const statusCls = status === 'superseded' ? 'approved' : (status || 'empty');

  return (
    <div className="page" style={{ maxWidth: 1340 }}>
      <div className="page-head" style={{ alignItems: 'center' }}>
        <div className="crumb small">
          <a href="#/">Projects</a> / <a href={`#/projects/${doc.project_id}`}>{doc.project_name}</a> / <b>{doc.name}</b>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span className={`savebar ${saving || dirty ? 'saving' : ''}`}>
            <span className="dot" />
            {saving ? 'saving…' : dirty ? 'editing…' : status ? `rev ${doc.latest_version_number} saved` : 'no content yet'}
          </span>
          <a className="btn sm" href={`api/documents/${id}/export.docx`} title="Generate the Word deliverable from this document's content">⤓ .docx</a>
          <a className="btn sm" href={`api/documents/${id}/export.xlsx`} title="Generate the Excel deliverable from this document's content">⤓ .xlsx</a>
          <select className="input" style={{ width: 'auto', padding: '5px 8px', fontSize: 12.5 }}
                  value={viewVersion ?? ''}
                  onChange={e => openVersion(e.target.value === '' ? null : Number(e.target.value))}>
            <option value="">Current{status ? ` (rev ${doc.latest_version_number} ${status})` : ''}</option>
            {doc.versions.filter(v => v.version_number !== doc.latest_version_number).map(v => (
              <option key={v.version_number} value={v.version_number}>
                rev {v.version_number} — {v.status}
              </option>
            ))}
          </select>
          {doc.can_edit && status === 'draft' && viewVersion === null && (
            <button className="btn primary" onClick={submit}>Submit for review</button>
          )}
          {doc.can_review && status === 'submitted' && (
            <>
              <button className="btn danger" onClick={() => review('rejected')}>Reject</button>
              <button className="btn primary" onClick={() => review('approved')}>Approve</button>
            </>
          )}
        </div>
      </div>

      {doc.stale && (
        <div className="card" style={{ padding: '10px 16px', marginBottom: 14, background: 'var(--warn-bg)', borderColor: '#fde68a' }}>
          <b style={{ color: 'var(--warn)', fontSize: 13 }}>Upstream changed since this document was approved.</b>
          <div className="small" style={{ color: 'var(--warn)' }}>
            {doc.stale_reasons.map((r, i) => <div key={i}>· {r}</div>)}
          </div>
        </div>
      )}
      {status === 'rejected' && doc.versions[0]?.review_comment && (
        <div className="card" style={{ padding: '10px 16px', marginBottom: 14, background: 'var(--bad-bg)', borderColor: '#fecaca' }}>
          <b style={{ color: 'var(--bad)', fontSize: 13 }}>Rejected by {doc.versions[0].reviewed_by}:</b>
          <span className="small" style={{ color: 'var(--bad)', marginLeft: 6 }}>{doc.versions[0].review_comment}</span>
        </div>
      )}
      {err && <p style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</p>}

      <div className="editor-wrap">
        <div className="sheet">
          <div className="sheet-head">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="doc-no">{doc.project_name.toUpperCase().replace(/\s+/g, '-')}-{doc.node_key.toUpperCase()}-{String(doc.latest_version_number || 0).padStart(3, '0')}</span>
              <span className={`pill ${statusCls}`}>{status === 'superseded' ? 'approved' : status || 'not started'}</span>
            </div>
            <h1>{doc.name}</h1>
            <div className="soft small">{doc.description}</div>
            <div className="muted small" style={{ marginTop: 6 }}>
              Author: {doc.author_email || <i>anyone ({doc.author_role || 'unassigned'})</i>} ·
              Reviewer: {doc.reviewer_email || <i>anyone ({doc.reviewer_role || 'unassigned'})</i>}
              {doc.upstream.length > 0 && <> · Sources: {doc.upstream.map((u, i) => (
                <span key={u.document_id}>
                  {i > 0 && ', '}
                  <a href={`#/documents/${u.document_id}`}>{u.name}</a>
                  {u.approved_version ? <span className="mono"> r{u.approved_version}</span> : ' (no approved rev)'}
                </span>))}</>}
            </div>
          </div>

          {viewVersion !== null && (
            <div style={{ background: 'var(--accent-soft)', borderRadius: 8, padding: '8px 12px', margin: '12px 0', fontSize: 13, color: 'var(--accent)' }}>
              Viewing rev {viewVersion} (read-only). <a onClick={() => openVersion(null)} style={{ cursor: 'pointer' }}>Back to current</a>
            </div>
          )}

          {/* how this document is produced (defined on the workflow template) */}
          {doc.skill && <SkillPanel skill={doc.skill} />}

          {/* the real files behind this document; the ★ deliverable IS the
              document when it can't be structured (e.g. an AutoCAD P&ID) */}
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, padding: '12px 0 4px' }}>
            {attachments.map(a => (
              <span key={a.id} className="attach-chip"
                    style={a.kind === 'deliverable'
                      ? { borderColor: 'var(--accent-mid)', background: 'var(--accent-soft)' } : undefined}>
                <a onClick={() => setPreviewId(previewId === a.id ? null : a.id)}
                   style={{ cursor: 'pointer' }}
                   title={previewId === a.id ? 'Hide preview' : 'Show preview'}>
                  {a.content_type === 'application/pdf' ? '📄' : '📎'} {a.filename}
                </a>
                {a.kind === 'deliverable' && <span className="doc-no" style={{ color: 'var(--accent)' }}>deliverable</span>}
                <a onClick={() => setAttachmentKind(a, a.kind === 'deliverable' ? 'reference' : 'deliverable')}
                   style={{ cursor: 'pointer' }}
                   title={a.kind === 'deliverable'
                     ? 'Unmark — back to a simple reference file'
                     : 'Mark as the deliverable: this file IS the document (e.g. an uploaded P&ID)'}>
                  {a.kind === 'deliverable' ? '★' : '☆'}
                </a>
                <a href={`api/documents/${id}/attachments/${a.id}`} download={a.filename} title="Download">⤓</a>
              </span>
            ))}
            <label className="btn ghost sm" style={{ cursor: 'pointer' }}>
              + Attach file
              <input type="file" style={{ display: 'none' }}
                     onChange={e => { if (e.target.files?.[0]) { uploadFile(e.target.files[0]); e.target.value = ''; } }} />
            </label>
          </div>
          {previewId && attachments.some(a => a.id === previewId) && (
            <iframe title="attachment preview"
                    src={`api/documents/${id}/attachments/${previewId}`}
                    style={{ width: '100%', height: 560, border: '1px solid var(--line)', borderRadius: 8, margin: '4px 0 8px', background: '#fff' }} />
          )}

          {doc.content_schema.sections.map(s => (
            <SectionBlock key={s.key} section={s}
              value={shown[s.key]}
              readOnly={readOnly}
              flash={flashKeys.has(s.key)}
              hl={highlight?.section === s.key ? highlight : null}
              openComments={openThreads.filter(c => c.section_key === s.key)}
              onChange={v => edit({ ...content, [s.key]: v })}
              onComment={(row) => setCommentDraft({ section: s.key, row })}
            />
          ))}
        </div>

        <aside className="rail">
          <div className="rail-title" style={{ cursor: 'pointer' }} onClick={() => setRailComments(v => !v)}>
            <span>{railComments ? '▾' : '▸'} Comments {openThreads.length > 0 && `(${openThreads.length} open)`}</span>
            {railComments && (
              <button className="btn ghost sm" onClick={e => { e.stopPropagation(); setShowResolved(v => !v); }}>
                {showResolved ? 'hide resolved' : 'show resolved'}
              </button>
            )}
          </div>
          {railComments && shownThreads.length === 0 && (
            <div className="muted small" style={{ padding: '6px 4px' }}>
              No comments. Hover a section title and click 💬 to start a thread.
            </div>
          )}
          {railComments && shownThreads.map(c => (
            <Thread key={c.id} root={c}
                    replies={doc.comments.filter(r => r.parent_id === c.id)}
                    sections={doc.content_schema.sections}
                    onReply={body => replyComment(c, body)}
                    onResolve={() => resolveComment(c)}
                    onHover={on => setHighlight(on ? { section: c.section_key, row: c.row_index } : null)}
                    onJump={() => jumpTo(c.section_key, c.row_index)} />
          ))}
          <div className="rail-title" style={{ cursor: 'pointer', marginTop: 12 }} onClick={() => setRailActivity(v => !v)}>
            <span>{railActivity ? '▾' : '▸'} Activity</span>
          </div>
          {railActivity && <ActivityFeed id={id} stamp={doc.latest_updated_at} />}
        </aside>
      </div>

      {commentDraft && (
        <CommentModal target={commentDraft}
                      sections={doc.content_schema.sections}
                      onClose={() => setCommentDraft(null)}
                      onSubmit={body => addComment(commentDraft.section, commentDraft.row, body)} />
      )}
      {toast && <div className="toast"><span style={{ fontSize: 15 }}>✳️</span>{toast}</div>}
    </div>
  );
}

/* ================= sections ================= */

function SectionBlock({ section, value, readOnly, flash, hl, openComments, onChange, onComment }) {
  const sectionHl = hl && (hl.row === null || hl.row === undefined);
  return (
    <div id={`sec-${section.key}`}
         className={`section ${flash ? 'flash' : ''} ${sectionHl ? 'anchor-hl' : ''}`}>
      <div className="section-head">
        <h3>{section.title}</h3>
        {openComments.length > 0 && (
          <span className="comment-dot" style={{ display: 'inline-flex' }}>{openComments.length}</span>
        )}
        <span className="spacer" />
        <button className="icon-btn comment-btn" title="Comment on this section" onClick={() => onComment(null)}>💬</button>
      </div>
      {section.type === 'text'
        ? <ProseArea value={value || ''} readOnly={readOnly} onChange={onChange} />
        : <>
            <FlowDiagram section={section} rows={value || []} />
            <GridTable section={section} rows={value || []} readOnly={readOnly}
                       hlRow={hl && hl.row !== null && hl.row !== undefined ? hl.row : -1}
                       onChange={onChange} onCommentRow={onComment} />
          </>}
    </div>
  );
}

/* how this document is produced — the skill defined on the template node:
   which upstream documents to pull from, what to take from each. Read-only
   here; edited in the template editor (or by Claude via MCP). */
function SkillPanel({ skill }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ margin: '10px 0 2px' }}>
      <button className="btn ghost sm" onClick={() => setOpen(v => !v)}>
        {open ? '\u25be' : '\u25b8'} Skill <span className="muted">&mdash; how this document is produced</span>
      </button>
      {open && (
        <div style={{ border: '1px dashed var(--line-strong)', borderRadius: 9, marginTop: 6,
                      padding: '10px 14px', background: 'var(--bg)', whiteSpace: 'pre-wrap',
                      font: '400 12.5px var(--font-mono)', color: 'var(--ink-soft)' }}>
          {skill}
        </div>
      )}
    </div>
  );
}

function ProseArea({ value, readOnly, onChange }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (el) { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; }
  }, [value]);
  return (
    <textarea ref={ref} className="prose-edit" value={value} readOnly={readOnly}
              placeholder={readOnly ? '—' : 'Write here…'}
              onChange={e => onChange(e.target.value)} rows={1} />
  );
}

function GridTable({ section, rows, readOnly, hlRow = -1, onChange, onCommentRow }) {
  const cols = section.columns || [];

  function setCell(ri, key, v) {
    onChange(rows.map((r, i) => (i === ri ? { ...r, [key]: v } : r)));
  }
  function addRow() {
    onChange([...rows, Object.fromEntries(cols.map(c => [c.key, '']))]);
  }
  function delRow(ri) {
    onChange(rows.filter((_, i) => i !== ri));
  }
  function moveRow(ri, dir) {
    const j = ri + dir;
    if (j < 0 || j >= rows.length) return;
    const next = [...rows];
    [next[ri], next[j]] = [next[j], next[ri]];
    onChange(next);
  }

  return (
    <>
      <table className="grid-table">
        <thead>
          <tr>{cols.map(c => <th key={c.key}>{c.label}</th>)}<th style={{ width: 76 }} /></tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={cols.length + 1} style={{ padding: '10px 8px' }} className="muted small">
              {readOnly ? 'No rows.' : 'No rows yet.'}
            </td></tr>
          )}
          {rows.map((r, ri) => (
            <tr key={ri} id={`sec-${section.key}-row-${ri}`}
                className={ri === hlRow ? 'row-hl' : ''}>
              {cols.map(c => (
                <td key={c.key} className={c.type === 'number' ? 'num' : ''}>
                  <input value={String(r[c.key] ?? '')} readOnly={readOnly}
                         onChange={e => setCell(ri, c.key, e.target.value)} />
                </td>
              ))}
              <td style={{ borderBottom: '1px solid var(--line)' }}>
                <span className="row-tools">
                  <button className="icon-btn" title="Comment on this row" onClick={() => onCommentRow(ri)}>💬</button>
                  {!readOnly && <>
                    <button className="icon-btn" title="Move up" onClick={() => moveRow(ri, -1)}>↑</button>
                    <button className="icon-btn" title="Move down" onClick={() => moveRow(ri, 1)}>↓</button>
                    <button className="icon-btn" title="Delete row" onClick={() => delRow(ri)}>✕</button>
                  </>}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!readOnly && <button className="add-row" onClick={addRow}>+ Add row</button>}
    </>
  );
}

/* ================= comments ================= */

function Thread({ root, replies, sections, onReply, onResolve, onHover, onJump }) {
  const [reply, setReply] = useState('');
  const title = sections.find(s => s.key === root.section_key)?.title || root.section_key;
  return (
    <div className={`thread ${root.status === 'resolved' ? 'resolved' : ''}`}
         onMouseEnter={() => onHover?.(true)} onMouseLeave={() => onHover?.(false)}>
      <span className="anchor" title="Go to this section" onClick={onJump}>
        {title}{root.row_index !== null ? ` · row ${root.row_index + 1}` : ''} ↗
      </span>
      <Msg c={root} />
      {replies.map(r => <Msg key={r.id} c={r} />)}
      {root.status === 'open' ? (
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          <input className="input" style={{ fontSize: 12.5, padding: '5px 9px' }} placeholder="Reply…"
                 value={reply} onChange={e => setReply(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter' && reply.trim()) { onReply(reply.trim()); setReply(''); } }} />
          <button className="btn sm" onClick={onResolve} title="Mark resolved">✓</button>
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 11, marginTop: 5 }}>
          Resolved by {root.resolved_by?.split('@')[0]} {timeAgo(root.resolved_at)}
        </div>
      )}
    </div>
  );
}

function Msg({ c }) {
  const isClaude = c.author_kind === 'assistant';
  return (
    <div className="msg">
      <span className={`avatar ${isClaude ? 'assistant' : ''}`}>
        {isClaude ? '✳' : initials(c.author_email)}
      </span>
      <div style={{ minWidth: 0 }}>
        <span className="who">{isClaude ? 'Claude' : c.author_email.split('@')[0]}</span>
        <span className="when">{timeAgo(c.created_at)}</span>
        <div className="body">{c.body}</div>
      </div>
    </div>
  );
}

function CommentModal({ target, sections, onClose, onSubmit }) {
  const [body, setBody] = useState('');
  const title = sections.find(s => s.key === target.section)?.title || target.section;
  return (
    <div className="overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <h2>Comment — {title}{target.row !== null ? ` · row ${target.row + 1}` : ''}</h2>
        <textarea className="input" rows={4} autoFocus value={body}
                  placeholder="Flag missing info, question an assumption…"
                  onChange={e => setBody(e.target.value)} />
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 14 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!body.trim()} onClick={() => onSubmit(body.trim())}>Comment</button>
        </div>
      </div>
    </div>
  );
}

/* ================= activity ================= */

function ActivityFeed({ id, stamp }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    api.get(`api/documents/${id}/activity?limit=12`).then(setItems).catch(() => {});
  }, [id, stamp]);
  if (!items.length) return null;
  const verb = {
    draft_edit: 'edited', submit: 'submitted', review: 'reviewed',
    comment: 'commented', resolve_comment: 'resolved a comment',
  };
  return (
    <>
      <div className="rail-title" style={{ marginTop: 14 }}><span>Activity</span></div>
      <div className="card" style={{ padding: '8px 12px', boxShadow: 'none' }}>
        {items.map((a, i) => (
          <div key={i} className="small" style={{ padding: '4px 0', borderBottom: i < items.length - 1 ? '1px solid var(--line)' : 'none' }}>
            <span style={{ color: a.actor_kind === 'assistant' ? 'var(--claude)' : 'var(--ink-soft)', fontWeight: 600 }}>
              {a.actor_kind === 'assistant' ? '✳ Claude' : a.actor_email.split('@')[0]}
            </span>
            <span className="soft"> {verb[a.action] || a.action}</span>
            {a.payload?.section ? <span className="mono muted"> {String(a.payload.section)}</span> : null}
            <span className="muted"> · {timeAgo(a.created_at)}</span>
          </div>
        ))}
      </div>
    </>
  );
}
