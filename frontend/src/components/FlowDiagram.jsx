import React, { useEffect, useMemo, useRef, useState } from 'react';

/* Living process diagram: any table section with from/to columns renders as a
   flow graph straight from its rows - edit the table, the drawing follows.
   Built for PFD stream registers but generic (cable lists, I/O routing…).

   Deliberately uses literal colors (not CSS vars) so the exported SVG is
   self-contained. */

const FROM_KEYS = ['from_', 'from', 'source', 'src'];
const TO_KEYS = ['to', 'to_', 'dest', 'target'];
const ID_KEYS = ['stream', 'tag', 'line', 'cable', 'signal', 'id', 'name', 'item'];

const C = {
  paper: '#ffffff', ink: '#1c2433', inkSoft: '#4b5768', inkFaint: '#8a94a6',
  line: '#c3ccd9', edge: '#6366f1', edgeSoft: '#a5b4fc', box: '#fcfdff',
};

/* ---- equipment typing: tag prefixes + service words → symbol + color ---- */
const TYPES = [
  { t: 'pump', color: '#0369a1', re: /^P[-\s]?\d|PUMP/ },
  { t: 'compressor', color: '#7c3aed', re: /^K[-\s]?\d|COMPRESS|BLOWER|\bFAN\b/ },
  { t: 'exchanger', color: '#b45309', re: /^(E|HX|HE)[-\s]?\d|EXCHANGER|COOLER|HEATER|CONDENSER|REBOILER|CHILLER/ },
  { t: 'column', color: '#0f766e', re: /COLUMN|TOWER|SCRUBBER|ABSORBER|STRIPPER/ },
  { t: 'reactor', color: '#be123c', re: /^R[-\s]?\d|REACTOR|STACK|ELECTROLY|CELL/ },
  { t: 'tank', color: '#4d7c0f', re: /^TK[-\s]?\d|TANK|STORAGE/ },
  { t: 'vessel', color: '#155e75', re: /^[VDS][-\s]?\d|VESSEL|DRUM|SEPARATOR|KNOCK|FLASH|DEGAS/ },
  { t: 'filter', color: '#a16207', re: /^F[-\s]?\d|FILTER/ },
  { t: 'terminal', color: '#6b7280', re: /BATTERY|\bBL\b|VENT|FLARE|EXPORT|IMPORT|UTILIT|ATMOSPHERE|\bATM\b|DRAIN|SEWER/ },
];

function classify(name) {
  const up = String(name).toUpperCase();
  for (const x of TYPES) if (x.re.test(up)) return x;
  return { t: 'unit', color: '#64748b' };
}

/* small ISO-flavored glyph, drawn in a 20×20 box */
function Glyph({ type, color }) {
  const s = { fill: 'none', stroke: color, strokeWidth: 1.4 };
  switch (type) {
    case 'pump': return <g style={s}><circle cx={10} cy={10} r={7.2} /><path d="M6.5,5.4 L15.5,10 L6.5,14.6" /></g>;
    case 'compressor': return <g style={s}><path d="M4,5 L16,7.5 L16,12.5 L4,15 Z" /></g>;
    case 'exchanger': return <g style={s}><circle cx={10} cy={10} r={7.2} /><path d="M3,10 h3 l2.6,-3.4 l3,6.8 l2.6,-3.4 h2.8" /></g>;
    case 'column': return <g style={s}><rect x={6.4} y={2.5} width={7.2} height={15} rx={3.4} /><path d="M6.4,7.5 h4.4 M9.2,11 h4.4 M6.4,14.5 h4.4" /></g>;
    case 'reactor': return <g style={s}><rect x={5} y={3.5} width={10} height={13} rx={2.4} /><path d="M5,13 c2,-2.4 4,1.6 5,-1 c1,-2.6 3,1.4 5,-1" /></g>;
    case 'tank': return <g style={s}><path d="M4.5,7 a5.5,2.6 0 0 1 11,0 v7 a5.5,2.6 0 0 1 -11,0 Z" /></g>;
    case 'vessel': return <g style={s}><rect x={6.4} y={3} width={7.2} height={14} rx={3.5} /></g>;
    case 'filter': return <g style={s}><rect x={4.5} y={5} width={11} height={10} rx={1.6} /><path d="M6,13 L14,7 M6,9.5 L11,5.6 M9,14 L14,10.4" /></g>;
    case 'terminal': return <g style={s}><path d="M5.5,5.5 h9 l-2.4,4.5 l2.4,4.5 h-9 Z" /></g>;
    default: return <g style={s}><rect x={4.8} y={5.4} width={10.4} height={9.2} rx={1.8} /></g>;
  }
}

