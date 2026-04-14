import { useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { ZoomIn, ZoomOut, Maximize2, Search, X } from 'lucide-react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force-3d';
import { select } from 'd3-selection';
import { zoom as createZoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from 'd3-zoom';

import type { KnowledgeGraph, KnowledgeGraphCluster, KnowledgeGraphEdge, KnowledgeGraphFilters, KnowledgeGraphNode, Note } from '@/types';
import { generateKnowledgeGraph } from '@/lib/mockData';
import { api } from '@/lib/api';
import { transformNoteFromAPI } from '@/lib/transformers';
import { AuthContext } from '@/contexts/AuthContext';
import { defaultKnowledgeGraphFilters, resetKnowledgeGraphFilters, setKnowledgeGraphFilters, useKnowledgeGraphFilters } from '@/stores/knowledgeGraphFilters';

interface KnowledgeGraphViewProps { notes?: Note[]; }
interface RenderNode extends KnowledgeGraphNode { index?: number; x: number; y: number; vx: number; vy: number; fx?: number | null; fy?: number | null; radius: number; importance: number; clusterKey: string; }
interface RenderEdge extends Omit<KnowledgeGraphEdge, 'source' | 'target'> { source: string | RenderNode; target: string | RenderNode; idealDistance: number; pairIndex: number; pairCount: number; }
interface NodeRelationship { edge: KnowledgeGraphEdge; node: KnowledgeGraphNode; direction: 'incoming' | 'outgoing'; }

const TT = { inkBlack: '#0A0A0A', inkDeep: '#111111', inkRaised: '#1A1A1A', inkBorder: '#252525', inkMid: '#3A3A3A', inkMuted: '#5A5A5A', inkSubtle: '#888888', snow: '#F5F5F5', yolk: '#F5E642', error: '#FF4545', fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif", fontMono: "'IBM Plex Mono', monospace" };
const EMPTY_GRAPH: KnowledgeGraph = { nodes: [], edges: [], clusters: [], stats: { total_nodes: 0, total_edges: 0, total_clusters: 0, node_types: {}, edge_types: {} } };
const EMPTY_NOTES: Note[] = [];
const NODE_COLORS: Record<KnowledgeGraphNode['type'] | 'default', string> = { workspace: '#60A5FA', note: '#9CA3AF', entity: '#F5E642', tag: '#FB923C', default: '#5A5A5A' };
const EDGE_COLORS: Record<KnowledgeGraphEdge['type'], string> = { workspace_contains_note: '#60A5FA', note_mentions_entity: '#F5E642', note_has_tag: '#FB923C', note_links_note: '#9CA3AF', note_related_note: '#F472B6', entity_co_occurs_with_entity: '#34D399', tag_co_occurs_with_tag: '#F97316' };
const EDGE_DISTANCES: Record<KnowledgeGraphEdge['type'], number> = { workspace_contains_note: 180, note_mentions_entity: 120, note_has_tag: 105, note_links_note: 160, note_related_note: 200, entity_co_occurs_with_entity: 135, tag_co_occurs_with_tag: 125 };
const LABEL_ZOOM_THRESHOLD: Record<KnowledgeGraphNode['type'], number> = { workspace: 0.8, note: 1.45, entity: 1.05, tag: 0.95 };
const BASE_CLUSTER_POSITIONS: Record<KnowledgeGraphNode['type'], { x: number; y: number }> = { workspace: { x: 0.5, y: 0.18 }, note: { x: 0.28, y: 0.62 }, entity: { x: 0.72, y: 0.34 }, tag: { x: 0.72, y: 0.72 } };
const NODE_TYPE_LABELS: Record<KnowledgeGraphNode['type'], string> = { workspace: 'Workspace', note: 'Notes', entity: 'Entities', tag: 'Tags' };
const EDGE_TYPE_LABELS: Record<KnowledgeGraphEdge['type'], string> = { workspace_contains_note: 'Workspace -> Note', note_mentions_entity: 'Mentions Entity', note_has_tag: 'Has Tag', note_links_note: 'Explicit Links', note_related_note: 'Suggested Links', entity_co_occurs_with_entity: 'Entity Co-occurrence', tag_co_occurs_with_tag: 'Tag Co-occurrence' };
const ZOOM_PRESETS = { macro: 0.65, meso: 1.15, micro: 1.95 } as const;
type ZoomMode = keyof typeof ZOOM_PRESETS;

function IconBtn({ onClick, children, title }: { onClick: () => void; children: ReactNode; title?: string }) {
  return <button onClick={onClick} title={title} style={{ width: 34, height: 34, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: TT.inkMuted, transition: 'all 0.15s' }} onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; }} onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}>{children}</button>;
}
function FilterChip({ label, active, color, onClick }: { label: string; active: boolean; color?: string; onClick: () => void }) {
  return <button onClick={onClick} style={{ borderRadius: 999, border: `1px solid ${active ? color || TT.yolk : TT.inkBorder}`, background: active ? `${color || TT.yolk}18` : TT.inkRaised, color: active ? TT.snow : TT.inkMuted, fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '8px 12px', cursor: 'pointer', transition: 'all 0.15s', display: 'inline-flex', alignItems: 'center', gap: 8 }}>{color && <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}55` }} />}{label}</button>;
}
function toggleValue<T extends string>(values: T[], value: T): T[] { return values.includes(value) ? values.filter((item) => item !== value) : [...values, value]; }
function getNodeClusterKey(node: KnowledgeGraphNode): string { return typeof node.metadata?.cluster_id === 'string' ? node.metadata.cluster_id : typeof node.metadata?.cluster_key === 'string' ? node.metadata.cluster_key : node.type; }
function getNodeNoteIds(node: KnowledgeGraphNode): string[] { return Array.isArray(node.metadata?.note_ids) ? node.metadata.note_ids.filter((noteId): noteId is string => typeof noteId === 'string') : []; }
function getNodeColor(node: KnowledgeGraphNode): string { const displayColor = typeof node.metadata?.display_color === 'string' ? node.metadata.display_color : null; return displayColor || NODE_COLORS[node.type] || NODE_COLORS.default; }
function getNodeRadius(node: KnowledgeGraphNode): number { return Math.max(10, Math.min(26, 9 + node.value * 2.2)); }
function getNodeDescription(node: KnowledgeGraphNode): string { switch (node.type) { case 'workspace': return 'Workspace anchor for the graph cluster.'; case 'note': return `Note node${node.metadata?.note_type ? ` (${node.metadata.note_type})` : ''} grounded in your knowledge base.`; case 'entity': return 'Extracted entity grouped by the notes that mention it.'; case 'tag': return 'Tag node synced from note tags and inline hashtags.'; default: return 'Knowledge graph node.'; } }
function getNodeMetaTokens(node: KnowledgeGraphNode): string[] { const tags = Array.isArray(node.metadata?.tags) ? node.metadata.tags.filter((tag): tag is string => typeof tag === 'string') : []; return [node.label, ...tags, typeof node.metadata?.cluster_label === 'string' ? node.metadata.cluster_label : '', typeof node.metadata?.cluster_description === 'string' ? node.metadata.cluster_description : '', typeof node.metadata?.note_type === 'string' ? node.metadata.note_type : ''].filter(Boolean); }
function getEdgeDescription(edgeType: KnowledgeGraphEdge['type']): string { switch (edgeType) { case 'workspace_contains_note': return 'This note belongs to the active workspace.'; case 'note_mentions_entity': return 'The note text mentions this entity.'; case 'note_has_tag': return 'The note is tagged with this keyword.'; case 'note_links_note': return 'These notes are explicitly linked.'; case 'note_related_note': return 'Similarity and graph signals suggest these notes belong together.'; case 'entity_co_occurs_with_entity': return 'These entities often appear in the same notes.'; case 'tag_co_occurs_with_tag': return 'These tags commonly appear together.'; default: return 'Graph relationship.'; } }
function canonicalPairKey(sourceId: string, targetId: string): string { return sourceId < targetId ? `${sourceId}::${targetId}` : `${targetId}::${sourceId}`; }
function createClusterForce(clusterCenters: Record<string, { x: number; y: number }>, strength = 0.1) { let nodes: RenderNode[] = []; const centerKeys = Object.keys(clusterCenters); const fallbackTarget = clusterCenters[centerKeys[0] || 'default'] || { x: 0, y: 0 }; const force = (alpha: number) => { for (const node of nodes) { const target = clusterCenters[node.clusterKey] || clusterCenters[node.type] || fallbackTarget; const nodeStrength = strength * alpha * (node.type === 'workspace' ? 0.45 : 1); node.vx += (target.x - node.x) * nodeStrength; node.vy += (target.y - node.y) * nodeStrength; } }; force.initialize = (initialNodes: RenderNode[]) => { nodes = initialNodes; }; return force; }
function getClusterColor(clusterKey: string, fallbackColor: string): string { let hash = 0; for (let index = 0; index < clusterKey.length; index += 1) hash = ((hash << 5) - hash) + clusterKey.charCodeAt(index); const hue = Math.abs(hash) % 360; return `hsl(${hue} 72% 58%)`; }
function createConvexHull(points: Array<{ x: number; y: number }>): Array<{ x: number; y: number }> { if (points.length <= 1) return points; const sorted = [...points].sort((left, right) => (left.x === right.x ? left.y - right.y : left.x - right.x)); const cross = (origin: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }) => (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x); const lower: Array<{ x: number; y: number }> = []; for (const point of sorted) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop(); lower.push(point); } const upper: Array<{ x: number; y: number }> = []; for (const point of [...sorted].reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop(); upper.push(point); } lower.pop(); upper.pop(); return [...lower, ...upper]; }
function buildHullPath(nodes: RenderNode[]): string | null { if (!nodes.length) return null; const expandedPoints = nodes.flatMap((node) => { const samples = 10; const padding = node.type === 'workspace' ? 28 : 18; const radius = node.radius + padding; return Array.from({ length: samples }, (_, index) => { const angle = (Math.PI * 2 * index) / samples; return { x: node.x + Math.cos(angle) * radius, y: node.y + Math.sin(angle) * radius }; }); }); const hull = createConvexHull(expandedPoints); return hull.length < 3 ? null : `M ${hull.map((point) => `${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' L ')} Z`; }
function resolveNode(nodeRef: string | RenderNode, nodeLookup: Map<string, RenderNode>): RenderNode | null { return typeof nodeRef === 'string' ? nodeLookup.get(nodeRef) || null : nodeRef; }
function buildArcPath(edge: RenderEdge, nodeLookup: Map<string, RenderNode>): string | null { const source = resolveNode(edge.source, nodeLookup); const target = resolveNode(edge.target, nodeLookup); if (!source || !target) return null; if (source.id === target.id) { const loopRadius = source.radius + 20; return [`M ${source.x} ${source.y}`, `C ${source.x + loopRadius} ${source.y - loopRadius}`, `${source.x - loopRadius} ${source.y - loopRadius}`, `${source.x} ${source.y}`].join(' '); } const deltaX = target.x - source.x; const deltaY = target.y - source.y; const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY) || 1; const normalX = -deltaY / distance; const normalY = deltaX / distance; const pairOffset = (edge.pairIndex - (edge.pairCount - 1) / 2) * 24; const typeOffset = ((EDGE_DISTANCES[edge.type] || 140) - 140) * 0.16; const curve = pairOffset + typeOffset; const controlX = (source.x + target.x) / 2 + normalX * curve; const controlY = (source.y + target.y) / 2 + normalY * curve; return `M ${source.x} ${source.y} Q ${controlX} ${controlY} ${target.x} ${target.y}`; }
function shouldShowLabel(node: RenderNode, zoom: number, hoveredNodeId: string | null, selectedNodeId: string | null): boolean { return node.id === hoveredNodeId || node.id === selectedNodeId || zoom >= LABEL_ZOOM_THRESHOLD[node.type]; }
function parseNodeDate(node: KnowledgeGraphNode): number | null {
  const raw = node.metadata?.updated_at || node.metadata?.created_at;
  if (typeof raw !== 'string' || !raw) return null;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}
function matchesDateRange(node: KnowledgeGraphNode, dateFrom?: string, dateTo?: string): boolean {
  if (!dateFrom && !dateTo) return true;
  const timestamp = parseNodeDate(node);
  if (timestamp === null) return false;
  const from = dateFrom ? Date.parse(dateFrom) : null;
  const to = dateTo ? Date.parse(dateTo) : null;
  if (from !== null && Number.isFinite(from) && timestamp < from) return false;
  if (to !== null && Number.isFinite(to) && timestamp > (to + (24 * 60 * 60 * 1000) - 1)) return false;
  return true;
}
function getEdgeConfidence(edge: KnowledgeGraphEdge): number {
  const metadataConfidence = typeof edge.metadata?.confidence === 'number' ? edge.metadata.confidence : null;
  return Math.max(0, Math.min(1, metadataConfidence ?? edge.weight ?? 0));
}
function applyFilters(
  graph: KnowledgeGraph,
  filters: KnowledgeGraphFilters,
): { nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; clusters: KnowledgeGraphCluster[] } {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const clusters = graph.clusters || [];

  let nextNodes = [...nodes];
  let nextEdges = [...edges];
  let nextClusters = [...clusters];

  if (filters.nodeTypes.length > 0) {
    const allowedNodeTypes = new Set(filters.nodeTypes);
    nextNodes = nextNodes.filter((node) => allowedNodeTypes.has(node.type));
  }

  if (filters.dateFrom || filters.dateTo) {
    nextNodes = nextNodes.filter((node) => matchesDateRange(node, filters.dateFrom, filters.dateTo));
  }

  if (filters.clusterIds.length > 0) {
    const allowedClusterIds = new Set(filters.clusterIds);
    nextNodes = nextNodes.filter((node) => {
      const clusterId = typeof node.metadata?.cluster_id === 'string' ? node.metadata.cluster_id : undefined;
      const clusterKey = typeof node.metadata?.cluster_key === 'string' ? node.metadata.cluster_key : undefined;
      return Boolean((clusterId && allowedClusterIds.has(clusterId)) || (clusterKey && allowedClusterIds.has(clusterKey)));
    });
    nextClusters = nextClusters.filter((cluster) => allowedClusterIds.has(cluster.id) || allowedClusterIds.has(cluster.key));
  }

  if (filters.search.trim()) {
    const searchTerm = filters.search.trim().toLowerCase();
    nextNodes = nextNodes.filter((node) => getNodeMetaTokens(node).join(' ').toLowerCase().includes(searchTerm));
  }

  const allowedNodeIds = new Set(nextNodes.map((node) => node.id));
  nextEdges = nextEdges.filter((edge) => allowedNodeIds.has(edge.source) && allowedNodeIds.has(edge.target));

  if (filters.edgeTypes.length > 0) {
    const allowedEdgeTypes = new Set(filters.edgeTypes);
    nextEdges = nextEdges.filter((edge) => allowedEdgeTypes.has(edge.type));
  }

  nextEdges = nextEdges.filter((edge) => edge.weight >= filters.minWeight);

  if (filters.confidenceThreshold > 0) {
    nextEdges = nextEdges.filter((edge) => getEdgeConfidence(edge) >= filters.confidenceThreshold);
  }

  if (!filters.includeIsolated) {
    const connectedNodeIds = new Set(nextEdges.flatMap((edge) => [edge.source, edge.target]));
    nextNodes = nextNodes.filter((node) => connectedNodeIds.has(node.id));
  }

  const finalNodeIds = new Set(nextNodes.map((node) => node.id));
  nextEdges = nextEdges.filter((edge) => finalNodeIds.has(edge.source) && finalNodeIds.has(edge.target));
  nextClusters = nextClusters.filter((cluster) => cluster.node_ids.some((nodeId) => finalNodeIds.has(nodeId)));

  return { nodes: nextNodes, edges: nextEdges, clusters: nextClusters };
}

export function KnowledgeGraphView({ notes = EMPTY_NOTES }: KnowledgeGraphViewProps) {
  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;
  const filters = useKnowledgeGraphFilters();
  const viewportRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const simulationRef = useRef<any>(null);
  const zoomBehaviorRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const layoutRef = useRef<{ nodes: RenderNode[]; edges: RenderEdge[] }>({ nodes: [], edges: [] });
  const animationFrameRef = useRef<number | null>(null);
  const [rawGraphData, setRawGraphData] = useState<KnowledgeGraph>(EMPTY_GRAPH);
  const [selectedNode, setSelectedNode] = useState<KnowledgeGraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [zoomTransform, setZoomTransform] = useState<ZoomTransform>(zoomIdentity);
  const [viewportSize, setViewportSize] = useState({ width: 1100, height: 560 });
  const [, setLayoutTick] = useState(0);
  const [activeClusterKey, setActiveClusterKey] = useState<string | null>(null);
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [selectedNotePreviews, setSelectedNotePreviews] = useState<Note[]>([]);
  const [loadingSelectedNotes, setLoadingSelectedNotes] = useState(false);
  const [debouncedSearch, setDebouncedSearch] = useState(filters.search.trim());

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedSearch(filters.search.trim());
    }, 300);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [filters.search]);

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
      setRawGraphData(generateKnowledgeGraph(notes));
      return undefined;
    }

    let isMounted = true;
    const abortController = new AbortController();

    const loadGraph = async () => {
      try {
        setIsLoading(true);
        const response = await api.getKnowledgeGraph(workspaceId, undefined, { signal: abortController.signal, retries: false });
        if (isMounted) setRawGraphData(response);
      } catch (error) {
        if (abortController.signal.aborted) return;
        console.error('Failed to load knowledge graph:', error);
        if (isMounted) {
          setRawGraphData((current) => current.nodes.length ? current : generateKnowledgeGraph(notes));
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
  }, [workspaceId]);

  useEffect(() => {
    if (workspaceId) return;
    setRawGraphData(generateKnowledgeGraph(notes));
  }, [workspaceId, notes]);

  useEffect(() => {
    resetKnowledgeGraphFilters();
    setActiveClusterKey(null);
    setFocusNodeId(null);
    setSelectedNode(null);
    setZoomTransform(zoomIdentity);
  }, [workspaceId]);

  const graphData = useMemo(() => applyFilters(rawGraphData, { ...filters, search: debouncedSearch }), [rawGraphData, filters, debouncedSearch]);

  useEffect(() => {
    if (selectedNode && !graphData.nodes.some((node) => node.id === selectedNode.id)) setSelectedNode(null);
    if (focusNodeId && !graphData.nodes.some((node) => node.id === focusNodeId)) setFocusNodeId(null);
    if (activeClusterKey && !(graphData.clusters || []).some((cluster) => cluster.id === activeClusterKey || cluster.key === activeClusterKey)) setActiveClusterKey(null);
  }, [graphData.nodes, graphData.clusters, selectedNode, focusNodeId, activeClusterKey]);

  useEffect(() => {
    setKnowledgeGraphFilters((current) => {
      const nextClusterIds = activeClusterKey ? [activeClusterKey] : [];
      const unchanged =
        current.clusterIds.length === nextClusterIds.length &&
        current.clusterIds.every((value, index) => value === nextClusterIds[index]);
      return unchanged ? current : { ...current, clusterIds: nextClusterIds };
    });
  }, [activeClusterKey]);

  const allNodeLookup = useMemo(() => new Map((graphData.nodes || []).map((node) => [node.id, node])), [graphData.nodes]);

  const searchMatchedNodes = useMemo(() => {
    const nodes = graphData.nodes || [];
    const searchTerm = filters.search.trim().toLowerCase();
    if (!searchTerm) return nodes;
    return nodes.filter((node) => getNodeMetaTokens(node).join(' ').toLowerCase().includes(searchTerm));
  }, [graphData.nodes, filters.search]);

  const searchMatchedNodeIds = useMemo(() => new Set(searchMatchedNodes.map((node) => node.id)), [searchMatchedNodes]);

  const searchMatchedEdges = useMemo(() => {
    return (graphData.edges || []).filter((edge) => searchMatchedNodeIds.has(edge.source) && searchMatchedNodeIds.has(edge.target));
  }, [graphData.edges, searchMatchedNodeIds]);

  const clusterScopedNodeIds = useMemo(() => {
    if (!activeClusterKey) return searchMatchedNodeIds;
    const cluster = (graphData.clusters || []).find((item) => item.id === activeClusterKey || item.key === activeClusterKey);
    if (!cluster) return searchMatchedNodeIds;
    return new Set(cluster.node_ids.filter((nodeId) => searchMatchedNodeIds.has(nodeId)));
  }, [activeClusterKey, graphData.clusters, searchMatchedNodeIds]);

  const clusterScopedNodes = useMemo(() => searchMatchedNodes.filter((node) => clusterScopedNodeIds.has(node.id)), [searchMatchedNodes, clusterScopedNodeIds]);

  const clusterScopedEdges = useMemo(() => {
    return searchMatchedEdges.filter((edge) => clusterScopedNodeIds.has(edge.source) && clusterScopedNodeIds.has(edge.target));
  }, [searchMatchedEdges, clusterScopedNodeIds]);

  const displayNodeIds = useMemo(() => {
    if (!focusNodeId) return clusterScopedNodeIds;
    const ids = new Set<string>();
    if (clusterScopedNodeIds.has(focusNodeId)) ids.add(focusNodeId);
    for (const edge of clusterScopedEdges) {
      if (edge.source === focusNodeId || edge.target === focusNodeId) {
        ids.add(edge.source);
        ids.add(edge.target);
      }
    }
    return ids.size ? ids : clusterScopedNodeIds;
  }, [focusNodeId, clusterScopedEdges, clusterScopedNodeIds]);

  const filteredNodes = useMemo(() => clusterScopedNodes.filter((node) => displayNodeIds.has(node.id)), [clusterScopedNodes, displayNodeIds]);

  const filteredEdges = useMemo(() => {
    return clusterScopedEdges.filter((edge) => displayNodeIds.has(edge.source) && displayNodeIds.has(edge.target));
  }, [clusterScopedEdges, displayNodeIds]);

  const filteredClusters = useMemo(() => {
    return (graphData.clusters || []).filter((cluster) => cluster.node_ids.some((nodeId) => displayNodeIds.has(nodeId)));
  }, [graphData.clusters, displayNodeIds]);

  const clusterLookup = useMemo(() => {
    const lookup = new Map<string, KnowledgeGraphCluster>();
    for (const cluster of filteredClusters) {
      lookup.set(cluster.id, cluster);
      lookup.set(cluster.key, cluster);
    }
    return lookup;
  }, [filteredClusters]);

  const activeCluster = useMemo(() => filteredClusters.find((cluster) => cluster.id === activeClusterKey || cluster.key === activeClusterKey) || null, [filteredClusters, activeClusterKey]);

  const selectedRelationships = useMemo(() => {
    if (!selectedNode) return [] as NodeRelationship[];
    return clusterScopedEdges.flatMap((edge): NodeRelationship[] => {
      if (edge.source === selectedNode.id) {
        const node = allNodeLookup.get(edge.target);
        return node ? [{ edge, node, direction: 'outgoing' }] : [];
      }
      if (edge.target === selectedNode.id) {
        const node = allNodeLookup.get(edge.source);
        return node ? [{ edge, node, direction: 'incoming' }] : [];
      }
      return [];
    }).sort((left, right) => right.edge.weight - left.edge.weight);
  }, [selectedNode, clusterScopedEdges, allNodeLookup]);

  const relationshipCounts = useMemo(() => {
    return selectedRelationships.reduce<Record<string, number>>((accumulator, relationship) => {
      accumulator[relationship.edge.type] = (accumulator[relationship.edge.type] || 0) + 1;
      return accumulator;
    }, {});
  }, [selectedRelationships]);

  useEffect(() => {
    let active = true;
    const noteIds = selectedNode ? getNodeNoteIds(selectedNode).slice(0, 4) : [];
    const isMicroZoom = zoomTransform.k >= ZOOM_PRESETS.micro;

    if (!selectedNode || !noteIds.length || !isMicroZoom) {
      setSelectedNotePreviews([]);
      setLoadingSelectedNotes(false);
      return undefined;
    }

    const existingNotes = notes.filter((note) => noteIds.includes(note.id)).slice(0, 4);
    if (existingNotes.length === noteIds.length || !workspaceId) {
      setSelectedNotePreviews(existingNotes);
      setLoadingSelectedNotes(false);
      return undefined;
    }

    const loadNotes = async () => {
      try {
        setLoadingSelectedNotes(true);
        const missingNoteIds = noteIds.filter((noteId) => !existingNotes.some((note) => note.id === noteId));
        const results = await Promise.allSettled(missingNoteIds.map((noteId) => api.getNote(noteId)));
        if (!active) return;
        const loadedNotes = results.flatMap((result) => result.status === 'fulfilled' ? [transformNoteFromAPI(result.value)] : []);
        const mergedNotes = [...existingNotes, ...loadedNotes].sort((left, right) => noteIds.indexOf(left.id) - noteIds.indexOf(right.id)).slice(0, 4);
        setSelectedNotePreviews(mergedNotes);
      } catch (error) {
        if (!active) return;
        console.error('Failed to load graph note previews:', error);
        setSelectedNotePreviews(existingNotes);
      } finally {
        if (active) setLoadingSelectedNotes(false);
      }
    };

    loadNotes();
    return () => { active = false; };
  }, [selectedNode, notes, workspaceId, zoomTransform.k]);

  const connectedNotes = selectedNotePreviews;

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
    { label: 'Nodes', value: filteredNodes.length },
    { label: 'Connections', value: filteredEdges.length },
    { label: 'Clusters', value: filteredClusters.length },
    { label: 'Notes', value: filteredNodes.filter((node) => node.type === 'note').length },
  ];
  const zoom = zoomTransform.k;
  const zoomMode: ZoomMode = zoom >= ZOOM_PRESETS.micro ? 'micro' : zoom >= ZOOM_PRESETS.meso ? 'meso' : 'macro';
  const isMicroZoom = zoomMode === 'micro';
  const totalNodeCount = rawGraphData.nodes.length;

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;

    const zoomBehavior = (createZoom() as ZoomBehavior<SVGSVGElement, unknown>)
      .scaleExtent([0.35, 2.8])
      .on('zoom', (event: { transform: ZoomTransform }) => {
        setZoomTransform(event.transform);
      });

    zoomBehaviorRef.current = zoomBehavior;
    const selection = select(svg);
    selection.call(zoomBehavior);
    selection.call(zoomBehavior.transform, zoomIdentity);

    return () => {
      selection.on('.zoom', null);
      zoomBehaviorRef.current = null;
    };
  }, []);

  const applyZoomTransform = (nextTransform: ZoomTransform) => {
    const svg = svgRef.current;
    const zoomBehavior = zoomBehaviorRef.current;
    if (!svg || !zoomBehavior) {
      setZoomTransform(nextTransform);
      return;
    }

    select(svg).call(zoomBehavior.transform, nextTransform);
  };

  const resetAllControls = () => {
    resetKnowledgeGraphFilters();
    setActiveClusterKey(null);
    setFocusNodeId(null);
    setSelectedNode(null);
    applyZoomTransform(zoomIdentity);
  };

  const jumpToZoomPreset = (mode: ZoomMode) => {
    const nextTransform = zoomIdentity
      .translate(mode === 'macro' ? 0 : zoomTransform.x, mode === 'macro' ? 0 : zoomTransform.y)
      .scale(ZOOM_PRESETS[mode]);
    applyZoomTransform(nextTransform);
  };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Workspace Graph Explorer</span>
          </div>
          <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 44, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase' }}><span style={{ color: TT.yolk }}>K</span>NOWLEDGE GRAPH</h1>
          <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <div style={{ position: 'relative' }}>
          <Search size={12} color={TT.inkMuted} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)' }} />
            <input value={filters.search} onChange={(event) => setKnowledgeGraphFilters((current) => ({ ...current, search: event.target.value }))} placeholder="Find nodes, clusters, tags..." style={{ height: 38, width: 260, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.02em', paddingLeft: 30, paddingRight: 12, outline: 'none', boxSizing: 'border-box' }} onFocus={(event) => { (event.target as HTMLInputElement).style.borderColor = TT.yolk; }} onBlur={(event) => { (event.target as HTMLInputElement).style.borderColor = TT.inkBorder; }} />
          </div>
          <button onClick={resetAllControls} style={{ height: 38, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '0 14px', cursor: 'pointer' }}>Reset View</button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2.1fr 1fr', gap: 12, marginBottom: 20 }}>
        <div style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: 16 }}>
          <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 12 }}>Graph Controls</div>
          <div style={{ display: 'grid', gap: 14 }}>
            <div>
              <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Node Types</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {(Object.keys(NODE_TYPE_LABELS) as KnowledgeGraphNode['type'][]).map((type) => <FilterChip key={type} label={`${NODE_TYPE_LABELS[type]} (${rawGraphData.stats.node_types[type] || 0})`} active={filters.nodeTypes.includes(type)} color={NODE_COLORS[type]} onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, nodeTypes: toggleValue(current.nodeTypes, type) }))} />)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Relationship Types</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {(Object.keys(EDGE_TYPE_LABELS) as KnowledgeGraphEdge['type'][]).map((type) => <FilterChip key={type} label={`${EDGE_TYPE_LABELS[type]} (${rawGraphData.stats.edge_types[type] || 0})`} active={filters.edgeTypes.includes(type)} color={EDGE_COLORS[type]} onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, edgeTypes: toggleValue(current.edgeTypes, type) }))} />)}
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr', gap: 12, alignItems: 'end' }}>
              <div>
                <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Minimum Edge Weight</div>
                <input type="range" min={0} max={1} step={0.05} value={filters.minWeight} onChange={(event) => setKnowledgeGraphFilters((current) => ({ ...current, minWeight: Number(event.target.value) }))} style={{ width: '100%' }} />
                <div style={{ fontSize: 10, color: TT.snow, marginTop: 6 }}>{filters.minWeight.toFixed(2)}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Confidence Threshold</div>
                <input type="range" min={0} max={1} step={0.05} value={filters.confidenceThreshold} onChange={(event) => setKnowledgeGraphFilters((current) => ({ ...current, confidenceThreshold: Number(event.target.value) }))} style={{ width: '100%' }} />
                <div style={{ fontSize: 10, color: TT.snow, marginTop: 6 }}>{filters.confidenceThreshold.toFixed(2)}</div>
              </div>
              <button onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, includeIsolated: !current.includeIsolated }))} style={{ height: 38, borderRadius: 3, border: `1px solid ${filters.includeIsolated ? TT.yolk : TT.inkBorder}`, background: filters.includeIsolated ? 'rgba(245,230,66,0.08)' : TT.inkRaised, color: filters.includeIsolated ? TT.snow : TT.inkMuted, fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', cursor: 'pointer' }}>{filters.includeIsolated ? 'Including Isolated' : 'Only Connected'}</button>
              <button onClick={() => setFocusNodeId((current) => (selectedNode && current !== selectedNode.id ? selectedNode.id : null))} disabled={!selectedNode} style={{ height: 38, borderRadius: 3, border: `1px solid ${focusNodeId ? TT.yolk : TT.inkBorder}`, background: focusNodeId ? 'rgba(245,230,66,0.08)' : TT.inkRaised, color: selectedNode ? TT.snow : TT.inkMid, fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', cursor: selectedNode ? 'pointer' : 'not-allowed' }}>{focusNodeId ? 'Clear Focus' : 'Focus Selection'}</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1.2fr', gap: 12, alignItems: 'end' }}>
              <div>
                <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Updated From</div>
                <input type="date" value={filters.dateFrom || ''} onChange={(event) => setKnowledgeGraphFilters((current) => ({ ...current, dateFrom: event.target.value || undefined }))} style={{ width: '100%', height: 38, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 11, padding: '0 10px', boxSizing: 'border-box' }} />
              </div>
              <div>
                <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Updated To</div>
                <input type="date" value={filters.dateTo || ''} onChange={(event) => setKnowledgeGraphFilters((current) => ({ ...current, dateTo: event.target.value || undefined }))} style={{ width: '100%', height: 38, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 11, padding: '0 10px', boxSizing: 'border-box' }} />
              </div>
              <div style={{ alignSelf: 'stretch', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
                <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Visible Nodes</div>
                <div style={{ height: 38, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, background: TT.inkRaised, color: TT.snow, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 12px', fontSize: 10.5 }}>
                  <span>{filteredNodes.length}</span>
                  <span style={{ color: TT.inkMuted }}>/ {totalNodeCount}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: 16 }}>
          <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 12 }}>Cluster Lens</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: activeCluster ? 12 : 0 }}>
            {filteredClusters.slice(0, 8).map((cluster) => <FilterChip key={cluster.id} label={`${cluster.label} (${cluster.node_count})`} active={activeClusterKey === cluster.id || activeClusterKey === cluster.key} color={getClusterColor(cluster.key || cluster.id, TT.yolk)} onClick={() => setActiveClusterKey((current) => current === cluster.id || current === cluster.key ? null : cluster.id)} />)}
          </div>
          {activeCluster ? <div><div style={{ fontSize: 12, color: TT.snow, marginBottom: 6 }}>{activeCluster.label}</div><div style={{ fontSize: 10, lineHeight: 1.5, color: TT.inkMuted }}>{activeCluster.description}</div></div> : <div style={{ fontSize: 10, lineHeight: 1.5, color: TT.inkMuted }}>Click a cluster to isolate a theme, then focus on a node to inspect its immediate neighborhood.</div>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 20 }}>
        {stats.map(({ label, value }) => <div key={label} style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '12px 16px' }}><div style={{ fontFamily: TT.fontDisplay, fontSize: 32, color: TT.snow, letterSpacing: '0.02em', lineHeight: 1 }}>{value}</div><div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginTop: 3 }}>{label}</div></div>)}
      </div>

      <div ref={viewportRef} style={{ position: 'relative', background: `radial-gradient(circle at 20% 20%, rgba(245,230,66,0.08), transparent 26%), linear-gradient(135deg, ${TT.inkDeep}, #171717 60%, #101010)`, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, overflow: 'hidden', height: 560 }}>
        {isLoading && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(10,10,10,0.6)', zIndex: 20, fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Loading graph...</div>}
        {!isLoading && !renderedNodes.length && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10, color: TT.inkMuted, fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase' }}>No graph nodes match the current workspace and filters</div>}
        <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', flexDirection: 'column', gap: 4, zIndex: 10 }}>
          <IconBtn onClick={() => applyZoomTransform(zoomIdentity.translate(zoomTransform.x, zoomTransform.y).scale(Math.min(zoom * 1.2, 2.8)))} title="Zoom in"><ZoomIn size={14} /></IconBtn>
          <IconBtn onClick={() => applyZoomTransform(zoomIdentity.translate(zoomTransform.x, zoomTransform.y).scale(Math.max(zoom / 1.2, 0.35)))} title="Zoom out"><ZoomOut size={14} /></IconBtn>
          <IconBtn onClick={() => applyZoomTransform(zoomIdentity)} title="Reset"><Maximize2 size={14} /></IconBtn>
          <div style={{ marginTop: 6, display: 'grid', gap: 4 }}>
            {(['macro', 'meso', 'micro'] as ZoomMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => jumpToZoomPreset(mode)}
                style={{
                  height: 28,
                  background: zoomMode === mode ? 'rgba(245,230,66,0.12)' : TT.inkRaised,
                  border: `1px solid ${zoomMode === mode ? TT.yolk : TT.inkBorder}`,
                  borderRadius: 3,
                  color: zoomMode === mode ? TT.snow : TT.inkMuted,
                  fontFamily: TT.fontMono,
                  fontSize: 9,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  cursor: 'pointer',
                  padding: '0 8px',
                }}
              >
                {mode}
              </button>
            ))}
          </div>
        </div>
        <div style={{ position: 'absolute', bottom: 12, left: 12, background: 'rgba(10,10,10,0.85)', border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '10px 14px', zIndex: 10, maxWidth: 280 }}>
          <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Active Graph Context</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
            {filters.nodeTypes.length > 0 && <FilterChip label={`${filters.nodeTypes.length} node filters`} active color={TT.yolk} onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, nodeTypes: [] }))} />}
            {filters.edgeTypes.length > 0 && <FilterChip label={`${filters.edgeTypes.length} edge filters`} active color={TT.yolk} onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, edgeTypes: [] }))} />}
            {activeCluster && <FilterChip label={activeCluster.label} active color={getClusterColor(activeCluster.key, TT.yolk)} onClick={() => setActiveClusterKey(null)} />}
            {focusNodeId && <FilterChip label="Focused neighborhood" active color={TT.yolk} onClick={() => setFocusNodeId(null)} />}
          </div>
          <div style={{ display: 'grid', gap: 6 }}>
            {[{ label: 'Workspace nodes', value: rawGraphData.stats.node_types.workspace || 0 }, { label: 'Suggested links', value: rawGraphData.stats.edge_types.note_related_note || 0 }, { label: 'Min edge weight', value: filters.minWeight.toFixed(2) }].map((item) => <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><span style={{ fontSize: 9.5, letterSpacing: '0.03em', color: TT.inkMuted }}>{item.label}</span><span style={{ fontSize: 9.5, letterSpacing: '0.03em', color: TT.snow }}>{item.value}</span></div>)}
          </div>
        </div>
        <div style={{ position: 'absolute', top: 12, left: 12, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.06em', color: TT.inkMid, textTransform: 'uppercase' }}>{Math.round(zoom * 100)}% | {zoomMode}</div>
        <svg ref={svgRef} style={{ width: '100%', height: '100%', cursor: 'grab' }} viewBox={`0 0 ${viewportSize.width} ${viewportSize.height}`}>
          <defs>
            <filter id="node-glow">
              <feGaussianBlur stdDeviation="5" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            {Object.entries(EDGE_COLORS).map(([type, color]) => <marker key={type} id={`arrow-${type}`} markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M 0 0 L 10 5 L 0 10 z" fill={color} /></marker>)}
          </defs>
          <g transform={`translate(${zoomTransform.x}, ${zoomTransform.y}) scale(${zoomTransform.k})`}>
            {clusterHulls.map((hull) => <g key={`hull-${hull.key}`} data-cluster-hull="true" onClick={() => setActiveClusterKey((current) => current === hull.key ? null : hull.key)} style={{ cursor: 'pointer' }}><path d={hull.path || ''} fill={hull.color} opacity={activeClusterKey === hull.key ? 0.14 : 0.08} stroke={hull.color} strokeWidth={activeClusterKey === hull.key ? 1.6 : 1.2} strokeOpacity={activeClusterKey === hull.key ? 0.42 : 0.28} />{zoom >= 0.75 && <text x={hull.center.x} y={hull.center.y} textAnchor="middle" fill={hull.color} fontSize={10} fontFamily={TT.fontMono} letterSpacing="0.08em" style={{ pointerEvents: 'none', textTransform: 'uppercase' }}>{hull.label}</text>}</g>)}
            {renderedEdges.map((edge) => {
              const path = buildArcPath(edge, nodeLookup);
              if (!path) return null;
              const sourceId = typeof edge.source === 'string' ? edge.source : edge.source.id;
              const targetId = typeof edge.target === 'string' ? edge.target : edge.target.id;
              const isHighlighted = hoveredNode === sourceId || hoveredNode === targetId || selectedNode?.id === sourceId || selectedNode?.id === targetId;
              return <path key={edge.id} d={path} fill="none" stroke={EDGE_COLORS[edge.type]} strokeWidth={isHighlighted ? 2.1 : 1.35} strokeOpacity={(hoveredNode || selectedNode) && !isHighlighted ? 0.1 : 0.72} markerEnd={`url(#arrow-${edge.type})`} />;
            })}
            {renderedNodes.map((node) => {
              const isHovered = hoveredNode === node.id;
              const isSelected = selectedNode?.id === node.id;
              const showLabel = shouldShowLabel(node, zoom, hoveredNode, selectedNode?.id || null);
              const nodeColor = getNodeColor(node);
              return (
                <g key={node.id} data-graph-node="true" transform={`translate(${node.x}, ${node.y})`} style={{ cursor: 'pointer' }} onMouseEnter={() => setHoveredNode(node.id)} onMouseLeave={() => setHoveredNode(null)} onClick={() => setSelectedNode(node)}>
                  {isSelected && <><circle r={node.radius + 10} fill="none" stroke={TT.yolk} strokeWidth={1.3} opacity={0.42} filter="url(#node-glow)" /><circle r={node.radius + 17} fill="none" stroke={TT.yolk} strokeWidth={0.9} opacity={0.18} /></>}
                  <circle r={node.radius} fill={nodeColor} opacity={(hoveredNode || selectedNode) && !isHovered && !isSelected ? 0.28 : 1} stroke={isSelected ? TT.snow : 'rgba(255,255,255,0.08)'} strokeWidth={isSelected ? 2 : 1} filter={isHovered ? 'url(#node-glow)' : undefined} />
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: getNodeColor(selectedNode), boxShadow: `0 0 8px ${getNodeColor(selectedNode)}80` }} />
            <span style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow }}>{selectedNode.label.toUpperCase()}</span>
            <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(245,230,66,0.08)', color: TT.yolk, border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2 }}>{getNodeNoteIds(selectedNode).length} notes</span>
            {typeof selectedNode.metadata?.cluster_label === 'string' && <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(255,255,255,0.05)', color: TT.snow, border: `1px solid ${TT.inkBorder}`, borderRadius: 2 }}>{selectedNode.metadata.cluster_label}</span>}
          </div>
          <button onClick={() => setSelectedNode(null)} style={{ background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2, cursor: 'pointer', padding: '4px 6px', color: TT.inkMuted, transition: 'all 0.15s' }} onMouseEnter={(event) => { (event.currentTarget as HTMLElement).style.color = TT.error; (event.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)'; }} onMouseLeave={(event) => { (event.currentTarget as HTMLElement).style.color = TT.inkMuted; (event.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}><X size={12} /></button>
          </div>
          <p style={{ fontSize: 10.5, letterSpacing: '0.04em', color: TT.inkMuted, marginBottom: 14 }}>{getNodeDescription(selectedNode)}</p>
          {typeof selectedNode.metadata?.cluster_description === 'string' && <p style={{ fontSize: 10, letterSpacing: '0.04em', color: TT.snow, marginBottom: 14 }}>{selectedNode.metadata.cluster_description}</p>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
            <FilterChip label={focusNodeId === selectedNode.id ? 'Clear Neighborhood Focus' : 'Focus Neighborhood'} active={focusNodeId === selectedNode.id} color={TT.yolk} onClick={() => setFocusNodeId((current) => current === selectedNode.id ? null : selectedNode.id)} />
            {typeof selectedNode.metadata?.cluster_id === 'string' && <FilterChip label={activeClusterKey === selectedNode.metadata.cluster_id ? 'Clear Cluster Filter' : 'Filter To Cluster'} active={activeClusterKey === selectedNode.metadata.cluster_id} color={getClusterColor(String(selectedNode.metadata.cluster_key || selectedNode.metadata.cluster_id), TT.yolk)} onClick={() => setActiveClusterKey((current) => current === selectedNode.metadata.cluster_id ? null : String(selectedNode.metadata.cluster_id))} />}
            <FilterChip label="Search This Label" active={filters.search.trim().toLowerCase() === selectedNode.label.toLowerCase()} color={getNodeColor(selectedNode)} onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, search: current.search === selectedNode.label ? '' : selectedNode.label }))} />
          </div>
          {Object.keys(relationshipCounts).length > 0 && <div style={{ marginBottom: 12 }}><div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Relationship Signals</div><div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{Object.entries(relationshipCounts).map(([edgeType, count]) => <FilterChip key={edgeType} label={`${EDGE_TYPE_LABELS[edgeType as KnowledgeGraphEdge['type']]} (${count})`} active={filters.edgeTypes.includes(edgeType as KnowledgeGraphEdge['type'])} color={EDGE_COLORS[edgeType as KnowledgeGraphEdge['type']]} onClick={() => setKnowledgeGraphFilters((current) => ({ ...current, edgeTypes: toggleValue(current.edgeTypes, edgeType as KnowledgeGraphEdge['type']) }))} />)}</div></div>}
          {selectedRelationships.length > 0 && <div style={{ marginBottom: 14 }}><div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Related Nodes</div><div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>{selectedRelationships.slice(0, 8).map((relationship) => <button key={relationship.edge.id} onClick={() => setSelectedNode(relationship.node)} style={{ width: '100%', textAlign: 'left', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px', cursor: 'pointer' }}><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 4 }}><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: getNodeColor(relationship.node) }} /><span style={{ fontSize: 11, color: TT.snow }}>{relationship.node.label}</span></div><span style={{ fontSize: 9, color: TT.inkMuted }}>{relationship.edge.weight.toFixed(2)}</span></div><div style={{ fontSize: 9, color: TT.inkMuted, lineHeight: 1.45 }}>{getEdgeDescription(relationship.edge.type)}</div></button>)}</div></div>}
          {!isMicroZoom && getNodeNoteIds(selectedNode).length > 0 && <div style={{ marginBottom: 10, padding: '10px 12px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, fontSize: 10, color: TT.inkMuted, letterSpacing: '0.03em' }}>Jump to <span style={{ color: TT.yolk }}>micro</span> zoom to load note previews for this node.</div>}
          {isMicroZoom && loadingSelectedNotes && <div style={{ fontSize: 10, color: TT.inkMuted, marginBottom: 10 }}>Loading note context...</div>}
          {isMicroZoom && connectedNotes.length > 0 && <>
            <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>Node Detail Preview</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
              {connectedNotes.slice(0, 4).map((note) => <div key={note.id} style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px' }}><p style={{ fontFamily: TT.fontMono, fontSize: 11.5, color: TT.snow, marginBottom: 4, letterSpacing: '0.02em', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{note.title}</p>{(note.tags ?? []).length > 0 && <p style={{ fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.03em', marginBottom: 6 }}>{(note.tags ?? []).slice(0, 3).join(' | ')}</p>}<p style={{ fontSize: 10, lineHeight: 1.5, color: TT.snow, opacity: 0.85 }}>{note.content.slice(0, 160)}{note.content.length > 160 ? '...' : ''}</p></div>)}
            </div>
          </>}
        </motion.div>
      )}
    </div>
  );
}
