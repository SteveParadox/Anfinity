import { useState, useEffect, useRef, useMemo, useContext } from 'react';
import { motion } from 'framer-motion';
import { ZoomIn, ZoomOut, Maximize2, Search, X } from 'lucide-react';
import type { Note, KnowledgeGraph, KnowledgeGraphFilters, KnowledgeGraphNode } from '@/types';
import { generateKnowledgeGraph } from '@/lib/mockData';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface KnowledgeGraphViewProps {
  notes?: Note[];
}

const TT = {
  inkBlack: '#0A0A0A',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMid: '#3A3A3A',
  inkMuted: '#5A5A5A',
  inkSubtle: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  yolkBright: '#FFF176',
  error: '#FF4545',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
};

const EMPTY_GRAPH: KnowledgeGraph = {
  nodes: [],
  edges: [],
  stats: {
    total_nodes: 0,
    total_edges: 0,
    node_types: {},
    edge_types: {},
  },
};

const categoryColors: Record<string, string> = {
  workspace: '#60A5FA',
  note: TT.inkSubtle,
  entity: TT.yolk,
  tag: '#FB923C',
  default: '#5A5A5A',
};

function IconBtn({ onClick, children, title }: { onClick: () => void; children: React.ReactNode; title?: string }) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 34,
        height: 34,
        background: TT.inkRaised,
        border: `1px solid ${TT.inkBorder}`,
        borderRadius: 3,
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
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

function getNodeNoteIds(node: KnowledgeGraphNode): string[] {
  return Array.isArray(node.metadata?.note_ids)
    ? node.metadata.note_ids.filter((noteId): noteId is string => typeof noteId === 'string')
    : [];
}

function getNodeColor(node: KnowledgeGraphNode): string {
  const displayColor = typeof node.metadata?.display_color === 'string' ? node.metadata.display_color : null;
  if (displayColor) {
    return displayColor;
  }
  return categoryColors[node.type] ?? categoryColors.default;
}

function getNodeRadius(node: KnowledgeGraphNode, hovered: boolean): number {
  const baseRadius = Math.max(8, Math.min(22, node.value * 2.4));
  return hovered ? baseRadius + 4 : baseRadius;
}

function getNodeDescription(node: KnowledgeGraphNode): string {
  switch (node.type) {
    case 'workspace':
      return 'Workspace node anchoring note relationships';
    case 'note':
      return `Note node${node.metadata?.note_type ? ` (${node.metadata.note_type})` : ''}`;
    case 'entity':
      return 'Extracted entity connected to notes that mention it';
    case 'tag':
      return 'Tag node connected to notes and related tags';
    default:
      return 'Knowledge graph node';
  }
}

