import { useState, useEffect, useRef, useMemo, useContext } from 'react';
import { motion } from 'framer-motion';
import { ZoomIn, ZoomOut, Maximize2, Search, X } from 'lucide-react';
import type { Note, KnowledgeNode } from '@/types';
import { generateKnowledgeGraph } from '@/lib/mockData';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface KnowledgeGraphViewProps {
  notes?: Note[];
}

const TT = {
  inkBlack:  '#0A0A0A',
  inkDeep:   '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMid:    '#3A3A3A',
  inkMuted:  '#5A5A5A',
  inkSubtle: '#888888',
  snow:      '#F5F5F5',
  yolk:      '#F5E642',
  yolkBright:'#FFF176',
  error:     '#FF4545',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

function IconBtn({ onClick, children, title }: { onClick: () => void; children: React.ReactNode; title?: string }) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 34, height: 34,
        background: TT.inkRaised,
        border: `1px solid ${TT.inkBorder}`,
        borderRadius: 3,
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: TT.inkMuted,
        transition: 'all 0.15s',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.color = TT.yolk;
        (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
        (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
      }}
    >
      {children}
    </button>
  );
}

const categoryColors: Record<string, string> = {
  note:     TT.inkSubtle,
  ai:       TT.yolk,
  ml:       TT.yolk,
  database: '#60A5FA',
  business: '#FB923C',
  research: '#A78BFA',
  default:  '#5A5A5A',
};

function nodeColor(node: KnowledgeNode): string {
  if (node.color && node.color !== '#ffffff') return node.color;
  return categoryColors[node.category?.toLowerCase() ?? 'default'] ?? categoryColors.default;
}

/** Safely normalise a node so noteIds is always a string[] */
function normaliseNode(node: any): KnowledgeNode {
  return {
    ...node,
    noteIds: Array.isArray(node.noteIds)
      ? node.noteIds
      : Array.isArray(node.note_ids)
        ? node.note_ids
        : [],
  };
}

