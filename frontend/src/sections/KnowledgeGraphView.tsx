import { useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { ZoomIn, ZoomOut, Maximize2, Search, X } from 'lucide-react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force-3d';

import type { KnowledgeGraph, KnowledgeGraphCluster, KnowledgeGraphEdge, KnowledgeGraphFilters, KnowledgeGraphNode, Note } from '@/types';
import { generateKnowledgeGraph } from '@/lib/mockData';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface KnowledgeGraphViewProps { notes?: Note[]; }
interface RenderNode extends KnowledgeGraphNode { index?: number; x: number; y: number; vx: number; vy: number; fx?: number | null; fy?: number | null; radius: number; importance: number; clusterKey: string; }
interface RenderEdge extends Omit<KnowledgeGraphEdge, 'source' | 'target'> { source: string | RenderNode; target: string | RenderNode; idealDistance: number; pairIndex: number; pairCount: number; }

const TT = { inkBlack: '#0A0A0A', inkDeep: '#111111', inkRaised: '#1A1A1A', inkBorder: '#252525', inkMid: '#3A3A3A', inkMuted: '#5A5A5A', inkSubtle: '#888888', snow: '#F5F5F5', yolk: '#F5E642', error: '#FF4545', fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif", fontMono: "'IBM Plex Mono', monospace" };
const EMPTY_GRAPH: KnowledgeGraph = { nodes: [], edges: [], clusters: [], stats: { total_nodes: 0, total_edges: 0, total_clusters: 0, node_types: {}, edge_types: {} } };
const NODE_COLORS: Record<KnowledgeGraphNode['type'] | 'default', string> = { workspace: '#60A5FA', note: '#9CA3AF', entity: '#F5E642', tag: '#FB923C', default: '#5A5A5A' };
const EDGE_COLORS: Record<KnowledgeGraphEdge['type'], string> = { workspace_contains_note: '#60A5FA', note_mentions_entity: '#F5E642', note_has_tag: '#FB923C', note_links_note: '#9CA3AF', note_related_note: '#F472B6', entity_co_occurs_with_entity: '#34D399', tag_co_occurs_with_tag: '#F97316' };
const EDGE_DISTANCES: Record<KnowledgeGraphEdge['type'], number> = { workspace_contains_note: 180, note_mentions_entity: 120, note_has_tag: 105, note_links_note: 160, note_related_note: 200, entity_co_occurs_with_entity: 135, tag_co_occurs_with_tag: 125 };
const LABEL_ZOOM_THRESHOLD: Record<KnowledgeGraphNode['type'], number> = { workspace: 0.8, note: 1.45, entity: 1.05, tag: 0.95 };
const BASE_CLUSTER_POSITIONS: Record<KnowledgeGraphNode['type'], { x: number; y: number }> = { workspace: { x: 0.5, y: 0.18 }, note: { x: 0.28, y: 0.62 }, entity: { x: 0.72, y: 0.34 }, tag: { x: 0.72, y: 0.72 } };

function IconBtn({ onClick, children, title }: { onClick: () => void; children: ReactNode; title?: string }) {
  return <button onClick={onClick} title={title} style={{ width: 34, height: 34, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: TT.inkMuted, transition: 'all 0.15s' }} onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; }} onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}>{children}</button>;
}
function getNodeClusterKey(node: KnowledgeGraphNode): string { return typeof node.metadata?.cluster_id === 'string' ? node.metadata.cluster_id : typeof node.metadata?.cluster_key === 'string' ? node.metadata.cluster_key : node.type; }
function getNodeNoteIds(node: KnowledgeGraphNode): string[] { return Array.isArray(node.metadata?.note_ids) ? node.metadata.note_ids.filter((noteId): noteId is string => typeof noteId === 'string') : []; }
function getNodeColor(node: KnowledgeGraphNode): string { const displayColor = typeof node.metadata?.display_color === 'string' ? node.metadata.display_color : null; return displayColor || NODE_COLORS[node.type] || NODE_COLORS.default; }
function getNodeRadius(node: KnowledgeGraphNode): number { return Math.max(10, Math.min(26, 9 + node.value * 2.2)); }
function getNodeDescription(node: KnowledgeGraphNode): string { switch (node.type) { case 'workspace': return 'Workspace anchor for the graph cluster.'; case 'note': return `Note node${node.metadata?.note_type ? ` (${node.metadata.note_type})` : ''} grounded in your knowledge base.`; case 'entity': return 'Extracted entity grouped by the notes that mention it.'; case 'tag': return 'Tag node synced from note tags and inline hashtags.'; default: return 'Knowledge graph node.'; } }
function canonicalPairKey(sourceId: string, targetId: string): string { return sourceId < targetId ? `${sourceId}::${targetId}` : `${targetId}::${sourceId}`; }
function createClusterForce(clusterCenters: Record<string, { x: number; y: number }>, strength = 0.1) { let nodes: RenderNode[] = []; const centerKeys = Object.keys(clusterCenters); const fallbackTarget = clusterCenters[centerKeys[0] || 'default'] || { x: 0, y: 0 }; const force = (alpha: number) => { for (const node of nodes) { const target = clusterCenters[node.clusterKey] || clusterCenters[node.type] || fallbackTarget; const nodeStrength = strength * alpha * (node.type === 'workspace' ? 0.45 : 1); node.vx += (target.x - node.x) * nodeStrength; node.vy += (target.y - node.y) * nodeStrength; } }; force.initialize = (initialNodes: RenderNode[]) => { nodes = initialNodes; }; return force; }
function getClusterColor(clusterKey: string, fallbackColor: string): string { let hash = 0; for (let index = 0; index < clusterKey.length; index += 1) hash = ((hash << 5) - hash) + clusterKey.charCodeAt(index); const hue = Math.abs(hash) % 360; return `hsl(${hue} 72% 58%)`; }
function createConvexHull(points: Array<{ x: number; y: number }>): Array<{ x: number; y: number }> { if (points.length <= 1) return points; const sorted = [...points].sort((left, right) => (left.x === right.x ? left.y - right.y : left.x - right.x)); const cross = (origin: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }) => (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x); const lower: Array<{ x: number; y: number }> = []; for (const point of sorted) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop(); lower.push(point); } const upper: Array<{ x: number; y: number }> = []; for (const point of [...sorted].reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop(); upper.push(point); } lower.pop(); upper.pop(); return [...lower, ...upper]; }
function buildHullPath(nodes: RenderNode[]): string | null { if (!nodes.length) return null; const expandedPoints = nodes.flatMap((node) => { const samples = 10; const padding = node.type === 'workspace' ? 28 : 18; const radius = node.radius + padding; return Array.from({ length: samples }, (_, index) => { const angle = (Math.PI * 2 * index) / samples; return { x: node.x + Math.cos(angle) * radius, y: node.y + Math.sin(angle) * radius }; }); }); const hull = createConvexHull(expandedPoints); return hull.length < 3 ? null : `M ${hull.map((point) => `${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' L ')} Z`; }
function resolveNode(nodeRef: string | RenderNode, nodeLookup: Map<string, RenderNode>): RenderNode | null { return typeof nodeRef === 'string' ? nodeLookup.get(nodeRef) || null : nodeRef; }
function buildArcPath(edge: RenderEdge, nodeLookup: Map<string, RenderNode>): string | null { const source = resolveNode(edge.source, nodeLookup); const target = resolveNode(edge.target, nodeLookup); if (!source || !target) return null; if (source.id === target.id) { const loopRadius = source.radius + 20; return [`M ${source.x} ${source.y}`, `C ${source.x + loopRadius} ${source.y - loopRadius}`, `${source.x - loopRadius} ${source.y - loopRadius}`, `${source.x} ${source.y}`].join(' '); } const deltaX = target.x - source.x; const deltaY = target.y - source.y; const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY) || 1; const normalX = -deltaY / distance; const normalY = deltaX / distance; const pairOffset = (edge.pairIndex - (edge.pairCount - 1) / 2) * 24; const typeOffset = ((EDGE_DISTANCES[edge.type] || 140) - 140) * 0.16; const curve = pairOffset + typeOffset; const controlX = (source.x + target.x) / 2 + normalX * curve; const controlY = (source.y + target.y) / 2 + normalY * curve; return `M ${source.x} ${source.y} Q ${controlX} ${controlY} ${target.x} ${target.y}`; }
function shouldShowLabel(node: RenderNode, zoom: number, hoveredNodeId: string | null, selectedNodeId: string | null): boolean { return node.id === hoveredNodeId || node.id === selectedNodeId || zoom >= LABEL_ZOOM_THRESHOLD[node.type]; }

export function KnowledgeGraphView({ notes = [] }: KnowledgeGraphViewProps) {
  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;
  const viewportRef = useRef<HTMLDivElement>(null);
  const simulationRef = useRef<any>(null);
  const layoutRef = useRef<{ nodes: RenderNode[]; edges: RenderEdge[] }>({ nodes: [], edges: [] });
  const animationFrameRef = useRef<number | null>(null);
  const [graphData, setGraphData] = useState<KnowledgeGraph>(EMPTY_GRAPH);
  const [selectedNode, setSelectedNode] = useState<KnowledgeGraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [viewportSize, setViewportSize] = useState({ width: 1100, height: 560 });
  const [, setLayoutTick] = useState(0);
  const [filters, setFilters] = useState<KnowledgeGraphFilters>({ nodeTypes: [], edgeTypes: [], search: '', minWeight: 0, includeIsolated: true });

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setViewportSize({ width: Math.max(360, Math.round(entry.contentRect.width)), height: Math.max(420, Math.round(entry.contentRect.height)) });
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!workspaceId) {
      setGraphData(generateKnowledgeGraph(notes));
      return undefined;
    }

    let isMounted = true;
    const abortController = new AbortController();

    const loadGraph = async () => {
      try {
        setIsLoading(true);
        const response = await api.getKnowledgeGraph(
          workspaceId,
          {
            nodeTypes: filters.nodeTypes,
            edgeTypes: filters.edgeTypes,
            minWeight: filters.minWeight,
            includeIsolated: filters.includeIsolated,
          },
          {
            signal: abortController.signal,
            retries: false,
          },
        );
        if (isMounted) setGraphData(response);
      } catch (error) {
        if (abortController.signal.aborted) return;
        console.error('Failed to load knowledge graph:', error);
        if (isMounted) {
          setGraphData((current) => current.nodes.length ? current : generateKnowledgeGraph(notes));
        }
      } finally {
        if (isMounted && !abortController.signal.aborted) setIsLoading(false);
      }
    };

    loadGraph();
    return () => {
      isMounted = false;
      abortController.abort();
    };
  }, [workspaceId, filters.nodeTypes, filters.edgeTypes, filters.minWeight, filters.includeIsolated]);

  useEffect(() => {
    if (workspaceId) return;
    setGraphData(generateKnowledgeGraph(notes));
  }, [workspaceId, notes]);

  const filteredNodes = useMemo(() => {
    const nodes = graphData.nodes || [];
    if (!filters.search) return nodes;
    const searchTerm = filters.search.toLowerCase();
    return nodes.filter((node) => {
      const tags = Array.isArray(node.metadata?.tags) ? node.metadata.tags.join(' ') : '';
      return `${node.label} ${tags}`.toLowerCase().includes(searchTerm);
    });
  }, [graphData.nodes, filters.search]);

  const filteredEdges = useMemo(() => {
    const nodeIds = new Set(filteredNodes.map((node) => node.id));
    return (graphData.edges || []).filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  }, [graphData.edges, filteredNodes]);

  const filteredClusters = useMemo(() => {
    const nodeIds = new Set(filteredNodes.map((node) => node.id));
    return (graphData.clusters || []).filter((cluster) => cluster.node_ids.some((nodeId) => nodeIds.has(nodeId)));
  }, [graphData.clusters, filteredNodes]);

  const clusterLookup = useMemo(() => {
    const lookup = new Map<string, KnowledgeGraphCluster>();
    for (const cluster of filteredClusters) {
      lookup.set(cluster.id, cluster);
      lookup.set(cluster.key, cluster);
    }
    return lookup;
  }, [filteredClusters]);

  useEffect(() => {
    const previousPositions = new Map(layoutRef.current.nodes.map((node) => [node.id, { x: node.x, y: node.y }]));
    const width = viewportSize.width;
    const height = viewportSize.height;
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    if (simulationRef.current) {
      simulationRef.current.stop();
      simulationRef.current = null;
    }
    if (!filteredNodes.length) {
      layoutRef.current = { nodes: [], edges: [] };
      setLayoutTick((tick) => tick + 1);
      return undefined;
    }

    const orderedClusterKeys = filteredClusters.length
      ? filteredClusters.map((cluster) => cluster.id || cluster.key)
      : Array.from(new Set(filteredNodes.map((node) => getNodeClusterKey(node))));
    const clusterCenters = orderedClusterKeys.reduce<Record<string, { x: number; y: number }>>((accumulator, clusterKey, index) => {
      const cluster = clusterLookup.get(clusterKey);
      if (cluster) {
        const angle = (Math.PI * 2 * index) / Math.max(orderedClusterKeys.length, 1);
        const radiusX = Math.max(120, width * 0.26);
        const radiusY = Math.max(90, height * 0.22);
        accumulator[clusterKey] = {
          x: width / 2 + Math.cos(angle) * radiusX,
          y: height / 2 + Math.sin(angle) * radiusY,
        };
      } else {
        const nodeTypeKey = clusterKey as KnowledgeGraphNode['type'];
        const ratio = BASE_CLUSTER_POSITIONS[nodeTypeKey] || BASE_CLUSTER_POSITIONS.note;
        accumulator[clusterKey] = { x: width * ratio.x, y: height * ratio.y };
      }
      return accumulator;
    }, {});

    const renderNodes: RenderNode[] = filteredNodes.map((node, index) => {
      const clusterKey = getNodeClusterKey(node);
      const clusterAnchor = clusterCenters[clusterKey] || { x: width / 2, y: height / 2 };
      const previous = previousPositions.get(node.id);
      return { ...node, x: previous?.x ?? clusterAnchor.x + ((index % 5) - 2) * 24, y: previous?.y ?? clusterAnchor.y + ((index % 7) - 3) * 18, vx: 0, vy: 0, radius: getNodeRadius(node), importance: Math.max(1, node.value), clusterKey };
    });

    const pairTotals = new Map<string, number>();
    for (const edge of filteredEdges) pairTotals.set(canonicalPairKey(edge.source, edge.target), (pairTotals.get(canonicalPairKey(edge.source, edge.target)) || 0) + 1);
    const pairIndexes = new Map<string, number>();
    const renderEdges: RenderEdge[] = filteredEdges.map((edge) => {
      const key = canonicalPairKey(edge.source, edge.target);
      const nextPairIndex = pairIndexes.get(key) || 0;
      pairIndexes.set(key, nextPairIndex + 1);
      return { ...edge, source: edge.source, target: edge.target, idealDistance: EDGE_DISTANCES[edge.type] || 150, pairIndex: nextPairIndex, pairCount: pairTotals.get(key) || 1 };
    });

    layoutRef.current = { nodes: renderNodes, edges: renderEdges };

    const simulation = forceSimulation(renderNodes, 2)
      .force('link', forceLink(renderEdges).id((node: RenderNode) => node.id).distance((edge: RenderEdge) => edge.idealDistance).strength((edge: RenderEdge) => Math.max(0.12, Math.min(0.55, 0.16 + edge.weight * 0.12))))
      .force('charge', forceManyBody().strength((node: RenderNode) => -(80 + node.importance * 18 + node.radius * 2.5)))
      .force('center', forceCenter(width / 2, height / 2))
      .force('collision', forceCollide().radius((node: RenderNode) => node.radius + 12).iterations(2))
      .force('cluster', createClusterForce(clusterCenters, 0.14))
      .alpha(0.95)
      .alphaDecay(0.04)
      .velocityDecay(0.28);

    const scheduleRender = () => {
      if (animationFrameRef.current !== null) return;
      animationFrameRef.current = window.requestAnimationFrame(() => {
        animationFrameRef.current = null;
        setLayoutTick((tick) => tick + 1);
      });
    };

    simulation.on('tick', scheduleRender);
    simulation.on('end', scheduleRender);
    simulationRef.current = simulation;
    scheduleRender();

    return () => {
      simulation.on('tick', null);
      simulation.on('end', null);
      simulation.stop();
      if (animationFrameRef.current !== null) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
    };
  }, [clusterLookup, filteredClusters, filteredNodes, filteredEdges, viewportSize]);

  useEffect(() => {
    if (!selectedNode) return;
    const nextSelectedNode = graphData.nodes.find((node) => node.id === selectedNode.id) || null;
    setSelectedNode(nextSelectedNode);
  }, [graphData.nodes, selectedNode?.id]);

  const connectedNotes = useMemo(() => {
    if (!selectedNode) return [];
    const connectedIds = new Set(getNodeNoteIds(selectedNode));
    return notes.filter((note) => connectedIds.has(note.id));
  }, [notes, selectedNode]);

  const renderedNodes = layoutRef.current.nodes;
  const renderedEdges = layoutRef.current.edges;
  const nodeLookup = useMemo(() => new Map(renderedNodes.map((node) => [node.id, node])), [renderedNodes]);

  const clusterHulls = useMemo(() => {
    const grouped = renderedNodes.reduce<Record<string, RenderNode[]>>((accumulator, node) => {
      accumulator[node.clusterKey] = accumulator[node.clusterKey] || [];
      accumulator[node.clusterKey]?.push(node);
      return accumulator;
    }, {});
    return Object.entries(grouped).map(([clusterKey, nodes]) => {
      const cluster = clusterLookup.get(clusterKey);
      const label = cluster?.label || clusterKey;
      const color = cluster ? getClusterColor(clusterKey, getNodeColor(nodes[0]!)) : getNodeColor(nodes[0]!);
      const center = nodes.reduce((accumulator, node) => ({ x: accumulator.x + node.x / nodes.length, y: accumulator.y + node.y / nodes.length }), { x: 0, y: 0 });
      return { key: clusterKey, label, description: cluster?.description || '', color, path: buildHullPath(nodes), center };
    }).filter((hull) => Boolean(hull.path));
  }, [clusterLookup, renderedNodes]);

  const stats = [
    { label: 'Nodes', value: graphData.stats.total_nodes || filteredNodes.length },
    { label: 'Connections', value: graphData.stats.total_edges || filteredEdges.length },
    { label: 'Clusters', value: graphData.stats.total_clusters || filteredClusters.length },
    { label: 'Notes', value: graphData.stats.node_types.note || 0 },
  ];

  const handleMouseDown = (event: React.MouseEvent<SVGSVGElement>) => {
    const target = event.target as Element;
    if (target.closest('[data-graph-node="true"]')) return;
    setIsDragging(true);
    setDragStart({ x: event.clientX - pan.x, y: event.clientY - pan.y });
  };
  const handleMouseMove = (event: React.MouseEvent<SVGSVGElement>) => { if (isDragging) setPan({ x: event.clientX - dragStart.x, y: event.clientY - dragStart.y }); };
  const handleMouseUp = () => setIsDragging(false);
  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => { event.preventDefault(); const scaleDelta = event.deltaY > 0 ? 0.9 : 1.1; setZoom((currentZoom) => Math.max(0.35, Math.min(2.8, currentZoom * scaleDelta))); };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 28, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Visualization</span>
          </div>
          <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 44, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase' }}><span style={{ color: TT.yolk }}>K</span>NOWLEDGE GRAPH</h1>
          <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
        </div>
        <div style={{ position: 'relative' }}>
          <Search size={12} color={TT.inkMuted} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)' }} />
          <input value={filters.search} onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))} placeholder="Find graph nodes..." style={{ height: 36, width: 220, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.02em', paddingLeft: 30, paddingRight: 12, outline: 'none', boxSizing: 'border-box' }} onFocus={(event) => { (event.target as HTMLInputElement).style.borderColor = TT.yolk; }} onBlur={(event) => { (event.target as HTMLInputElement).style.borderColor = TT.inkBorder; }} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 20 }}>
        {stats.map(({ label, value }) => <div key={label} style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '12px 16px' }}><div style={{ fontFamily: TT.fontDisplay, fontSize: 32, color: TT.snow, letterSpacing: '0.02em', lineHeight: 1 }}>{value}</div><div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginTop: 3 }}>{label}</div></div>)}
      </div>

      <div ref={viewportRef} onWheel={handleWheel} style={{ position: 'relative', background: `radial-gradient(circle at 20% 20%, rgba(245,230,66,0.08), transparent 26%), linear-gradient(135deg, ${TT.inkDeep}, #171717 60%, #101010)`, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, overflow: 'hidden', height: 560 }}>
        {isLoading && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(10,10,10,0.6)', zIndex: 20, fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Loading graph...</div>}
        {!isLoading && !renderedNodes.length && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10, color: TT.inkMuted, fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase' }}>No graph nodes match the current workspace and filters</div>}
        <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', flexDirection: 'column', gap: 4, zIndex: 10 }}>
          <IconBtn onClick={() => setZoom((current) => Math.min(current * 1.2, 2.8))} title="Zoom in"><ZoomIn size={14} /></IconBtn>
          <IconBtn onClick={() => setZoom((current) => Math.max(current / 1.2, 0.35))} title="Zoom out"><ZoomOut size={14} /></IconBtn>
          <IconBtn onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }} title="Reset"><Maximize2 size={14} /></IconBtn>
        </div>
        <div style={{ position: 'absolute', bottom: 12, left: 12, background: 'rgba(10,10,10,0.85)', border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '10px 14px', zIndex: 10, maxWidth: 260 }}>
          <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Legend</div>
          {[{ label: 'Semantic Clusters', color: TT.yolk }, { label: 'Note Links', color: EDGE_COLORS.note_links_note }, { label: 'Entities', color: NODE_COLORS.entity }, { label: 'Tags', color: NODE_COLORS.tag }].map(({ label, color }) => <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 }}><div style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: `0 0 5px ${color}60` }} /><span style={{ fontSize: 9.5, letterSpacing: '0.04em', color: TT.inkMuted }}>{label}</span></div>)}
          {filteredClusters.slice(0, 3).map((cluster) => <div key={cluster.id} style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${TT.inkBorder}` }}><div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.snow }}>{cluster.label}</div><div style={{ fontSize: 9, lineHeight: 1.4, color: TT.inkMuted, marginTop: 3 }}>{cluster.description}</div></div>)}
        </div>
        <div style={{ position: 'absolute', top: 12, left: 12, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.06em', color: TT.inkMid, textTransform: 'uppercase' }}>{Math.round(zoom * 100)}%</div>
        <svg style={{ width: '100%', height: '100%', cursor: isDragging ? 'grabbing' : 'grab' }} viewBox={`0 0 ${viewportSize.width} ${viewportSize.height}`} onMouseDown={handleMouseDown} onMouseMove={handleMouseMove} onMouseUp={handleMouseUp} onMouseLeave={handleMouseUp}>
          <defs>
            <filter id="node-glow">
              <feGaussianBlur stdDeviation="5" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            {Object.entries(EDGE_COLORS).map(([type, color]) => <marker key={type} id={`arrow-${type}`} markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M 0 0 L 10 5 L 0 10 z" fill={color} /></marker>)}
          </defs>
          <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
            {clusterHulls.map((hull) => <g key={`hull-${hull.key}`}><path d={hull.path || ''} fill={hull.color} opacity={0.08} stroke={hull.color} strokeWidth={1.2} strokeOpacity={0.28} />{zoom >= 0.75 && <text x={hull.center.x} y={hull.center.y} textAnchor="middle" fill={hull.color} fontSize={10} fontFamily={TT.fontMono} letterSpacing="0.08em" style={{ pointerEvents: 'none', textTransform: 'uppercase' }}>{hull.label}</text>}</g>)}
            {renderedEdges.map((edge) => {
              const path = buildArcPath(edge, nodeLookup);
              if (!path) return null;
              const sourceId = typeof edge.source === 'string' ? edge.source : edge.source.id;
              const targetId = typeof edge.target === 'string' ? edge.target : edge.target.id;
              const isHighlighted = hoveredNode === sourceId || hoveredNode === targetId;
              return <path key={edge.id} d={path} fill="none" stroke={EDGE_COLORS[edge.type]} strokeWidth={isHighlighted ? 2.1 : 1.35} strokeOpacity={hoveredNode && !isHighlighted ? 0.12 : 0.72} markerEnd={`url(#arrow-${edge.type})`} />;
            })}
            {renderedNodes.map((node) => {
              const isHovered = hoveredNode === node.id;
              const isSelected = selectedNode?.id === node.id;
              const showLabel = shouldShowLabel(node, zoom, hoveredNode, selectedNode?.id || null);
              const nodeColor = getNodeColor(node);
              return (
                <g key={node.id} data-graph-node="true" transform={`translate(${node.x}, ${node.y})`} style={{ cursor: 'pointer' }} onMouseEnter={() => setHoveredNode(node.id)} onMouseLeave={() => setHoveredNode(null)} onClick={() => setSelectedNode(node)}>
                  {isSelected && <><circle r={node.radius + 10} fill="none" stroke={TT.yolk} strokeWidth={1.3} opacity={0.42} filter="url(#node-glow)" /><circle r={node.radius + 17} fill="none" stroke={TT.yolk} strokeWidth={0.9} opacity={0.18} /></>}
                  <circle r={node.radius} fill={nodeColor} opacity={hoveredNode && !isHovered ? 0.28 : 1} stroke={isSelected ? TT.snow : 'rgba(255,255,255,0.08)'} strokeWidth={isSelected ? 2 : 1} filter={isHovered ? 'url(#node-glow)' : undefined} />
                  {showLabel && <text y={node.radius + 16} textAnchor="middle" fill={isSelected || isHovered ? TT.yolk : TT.snow} fontSize={node.type === 'note' ? 10 : 9} fontFamily={TT.fontMono} letterSpacing="0.03em" style={{ pointerEvents: 'none', textTransform: 'uppercase' }}>{node.label}</text>}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {selectedNode && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} style={{ marginTop: 12, background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '18px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: getNodeColor(selectedNode), boxShadow: `0 0 8px ${getNodeColor(selectedNode)}80` }} />
            <span style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow }}>{selectedNode.label.toUpperCase()}</span>
            <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(245,230,66,0.08)', color: TT.yolk, border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2 }}>{getNodeNoteIds(selectedNode).length} notes</span>
            {typeof selectedNode.metadata?.cluster_label === 'string' && <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(255,255,255,0.05)', color: TT.snow, border: `1px solid ${TT.inkBorder}`, borderRadius: 2 }}>{selectedNode.metadata.cluster_label}</span>}
          </div>
          <button onClick={() => setSelectedNode(null)} style={{ background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2, cursor: 'pointer', padding: '4px 6px', color: TT.inkMuted, transition: 'all 0.15s' }} onMouseEnter={(event) => { (event.currentTarget as HTMLElement).style.color = TT.error; (event.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)'; }} onMouseLeave={(event) => { (event.currentTarget as HTMLElement).style.color = TT.inkMuted; (event.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}><X size={12} /></button>
          </div>
          <p style={{ fontSize: 10.5, letterSpacing: '0.04em', color: TT.inkMuted, marginBottom: 14 }}>{getNodeDescription(selectedNode)}</p>
          {typeof selectedNode.metadata?.cluster_description === 'string' && <p style={{ fontSize: 10, letterSpacing: '0.04em', color: TT.snow, marginBottom: 14 }}>{selectedNode.metadata.cluster_description}</p>}
          {connectedNotes.length > 0 && <>
            <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Connected Notes</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
              {connectedNotes.slice(0, 4).map((note) => <div key={note.id} style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px' }}><p style={{ fontFamily: TT.fontMono, fontSize: 11.5, color: TT.snow, marginBottom: 4, letterSpacing: '0.02em', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{note.title}</p><p style={{ fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.03em' }}>{(note.tags ?? []).slice(0, 3).join(' · ')}</p></div>)}
            </div>
          </>}
        </motion.div>
      )}
    </div>
  );
}