/* ---- layout: layered longest-path, cycle-tolerant, barycenter ordering ---- */
function layout(links) {
  const names = [...new Set(links.flatMap(l => [l.from, l.to]))];
  const indeg = new Map(names.map(n => [n, 0]));
  const out = new Map(), inn = new Map();
  for (const l of links) {
    indeg.set(l.to, (indeg.get(l.to) || 0) + 1);
    out.set(l.from, [...(out.get(l.from) || []), l.to]);
    inn.set(l.to, [...(inn.get(l.to) || []), l.from]);
  }
  const layer = new Map();
  const q = names.filter(n => (indeg.get(n) || 0) === 0);
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
  // recycle loops never drain in Kahn: spread the rest by BFS from what IS layered
  if (!layer.size) layer.set(names[0], 0);
  const assigned = new Set(layer.keys());
  let frontier = [...assigned];
  while (frontier.length) {
    const next = [];
    for (const n of frontier) for (const m of out.get(n) || []) {
      if (!assigned.has(m)) { layer.set(m, (layer.get(n) || 0) + 1); assigned.add(m); next.push(m); }
    }
    frontier = next;
  }
  names.forEach(n => { if (!assigned.has(n)) layer.set(n, 0); });

  const cols = new Map();
  for (const n of names) {
    const l = layer.get(n);
    cols.set(l, [...(cols.get(l) || []), n]);
  }
  const ordered = [...cols.entries()].sort((a, b) => a[0] - b[0]);
  // barycenter pass: order each column by the mean row of its predecessors
  const rowOf = new Map();
  ordered.forEach(([, ns], ci) => {
    if (ci > 0) {
      ns.sort((a, b) => {
        const bary = n => {
          const ps = (inn.get(n) || []).filter(p => rowOf.has(p));
          return ps.length ? ps.reduce((s, p) => s + rowOf.get(p), 0) / ps.length : 99;
        };
        return bary(a) - bary(b);
      });
    }
    ns.forEach((n, i) => rowOf.set(n, i));
  });

  const W = 150, H = 40, GX = 96, GY = 26;
  let maxRows = 1;
  for (const [, ns] of ordered) maxRows = Math.max(maxRows, ns.length);
  const totalH = maxRows * (H + GY) - GY;
  const pos = new Map();
  for (const [l, ns] of ordered) {
    const colH = ns.length * (H + GY) - GY;
    ns.forEach((n, i) => pos.set(n, { x: l * (W + GX), y: (totalH - colH) / 2 + i * (H + GY) }));
  }
  const width = (Math.max(...ordered.map(([l]) => l)) + 1) * (W + GX) - GX;
  return { names, pos, W, H, width, height: totalH };
}