// FIX 1: Default prop value so notes is never undefined
export function KnowledgeGraphView({ notes = [] }: KnowledgeGraphViewProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [selectedNode, setSelectedNode] = useState<KnowledgeNode | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;

  const [graphData, setGraphData] = useState<{ nodes: KnowledgeNode[]; links: any[] }>({
    nodes: [],
    links: [],
  });

  useEffect(() => {
    if (!workspaceId) return;

    const loadGraph = async () => {
      try {
        setIsLoading(true);
        const response: any = await api.getKnowledgeGraph(workspaceId);
        const rawNodes: any[] = response.nodes || [];
        const transformed = {
          nodes: rawNodes.map(normaliseNode),
          links: response.edges || response.links || [],
        };
        setGraphData(transformed);
      } catch (err) {
        console.error('Failed to load knowledge graph:', err);
        // FIX 2: notes is guaranteed non-undefined via default prop, but
        // guard here anyway for extra safety
        const fallback = generateKnowledgeGraph(notes ?? []);
        setGraphData({
          nodes: (fallback.nodes || []).map(normaliseNode),
          links: fallback.links || [],
        });
      } finally {
        setIsLoading(false);
      }
    };

    loadGraph();
  // FIX 3: Remove `notes` from deps — it caused the effect to re-fire on
  // every parent render that passed a new array reference, and the fallback
  // path would re-run even after a successful load. If notes genuinely need
  // to trigger a reload, memoize the array at the call site instead.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const nodes = graphData?.nodes ?? [];
  const links = graphData?.links ?? [];

  const filteredNodes = useMemo(() => {
    if (!searchQuery) return nodes;
    return nodes.filter((n) => n.name?.toLowerCase().includes(searchQuery.toLowerCase()));
  }, [nodes, searchQuery]);

  const filteredLinks = useMemo(() => {
    const ids = new Set(filteredNodes.map((n) => n.id));
    return links.filter((l) => ids.has(l.source as string) && ids.has(l.target as string));
  }, [links, filteredNodes]);

  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());

  useEffect(() => {
    const W = 800, H = 600;
    const pos = new Map<string, { x: number; y: number }>();
    filteredNodes.forEach((node, i) => {
      const angle = (i / Math.max(filteredNodes.length, 1)) * 2 * Math.PI;
      const r = Math.min(W, H) * 0.35;
      pos.set(node.id, { x: W / 2 + Math.cos(angle) * r, y: H / 2 + Math.sin(angle) * r });
    });
    for (let iter = 0; iter < 50; iter++) {
      filteredNodes.forEach((n1, i) => {
        const p1 = pos.get(n1.id)!;
        let fx = 0, fy = 0;
        filteredNodes.forEach((n2, j) => {
          if (i === j) return;
          const p2 = pos.get(n2.id)!;
          const dx = p1.x - p2.x, dy = p1.y - p2.y;
          const d = Math.sqrt(dx * dx + dy * dy) || 1;
          const f = 2000 / (d * d);
          fx += (dx / d) * f; fy += (dy / d) * f;
        });
        p1.x += fx * 0.1; p1.y += fy * 0.1;
      });
      filteredLinks.forEach((link) => {
        const sp = pos.get(link.source as string), tp = pos.get(link.target as string);
        if (!sp || !tp) return;
        const dx = tp.x - sp.x, dy = tp.y - sp.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - 100) * 0.01;
        sp.x += (dx / d) * f; sp.y += (dy / d) * f;
        tp.x -= (dx / d) * f; tp.y -= (dy / d) * f;
      });
      filteredNodes.forEach((n) => {
        const p = pos.get(n.id)!;
        p.x += (W / 2 - p.x) * 0.05;
        p.y += (H / 2 - p.y) * 0.05;
      });
    }
    setPositions(pos);
  }, [filteredNodes, filteredLinks]);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.target === svgRef.current) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    }
  };
  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
  };
  const handleMouseUp = () => setIsDragging(false);

  const connectedNotes = useMemo(() => {
    if (!selectedNode) return [];
    const ids = selectedNode.noteIds ?? [];
    return notes.filter((n) => ids.includes(n.id));
  }, [selectedNode, notes]);

  const stats = [
    { label: 'Nodes',       value: nodes?.length ?? 0 },
    { label: 'Connections', value: links?.length ?? 0 },
    { label: 'Notes',       value: notes?.length ?? 0 },
    { label: 'Tags',        value: new Set((notes ?? []).flatMap((n) => n.tags ?? [])).size },
  ];

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>

      {/* ── Header ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 28, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Visualization</span>
          </div>
          <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 44, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase' }}>
            <span style={{ color: TT.yolk }}>K</span>NOWLEDGE GRAPH
          </h1>
          <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
        </div>

        {/* Search */}
        <div style={{ position: 'relative' }}>
          <Search size={12} color={TT.inkMuted} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)' }} />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Find concepts..."
            style={{
              height: 36, width: 200,
              background: TT.inkRaised,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 3,
              color: TT.snow,
              fontFamily: TT.fontMono,
              fontSize: 11, letterSpacing: '0.02em',
              paddingLeft: 30, paddingRight: 12,
              outline: 'none',
              boxSizing: 'border-box',
            }}
            onFocus={(e) => { (e.target as HTMLInputElement).style.borderColor = TT.yolk; }}
            onBlur={(e) => { (e.target as HTMLInputElement).style.borderColor = TT.inkBorder; }}
          />
        </div>
      </div>

      {/* ── Stats row ───────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 20 }}>
        {stats.map(({ label, value }) => (
          <div
            key={label}
            style={{
              background: TT.inkDeep,
              border: `1px solid ${TT.inkBorder}`,
              borderLeft: `3px solid ${TT.yolk}`,
              borderRadius: 3,
              padding: '12px 16px',
            }}
          >
            <div style={{ fontFamily: TT.fontDisplay, fontSize: 32, color: TT.snow, letterSpacing: '0.02em', lineHeight: 1 }}>{value}</div>
            <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginTop: 3 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* ── Graph container ─────────────────────────────────────── */}
      <div
        style={{
          position: 'relative',
          background: TT.inkDeep,
          border: `1px solid ${TT.inkBorder}`,
          borderRadius: 3,
          overflow: 'hidden',
          height: 560,
          backgroundImage: 'radial-gradient(circle, rgba(245,230,66,0.06) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      >
        {/* Loading overlay */}
        {isLoading && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(10,10,10,0.6)', zIndex: 20,
            fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted,
          }}>
            Loading graph...
          </div>
        )}

        {/* Controls — top right */}
        <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', flexDirection: 'column', gap: 4, zIndex: 10 }}>
          <IconBtn onClick={() => setZoom((z) => Math.min(z * 1.2, 3))} title="Zoom in"><ZoomIn size={14} /></IconBtn>
          <IconBtn onClick={() => setZoom((z) => Math.max(z / 1.2, 0.3))} title="Zoom out"><ZoomOut size={14} /></IconBtn>
          <IconBtn onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }} title="Reset"><Maximize2 size={14} /></IconBtn>
        </div>

        {/* Legend — bottom left */}
        <div
          style={{
            position: 'absolute', bottom: 12, left: 12,
            background: 'rgba(10,10,10,0.85)',
            border: `1px solid ${TT.inkBorder}`,
            borderLeft: `3px solid ${TT.yolk}`,
            borderRadius: 3,
            padding: '10px 14px',
            zIndex: 10,
          }}
        >
          <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Legend</div>
          {[
            { label: 'Note',     color: TT.inkSubtle },
            { label: 'AI / ML',  color: TT.yolk      },
            { label: 'Database', color: '#60A5FA'     },
            { label: 'Business', color: '#FB923C'     },
          ].map(({ label, color }) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: `0 0 5px ${color}60` }} />
              <span style={{ fontSize: 9.5, letterSpacing: '0.04em', color: TT.inkMuted }}>{label}</span>
            </div>
          ))}
        </div>

        {/* Zoom level indicator */}
        <div style={{ position: 'absolute', top: 12, left: 12, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.06em', color: TT.inkMid, textTransform: 'uppercase' }}>
          {Math.round(zoom * 100)}%
        </div>

        {/* SVG */}
        <svg
          ref={svgRef}
          style={{ width: '100%', height: '100%', cursor: isDragging ? 'grabbing' : 'grab' }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <defs>
            <filter id="glow">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>

          <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
            {/* Links */}
            {filteredLinks.map((link, i) => {
              const sp = positions.get(link.source as string);
              const tp = positions.get(link.target as string);
              if (!sp || !tp) return null;
              const highlighted = hoveredNode && (link.source === hoveredNode || link.target === hoveredNode);
              return (
                <line
                  key={i}
                  x1={sp.x} y1={sp.y} x2={tp.x} y2={tp.y}
                  stroke={highlighted ? TT.yolk : TT.inkMid}
                  strokeWidth={highlighted ? 1.5 : 0.8}
                  opacity={hoveredNode && !highlighted ? 0.1 : highlighted ? 0.7 : 0.4}
                />
              );
            })}

            {/* Nodes */}
            {filteredNodes.map((node) => {
              const pos = positions.get(node.id);
              if (!pos) return null;
              const hovered  = hoveredNode === node.id;
              const selected = selectedNode?.id === node.id;
              const r = (node.val || 10) + (hovered ? 4 : 0);
              const color = selected || hovered ? TT.yolk : nodeColor(node);

              return (
                <g
                  key={node.id}
                  transform={`translate(${pos.x},${pos.y})`}
                  onMouseEnter={() => setHoveredNode(node.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                  onClick={() => setSelectedNode(node)}
                  style={{ cursor: 'pointer' }}
                  filter={hovered ? 'url(#glow)' : undefined}
                >
                  <circle
                    r={r}
                    fill={color}
                    opacity={hoveredNode && !hovered ? 0.2 : 1}
                    stroke={selected ? TT.snow : 'none'}
                    strokeWidth={selected ? 2 : 0}
                  />
                  {node.category !== 'note' && (
                    <text
                      dy={r + 13}
                      textAnchor="middle"
                      fill={hovered ? TT.yolk : TT.inkMuted}
                      fontSize={9}
                      fontFamily={TT.fontMono}
                      letterSpacing="0.03em"
                      style={{ pointerEvents: 'none', textTransform: 'uppercase' }}
                    >
                      {node.name}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* ── Selected node panel ──────────────────────────────────── */}
      {selectedNode && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            marginTop: 12,
            background: TT.inkDeep,
            border: `1px solid ${TT.inkBorder}`,
            borderLeft: `3px solid ${TT.yolk}`,
            borderRadius: 3,
            padding: '18px 20px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: nodeColor(selectedNode), boxShadow: `0 0 8px ${nodeColor(selectedNode)}80` }} />
              <span style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow }}>
                {selectedNode.name?.toUpperCase()}
              </span>
              <span style={{
                fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase',
                padding: '2px 7px',
                background: 'rgba(245,230,66,0.08)', color: TT.yolk,
                border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2,
              }}>
                {(selectedNode.noteIds ?? []).length} notes
              </span>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              style={{ background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2, cursor: 'pointer', padding: '4px 6px', color: TT.inkMuted, transition: 'all 0.15s' }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.error; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
            >
              <X size={12} />
            </button>
          </div>

          <p style={{ fontSize: 10.5, letterSpacing: '0.04em', color: TT.inkMuted, marginBottom: 14 }}>
            {selectedNode.category === 'note'
              ? 'A note in your knowledge base'
              : `Category: ${selectedNode.category}`}
          </p>

          {connectedNotes.length > 0 && (
            <>
              <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>
                Connected Notes
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
                {connectedNotes.slice(0, 4).map((note) => (
                  <div
                    key={note.id}
                    style={{
                      background: TT.inkRaised,
                      border: `1px solid ${TT.inkBorder}`,
                      borderRadius: 3,
                      padding: '10px 12px',
                    }}
                  >
                    <p style={{ fontFamily: TT.fontMono, fontSize: 11.5, color: TT.snow, marginBottom: 4, letterSpacing: '0.02em', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
                      {note.title}
                    </p>
                    <p style={{ fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.03em' }}>
                      {(note.tags ?? []).slice(0, 3).join(' · ')}
                    </p>
                  </div>
                ))}
              </div>
            </>
          )}
        </motion.div>
      )}
    </div>
  );
}