export function KnowledgeGraphView({ notes = [] }: KnowledgeGraphViewProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [selectedNode, setSelectedNode] = useState<KnowledgeGraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [graphData, setGraphData] = useState<KnowledgeGraph>(EMPTY_GRAPH);
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
  const [filters, setFilters] = useState<KnowledgeGraphFilters>({
    nodeTypes: [],
    edgeTypes: [],
    search: '',
    minWeight: 0,
    includeIsolated: true,
  });

  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;

  useEffect(() => {
    if (!workspaceId) {
      setGraphData(generateKnowledgeGraph(notes));
      return;
    }

    const loadGraph = async () => {
      try {
        setIsLoading(true);
        const response = await api.getKnowledgeGraph(workspaceId, {
          nodeTypes: filters.nodeTypes,
          edgeTypes: filters.edgeTypes,
          minWeight: filters.minWeight,
          includeIsolated: filters.includeIsolated,
        });
        setGraphData(response);
      } catch (err) {
        console.error('Failed to load knowledge graph:', err);
        setGraphData(generateKnowledgeGraph(notes));
      } finally {
        setIsLoading(false);
      }
    };

    loadGraph();
  }, [workspaceId, notes, filters.nodeTypes, filters.edgeTypes, filters.minWeight, filters.includeIsolated]);

  const nodes = graphData.nodes ?? [];
  const edges = graphData.edges ?? [];

  const filteredNodes = useMemo(() => {
    if (!filters.search) {
      return nodes;
    }
    const searchTerm = filters.search.toLowerCase();
    return nodes.filter((node) => {
      const noteTags = Array.isArray(node.metadata?.tags) ? node.metadata.tags.join(' ') : '';
      return `${node.label} ${noteTags}`.toLowerCase().includes(searchTerm);
    });
  }, [nodes, filters.search]);

  const filteredEdges = useMemo(() => {
    const nodeIds = new Set(filteredNodes.map((node) => node.id));
    return edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  }, [edges, filteredNodes]);

  useEffect(() => {
    const width = 800;
    const height = 600;
    const nextPositions = new Map<string, { x: number; y: number }>();

    filteredNodes.forEach((node, index) => {
      const angle = (index / Math.max(filteredNodes.length, 1)) * 2 * Math.PI;
      const radius = Math.min(width, height) * 0.35;
      nextPositions.set(node.id, {
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius,
      });
    });

    for (let iteration = 0; iteration < 50; iteration += 1) {
      filteredNodes.forEach((node, leftIndex) => {
        const leftPosition = nextPositions.get(node.id);
        if (!leftPosition) {
          return;
        }

        let forceX = 0;
        let forceY = 0;

        filteredNodes.forEach((otherNode, rightIndex) => {
          if (leftIndex === rightIndex) {
            return;
          }
          const rightPosition = nextPositions.get(otherNode.id);
          if (!rightPosition) {
            return;
          }
          const deltaX = leftPosition.x - rightPosition.x;
          const deltaY = leftPosition.y - rightPosition.y;
          const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY) || 1;
          const repulsion = 2000 / (distance * distance);
          forceX += (deltaX / distance) * repulsion;
          forceY += (deltaY / distance) * repulsion;
        });

        leftPosition.x += forceX * 0.1;
        leftPosition.y += forceY * 0.1;
      });

      filteredEdges.forEach((edge) => {
        const sourcePosition = nextPositions.get(edge.source);
        const targetPosition = nextPositions.get(edge.target);
        if (!sourcePosition || !targetPosition) {
          return;
        }
        const deltaX = targetPosition.x - sourcePosition.x;
        const deltaY = targetPosition.y - sourcePosition.y;
        const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY) || 1;
        const attraction = (distance - 100) * 0.01;
        sourcePosition.x += (deltaX / distance) * attraction;
        sourcePosition.y += (deltaY / distance) * attraction;
        targetPosition.x -= (deltaX / distance) * attraction;
        targetPosition.y -= (deltaY / distance) * attraction;
      });

      filteredNodes.forEach((node) => {
        const position = nextPositions.get(node.id);
        if (!position) {
          return;
        }
        position.x += (width / 2 - position.x) * 0.05;
        position.y += (height / 2 - position.y) * 0.05;
      });
    }

    setPositions(nextPositions);
  }, [filteredNodes, filteredEdges]);

  useEffect(() => {
    if (!selectedNode) {
      return;
    }
    const nextSelectedNode = nodes.find((node) => node.id === selectedNode.id) ?? null;
    setSelectedNode(nextSelectedNode);
  }, [nodes, selectedNode?.id]);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.target === svgRef.current) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
    }
  };

  const handleMouseUp = () => setIsDragging(false);

  const connectedNotes = useMemo(() => {
    if (!selectedNode) {
      return [];
    }
    const connectedNoteIds = new Set(getNodeNoteIds(selectedNode));
    return notes.filter((note) => connectedNoteIds.has(note.id));
  }, [notes, selectedNode]);

  const stats = [
    { label: 'Nodes', value: graphData.stats.total_nodes ?? nodes.length },
    { label: 'Connections', value: graphData.stats.total_edges ?? edges.length },
    { label: 'Notes', value: graphData.stats.node_types.note ?? 0 },
    { label: 'Tags', value: graphData.stats.node_types.tag ?? 0 },
  ];

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>
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

        <div style={{ position: 'relative' }}>
          <Search size={12} color={TT.inkMuted} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)' }} />
          <input
            value={filters.search}
            onChange={(e) => setFilters((current) => ({ ...current, search: e.target.value }))}
            placeholder="Find graph nodes..."
            style={{
              height: 36,
              width: 220,
              background: TT.inkRaised,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 3,
              color: TT.snow,
              fontFamily: TT.fontMono,
              fontSize: 11,
              letterSpacing: '0.02em',
              paddingLeft: 30,
              paddingRight: 12,
              outline: 'none',
              boxSizing: 'border-box',
            }}
            onFocus={(e) => {
              (e.target as HTMLInputElement).style.borderColor = TT.yolk;
            }}
            onBlur={(e) => {
              (e.target as HTMLInputElement).style.borderColor = TT.inkBorder;
            }}
          />
        </div>
      </div>

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
        {isLoading && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(10,10,10,0.6)',
              zIndex: 20,
              fontFamily: TT.fontMono,
              fontSize: 11,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              color: TT.inkMuted,
            }}
          >
            Loading graph...
          </div>
        )}

        {!isLoading && filteredNodes.length === 0 && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 10,
              color: TT.inkMuted,
              fontSize: 11,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            No graph nodes match the current workspace and filters
          </div>
        )}

        <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', flexDirection: 'column', gap: 4, zIndex: 10 }}>
          <IconBtn onClick={() => setZoom((current) => Math.min(current * 1.2, 3))} title="Zoom in"><ZoomIn size={14} /></IconBtn>
          <IconBtn onClick={() => setZoom((current) => Math.max(current / 1.2, 0.3))} title="Zoom out"><ZoomOut size={14} /></IconBtn>
          <IconBtn onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }} title="Reset"><Maximize2 size={14} /></IconBtn>
        </div>

        <div
          style={{
            position: 'absolute',
            bottom: 12,
            left: 12,
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
            { label: 'Workspace', color: categoryColors.workspace },
            { label: 'Note', color: categoryColors.note },
            { label: 'Entity', color: categoryColors.entity },
            { label: 'Tag', color: categoryColors.tag },
          ].map(({ label, color }) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: `0 0 5px ${color}60` }} />
              <span style={{ fontSize: 9.5, letterSpacing: '0.04em', color: TT.inkMuted }}>{label}</span>
            </div>
          ))}
        </div>

        <div style={{ position: 'absolute', top: 12, left: 12, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.06em', color: TT.inkMid, textTransform: 'uppercase' }}>
          {Math.round(zoom * 100)}%
        </div>

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
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
            {filteredEdges.map((edge) => {
              const sourcePosition = positions.get(edge.source);
              const targetPosition = positions.get(edge.target);
              if (!sourcePosition || !targetPosition) {
                return null;
              }
              const highlighted = hoveredNode && (edge.source === hoveredNode || edge.target === hoveredNode);
              return (
                <line
                  key={edge.id}
                  x1={sourcePosition.x}
                  y1={sourcePosition.y}
                  x2={targetPosition.x}
                  y2={targetPosition.y}
                  stroke={highlighted ? TT.yolk : TT.inkMid}
                  strokeWidth={highlighted ? 1.5 : 0.8}
                  opacity={hoveredNode && !highlighted ? 0.1 : highlighted ? 0.7 : 0.4}
                />
              );
            })}

            {filteredNodes.map((node) => {
              const position = positions.get(node.id);
              if (!position) {
                return null;
              }
              const hovered = hoveredNode === node.id;
              const selected = selectedNode?.id === node.id;
              const radius = getNodeRadius(node, hovered);
              const color = selected || hovered ? TT.yolk : getNodeColor(node);

              return (
                <g
                  key={node.id}
                  transform={`translate(${position.x},${position.y})`}
                  onMouseEnter={() => setHoveredNode(node.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                  onClick={() => setSelectedNode(node)}
                  style={{ cursor: 'pointer' }}
                  filter={hovered ? 'url(#glow)' : undefined}
                >
                  <circle
                    r={radius}
                    fill={color}
                    opacity={hoveredNode && !hovered ? 0.2 : 1}
                    stroke={selected ? TT.snow : 'none'}
                    strokeWidth={selected ? 2 : 0}
                  />
                  {node.type !== 'note' && (
                    <text
                      dy={radius + 13}
                      textAnchor="middle"
                      fill={hovered ? TT.yolk : TT.inkMuted}
                      fontSize={9}
                      fontFamily={TT.fontMono}
                      letterSpacing="0.03em"
                      style={{ pointerEvents: 'none', textTransform: 'uppercase' }}
                    >
                      {node.label}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

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
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: getNodeColor(selectedNode), boxShadow: `0 0 8px ${getNodeColor(selectedNode)}80` }} />
              <span style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow }}>
                {selectedNode.label.toUpperCase()}
              </span>
              <span
                style={{
                  fontFamily: TT.fontMono,
                  fontSize: 9,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  padding: '2px 7px',
                  background: 'rgba(245,230,66,0.08)',
                  color: TT.yolk,
                  border: '1px solid rgba(245,230,66,0.2)',
                  borderRadius: 2,
                }}
              >
                {getNodeNoteIds(selectedNode).length} notes
              </span>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              style={{ background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2, cursor: 'pointer', padding: '4px 6px', color: TT.inkMuted, transition: 'all 0.15s' }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.color = TT.error;
                (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
                (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
              }}
            >
              <X size={12} />
            </button>
          </div>

          <p style={{ fontSize: 10.5, letterSpacing: '0.04em', color: TT.inkMuted, marginBottom: 14 }}>
            {getNodeDescription(selectedNode)}
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