export default function FlowDiagram({ section, rows }) {
  const cols = section.columns || [];
  const keys = cols.map(c => c.key);
  const fromKey = keys.find(k => FROM_KEYS.includes(k));
  const toKey = keys.find(k => TO_KEYS.includes(k));

  const links = useMemo(() => {
    if (!fromKey || !toKey) return [];
    const idKey = keys.find(k => ID_KEYS.includes(k) && k !== fromKey && k !== toKey)
      || keys.find(k => k !== fromKey && k !== toKey);
    return (rows || [])
      .map((r, i) => ({
        from: String(r[fromKey] ?? '').trim(), to: String(r[toKey] ?? '').trim(),
        label: idKey ? String(r[idKey] ?? '').trim() : '',
        detail: cols.map(c => `${c.label}: ${String(r[c.key] ?? '') || '-'}`).join('\n'),
        row: i,
      }))
      .filter(l => l.from && l.to && l.from !== l.to);
  }, [rows, fromKey, toKey]);

  const [open, setOpen] = useState(true);
  const [tall, setTall] = useState(false);
  const [view, setView] = useState(null); // {x, y, k}
  const [hoverNode, setHoverNode] = useState(null);
  const [hoverEdge, setHoverEdge] = useState(-1);
  const boxRef = useRef(null);
  const svgRef = useRef(null);
  const drag = useRef(null);

  const lay = useMemo(() => (links.length ? layout(links) : null), [links]);

  // adapt the box to the diagram: a flat 2-row train doesn't need 340px
  const boxH = tall ? 640
    : (lay ? Math.max(200, Math.min(360, Math.round(lay.height) + 110)) : 340);
  const fit = () => {
    if (!lay || !boxRef.current) return;
    const cw = boxRef.current.clientWidth - 24;
    const k = Math.min(cw / (lay.width + 40), (boxH - 30) / (lay.height + 40), 1.35);
    setView({ k, x: (cw + 24 - lay.width * k) / 2, y: (boxH - lay.height * k) / 2 });
  };
  useEffect(() => { fit(); }, [lay, open, tall]); // eslint-disable-line react-hooks/exhaustive-deps

  // native wheel listener: React's onWheel is passive, preventDefault would be ignored
  useEffect(() => {
    const el = svgRef.current;
    if (!el || !open) return;
    const onWheel = e => {
      e.preventDefault();
      setView(v => {
        if (!v) return v;
        const rect = el.getBoundingClientRect();
        const px = e.clientX - rect.left, py = e.clientY - rect.top;
        const k = Math.min(4, Math.max(0.15, v.k * (e.deltaY < 0 ? 1.12 : 0.89)));
        return { k, x: px - ((px - v.x) / v.k) * k, y: py - ((py - v.y) / v.k) * k };
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [open, lay]);

  if (!fromKey || !toKey || !links.length) return null;

  const { pos, W, H } = lay;
  const v = view || { x: 12, y: 12, k: 1 };

  // group parallel streams between the same pair so they fan out instead of stacking
  const pairCount = new Map(), pairSeen = new Map();
  for (const l of links) {
    const p = `${l.from}|${l.to}`;
    pairCount.set(p, (pairCount.get(p) || 0) + 1);
  }

  const neighbor = n => new Set(links.flatMap(l => (l.from === n || l.to === n ? [l.from, l.to] : [])));
  const hoodlight = hoverNode ? neighbor(hoverNode) : null;

  function edgePath(l, idx) {
    const a = pos.get(l.from), b = pos.get(l.to);
    const p = `${l.from}|${l.to}`;
    const n = pairCount.get(p), i = pairSeen.get(p) || 0;
    pairSeen.set(p, i + 1);
    const off = (i - (n - 1) / 2) * 16;
    const back = b.x < a.x; // recycle: route under the diagram
    if (back) {
      const dip = lay.height + 34 + Math.abs(off);
      const x1 = a.x + W / 2, y1 = a.y + H, x2 = b.x + W / 2, y2 = b.y + H;
      return { d: `M${x1},${y1} C${x1},${dip} ${x2},${dip} ${x2},${y2 + 2}`,
               lx: (x1 + x2) / 2, ly: dip - 5 };
    }
    const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2.5, y2 = b.y + H / 2;
    const mx = (x1 + x2) / 2;
    return { d: `M${x1},${y1} C${mx},${y1 + off} ${mx},${y2 + off} ${x2},${y2}`,
             lx: mx, ly: (y1 + y2) / 2 + off - 5 };
  }

  function downloadSvg() {
    const el = svgRef.current;
    if (!el) return;
    const clone = el.cloneNode(true);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('style', 'background:#ffffff');
    // export the whole diagram flat, whatever the current pan/zoom
    const g = clone.querySelector(':scope > g');
    if (g) g.setAttribute('transform', '');
    const exW = lay.width + 28, exH = lay.height + 110; // room for recycle loops below
    clone.setAttribute('viewBox', `-14 -14 ${exW} ${exH}`);
    clone.setAttribute('width', exW);
    clone.setAttribute('height', exH);
    const blob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n' + clone.outerHTML],
                          { type: 'image/svg+xml' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(section.title || section.key).replace(/\s+/g, '_')}_diagram.svg`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div style={{ margin: '2px 0 12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <button className="btn ghost sm" onClick={() => setOpen(o => !o)}>
          {open ? '▾' : '▸'} Diagram <span className="muted">({lay.names.length} items, {links.length} flows)</span>
        </button>
        {open && <>
          <span className="spacer" style={{ flex: 1 }} />
          <button className="btn ghost sm" title="Zoom out" onClick={() => setView(x => x && { ...x, k: Math.max(0.15, x.k * 0.85) })}>−</button>
          <button className="btn ghost sm" title="Zoom in" onClick={() => setView(x => x && { ...x, k: Math.min(4, x.k * 1.18) })}>+</button>
          <button className="btn ghost sm" title="Fit to view" onClick={fit}>⌖ fit</button>
          <button className="btn ghost sm" title={tall ? 'Reduce height' : 'Expand height'} onClick={() => setTall(t => !t)}>{tall ? '⤡' : '⤢'}</button>
          <button className="btn ghost sm" title="Download as SVG" onClick={downloadSvg}>⤓ svg</button>
        </>}
      </div>
      {open && (
        <div ref={boxRef}
             style={{ border: '1px solid #dbe1ea', borderRadius: 9, marginTop: 6,
                      background: C.box, overflow: 'hidden', position: 'relative' }}>
          <svg ref={svgRef} width="100%" height={boxH}
               style={{ display: 'block', cursor: drag.current ? 'grabbing' : 'grab', touchAction: 'none' }}
               onMouseDown={e => { drag.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y }; }}
               onMouseMove={e => {
                 if (!drag.current) return;
                 const d = drag.current;
                 setView({ k: v.k, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y });
               }}
               onMouseUp={() => { drag.current = null; }}
               onMouseLeave={() => { drag.current = null; }}
               onDoubleClick={fit}>
            <defs>
              <marker id="pfd-arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
                <path d="M0,0.6 L7,4 L0,7.4" fill="none" stroke={C.edge} strokeWidth="1.3" />
              </marker>
            </defs>
            <g transform={`translate(${v.x},${v.y}) scale(${v.k})`}>
              {links.map((l, i) => {
                const a = pos.get(l.from), b = pos.get(l.to);
                if (!a || !b) return null;
                const { d, lx, ly } = edgePath(l, i);
                const dim = hoodlight && !(l.from === hoverNode || l.to === hoverNode);
                const hot = hoverEdge === i;
                return (
                  <g key={i} opacity={dim ? 0.12 : 1}
                     onMouseEnter={() => setHoverEdge(i)} onMouseLeave={() => setHoverEdge(-1)}>
                    <path d={d} fill="none" stroke={hot ? '#3730a3' : C.edge}
                          strokeWidth={hot ? 2.1 : 1.3} opacity={hot ? 1 : 0.75}
                          markerEnd="url(#pfd-arr)" />
                    <path d={d} fill="none" stroke="transparent" strokeWidth="13" />
                    {l.label && (
                      <text x={lx} y={ly} textAnchor="middle"
                            style={{ font: `${hot ? 600 : 500} 9.5px 'IBM Plex Mono', monospace`,
                                     fill: hot ? '#3730a3' : C.inkFaint, paintOrder: 'stroke',
                                     stroke: C.box, strokeWidth: 3 }}>
                        {l.label.length > 18 && !hot ? l.label.slice(0, 17) + '…' : l.label}
                      </text>
                    )}
                    <title>{l.detail}</title>
                  </g>
                );
              })}
              {lay.names.map(n => {
                const pt = pos.get(n);
                const cls = classify(n);
                const dim = hoodlight && !hoodlight.has(n);
                return (
                  <g key={n} transform={`translate(${pt.x},${pt.y})`} opacity={dim ? 0.28 : 1}
                     onMouseEnter={() => setHoverNode(n)} onMouseLeave={() => setHoverNode(null)}
                     style={{ cursor: 'default' }}>
                    <rect width={W} height={H} rx={8}
                          fill={C.paper} stroke={hoverNode === n ? cls.color : C.line}
                          strokeWidth={hoverNode === n ? 1.7 : 1.1} />
                    <g transform="translate(8,10)"><Glyph type={cls.t} color={cls.color} /></g>
                    <text x={34} y={H / 2 + 1}
                          style={{ font: `500 10.5px 'IBM Plex Sans', sans-serif`, fill: C.ink }}>
                      {n.length > 19 ? n.slice(0, 18) + '…' : n}
                    </text>
                    <text x={34} y={H / 2 + 12}
                          style={{ font: `400 8px 'IBM Plex Mono', monospace`, fill: cls.color }}>
                      {cls.t !== 'unit' ? cls.t : ''}
                    </text>
                    <title>{n}</title>
                  </g>
                );
              })}
            </g>
          </svg>
          <span style={{ position: 'absolute', right: 10, bottom: 6, fontSize: 10.5,
                         color: C.inkFaint, pointerEvents: 'none' }}>
            scroll to zoom · drag to pan · double-click to fit
          </span>
        </div>
      )}
    </div>
  );
}
