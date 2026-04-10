export interface User {
  id: string;
  email: string;
  name?: string;
  full_name?: string;
  avatar?: string;
  plan?: 'free' | 'pro' | 'team' | 'enterprise';
  is_active?: boolean;
  created_at?: string;
}

export interface Note {
  id: string;
  title: string;
  content: string;
  summary?: string;
  tags: string[];
  connections: string[];
  userId: string;
  workspaceId?: string;
  createdAt: Date;
  updatedAt: Date;
  confidence?: number;
  source?: string;
  type: 'note' | 'web-clip' | 'document' | 'voice' | 'ai-generated';
  word_count?: number;
  embedding?: string;
}

export type GraphNodeType = 'workspace' | 'note' | 'entity' | 'tag';

export type GraphEdgeType =
  | 'workspace_contains_note'
  | 'note_mentions_entity'
  | 'note_has_tag'
  | 'note_links_note'
  | 'note_related_note'
  | 'entity_co_occurs_with_entity'
  | 'tag_co_occurs_with_tag';

export interface KnowledgeGraphNodeMetadata {
  workspace_id?: string;
  note_id?: string;
  note_ids: string[];
  note_type?: string;
  tags?: string[];
  updated_at?: string | null;
  entity_type?: string;
  tag_source?: string;
  [key: string]: unknown;
}

export interface KnowledgeGraphEdgeMetadata {
  shared_signals?: number;
  [key: string]: unknown;
}

export interface KnowledgeGraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  value: number;
  metadata: KnowledgeGraphNodeMetadata;
}

export interface KnowledgeGraphEdge {
  id: string;
  source: string;
  target: string;
  type: GraphEdgeType;
  weight: number;
  metadata: KnowledgeGraphEdgeMetadata;
}

export interface KnowledgeGraphStats {
  total_nodes: number;
  total_edges: number;
  node_types: Partial<Record<GraphNodeType, number>>;
  edge_types: Partial<Record<GraphEdgeType, number>>;
}

export interface KnowledgeGraphFilters {
  nodeTypes: GraphNodeType[];
  edgeTypes: GraphEdgeType[];
  search: string;
  minWeight: number;
  includeIsolated: boolean;
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  stats: KnowledgeGraphStats;
}

export interface Workspace {
  id: string;
  name: string;
  description?: string;
  members: WorkspaceMember[];
  createdAt: Date;
  updatedAt: Date;
}

export interface WorkspaceMember {
  userId: string;
  role: 'owner' | 'admin' | 'member';
  joinedAt: Date;
}

export interface SearchResult {
  chunk_id: string;
  document_id: string;
  document_title: string;
  text: string;
  similarity: number;
}

export interface SearchDisplayResult {
  note: Note;
  score: number;
  highlights: string[];
  matchedNodes?: string[];
}

export interface AIInsight {
  id: string;
  type: 'connection' | 'summary' | 'suggestion' | 'trend';
  content: string;
  sources: string[];
  confidence: number;
  createdAt: Date;
}

export interface Workflow {
  id: string;
  name: string;
  trigger: WorkflowTrigger;
  actions: WorkflowAction[];
  isActive: boolean;
  createdAt: Date;
}

export interface WorkflowTrigger {
  type: 'note-created' | 'tag-added' | 'scheduled' | 'webhook';
  config: Record<string, any>;
}

export interface WorkflowAction {
  type: 'send-notification' | 'create-task' | 'export' | 'webhook' | 'ai-summarize';
  config: Record<string, any>;
}

export interface PricingPlan {
  id: string;
  name: string;
  description: string;
  price: number;
  priceUnit: string;
  features: string[];
  limitations: string[];
  highlighted?: boolean;
  cta: string;
}

export interface QueryRequest {
  workspace_id: string;
  query: string;
  top_k?: number;
  model?: string;
}

export interface QueryResponse {
  query_id: string;
  answer: string;
  confidence: number;
  confidence_factors?: {
    similarity_avg?: number;
    document_diversity?: number;
    source_coverage?: number;
  };
  sources: SearchResult[];
  model_used: string;
  tokens_used: number;
  response_time_ms: number;
}

export interface FeedbackRequest {
  rating: number;
  comment?: string;
}

export interface FeedbackResponse {
  feedback_id: string;
  message: string;
}

export interface Document {
  id: string;
  workspaceId: string;
  title: string;
  sourceType: 'upload' | 'slack' | 'notion' | 'google' | 'github' | 'web';
  status: 'pending' | 'processing' | 'indexed' | 'failed';
  tokenCount: number;
  chunkCount: number;
  storageUrl?: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface Chunk {
  id: string;
  documentId: string;
  chunkIndex: number;
  text: string;
  tokenCount: number;
  contextBefore?: string;
  contextAfter?: string;
  metadata?: Record<string, any>;
}
