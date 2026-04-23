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
  access?: NoteAccess;
  approvalStatus?: ApprovalWorkflowStatus;
  approvalPriority?: ApprovalWorkflowPriority;
  approvalDueAt?: Date;
  approvalSubmittedAt?: Date;
  approvalSubmittedByUserId?: string;
  approvalDecidedAt?: Date;
  approvalDecidedByUserId?: string;
}

export interface NoteAccess {
  noteId: string;
  accessSource: string;
  canView: boolean;
  canUpdate: boolean;
  canDelete: boolean;
  canManage: boolean;
  collaboratorRole?: 'viewer' | 'editor';
}

export interface NoteInvite {
  id: string;
  noteId: string;
  inviterUserId?: string;
  inviteeEmail?: string;
  inviteeUserId?: string;
  role: 'viewer' | 'editor';
  status: 'pending' | 'accepted' | 'revoked' | 'expired';
  expiresAt: Date;
  acceptedAt?: Date;
  revokedAt?: Date;
  message?: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface NoteContributionBreakdown {
  noteCreated: number;
  noteUpdated: number;
  noteRestored: number;
  thinkingContributions: number;
  votesCast: number;
}

export interface NoteContribution {
  noteId: string;
  workspaceId?: string;
  contributorUserId: string;
  contributorName?: string;
  contributorEmail?: string;
  contributionCount: number;
  breakdown: NoteContributionBreakdown;
  firstContributionAt?: Date;
  lastContributionAt?: Date;
}

export type NoteCommentReactionType =
  | 'thumbs_up'
  | 'heart'
  | 'laugh'
  | 'hooray'
  | 'eyes'
  | 'rocket';

export interface NoteCommentUser {
  id: string;
  email: string;
  name: string;
}

export interface NoteCommentMention {
  id: string;
  commentId: string;
  mentionedUserId: string;
  mentionToken: string;
  startOffset: number;
  endOffset: number;
  user?: NoteCommentUser | null;
}

export interface NoteCommentReactionSummary {
  emoji: NoteCommentReactionType;
  emojiValue: string;
  count: number;
  reactedByCurrentUser: boolean;
}

export interface NoteComment {
  id: string;
  noteId: string;
  authorUserId: string;
  parentCommentId?: string;
  depth: number;
  body: string;
  isResolved: boolean;
  resolvedByUserId?: string;
  resolvedAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
  author?: NoteCommentUser | null;
  resolvedBy?: NoteCommentUser | null;
  mentions: NoteCommentMention[];
  reactions: NoteCommentReactionSummary[];
  replies: NoteComment[];
}

export type ApprovalWorkflowStatus =
  | 'draft'
  | 'submitted'
  | 'needs_changes'
  | 'approved'
  | 'rejected'
  | 'cancelled';

export type ApprovalWorkflowPriority = 'low' | 'normal' | 'high' | 'critical';

export interface ApprovalWorkflowAvailableActions {
  submit: boolean;
  resubmit: boolean;
  cancel: boolean;
  approve: boolean;
  reject: boolean;
  request_changes: boolean;
}

export interface ApprovalWorkflowItem {
  noteId: string;
  workspaceId?: string;
  title: string;
  summary?: string;
  noteType: string;
  authorUserId: string;
  approvalStatus: ApprovalWorkflowStatus;
  approvalPriority: ApprovalWorkflowPriority;
  approvalDueAt?: Date;
  approvalSubmittedAt?: Date;
  approvalSubmittedByUserId?: string;
  approvalDecidedAt?: Date;
  approvalDecidedByUserId?: string;
  isOverdue: boolean;
  availableActions: ApprovalWorkflowAvailableActions;
  author?: NoteCommentUser | null;
  submittedBy?: NoteCommentUser | null;
  decidedBy?: NoteCommentUser | null;
}

export interface ApprovalWorkflowSummary {
  countsByStatus: Record<ApprovalWorkflowStatus, number>;
  total: number;
  overdue: number;
}

export interface ApprovalWorkflowTransition {
  id: string;
  noteId: string;
  workspaceId?: string;
  actorUserId?: string;
  fromStatus: ApprovalWorkflowStatus;
  toStatus: ApprovalWorkflowStatus;
  comment?: string;
  dueAtSnapshot?: Date;
  prioritySnapshot: ApprovalWorkflowPriority;
  createdAt?: Date;
  actor?: NoteCommentUser | null;
}

export type UserNotificationType =
  | 'automation'
  | 'comment_mention'
  | 'comment_reply'
  | 'approval_submitted'
  | 'approval_approved'
  | 'approval_rejected'
  | 'approval_needs_changes';

export interface UserNotification {
  id: string;
  userId: string;
  actorUserId?: string;
  workspaceId?: string;
  noteId?: string;
  commentId?: string;
  notificationType: UserNotificationType;
  payload: Record<string, unknown>;
  isRead: boolean;
  readAt?: Date;
  createdAt?: Date;
  actor?: NoteCommentUser | null;
}

export type NoteConnectionSuggestionStatus = 'pending' | 'confirmed' | 'dismissed';

export interface NoteConnectionSuggestion {
  id: string;
  workspaceId: string;
  noteId: string;
  suggestedNote: {
    id: string;
    title: string;
    contentPreview: string;
    tags: string[];
    createdAt: Date;
  };
  similarityScore: number;
  reason: string;
  status: NoteConnectionSuggestionStatus;
  metadata: Record<string, unknown>;
  respondedAt?: Date;
  createdAt: Date;
}

export interface NoteVersionDiffSegment {
  type: 'added' | 'deleted' | 'unchanged';
  text: string;
  wordCount: number;
}

export interface NoteVersion {
  id: string;
  noteId: string;
  workspaceId?: string;
  userId: string;
  versionNumber: number;
  changeReason: 'created' | 'updated' | 'restored' | string;
  restoredFromVersionId?: string;
  title: string;
  content: string;
  summary?: string;
  tags: string[];
  connections: string[];
  noteType: string;
  sourceUrl?: string;
  wordCount: number;
  diffSegments: NoteVersionDiffSegment[];
  metadata: Record<string, unknown>;
  createdAt: Date;
}

export type ThinkingSessionPhase =
  | 'waiting'
  | 'gathering'
  | 'synthesizing'
  | 'refining'
  | 'completed';

export type ThinkingSynthesisStatus =
  | 'pending'
  | 'streaming'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface ThinkingParticipant {
  id: string;
  userId: string;
  user?: {
    id: string;
    email: string;
    name: string;
  } | null;
  joinedAt?: Date;
  lastSeenAt?: Date;
}

export interface ThinkingContribution {
  id: string;
  sessionId: string;
  authorUserId: string;
  author?: {
    id: string;
    email: string;
    name: string;
  } | null;
  content: string;
  createdPhase: ThinkingSessionPhase;
  voteCount: number;
  voterUserIds: string[];
  rank: number;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface ThinkingSynthesisRun {
  id: string;
  sessionId: string;
  triggeredByUserId?: string;
  triggeredBy?: {
    id: string;
    email: string;
    name: string;
  } | null;
  status: ThinkingSynthesisStatus;
  model: string;
  contributionCount: number;
  outputText: string;
  errorMessage?: string | null;
  startedAt?: Date;
  completedAt?: Date;
  failedAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface ThinkingSession {
  id: string;
  workspaceId: string;
  noteId?: string | null;
  roomId: string;
  title: string;
  promptContext?: string | null;
  createdByUserId: string;
  hostUserId: string;
  creator?: {
    id: string;
    email: string;
    name: string;
  } | null;
  host?: {
    id: string;
    email: string;
    name: string;
  } | null;
  phase: ThinkingSessionPhase;
  phaseEnteredAt?: Date;
  waitingStartedAt?: Date;
  gatheringStartedAt?: Date;
  synthesizingStartedAt?: Date;
  refiningStartedAt?: Date;
  completedAt?: Date;
  activeSynthesisRunId?: string | null;
  synthesisOutput: string;
  refinedOutput: string;
  finalOutput: string;
  lastRefinedByUserId?: string | null;
  lastRefinedBy?: {
    id: string;
    email: string;
    name: string;
  } | null;
  createdAt?: Date;
  updatedAt?: Date;
  participants: ThinkingParticipant[];
  contributions: ThinkingContribution[];
  synthesisRuns: ThinkingSynthesisRun[];
  activeSynthesisRun?: ThinkingSynthesisRun | null;
}

export interface ThinkingSessionSummary {
  id: string;
  workspaceId: string;
  noteId?: string | null;
  roomId: string;
  title: string;
  phase: ThinkingSessionPhase;
  hostUserId: string;
  activeSynthesisRunId?: string | null;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface ThinkingSessionAccess {
  sessionId: string;
  workspaceId: string;
  roomId: string;
  canView: boolean;
  canParticipate: boolean;
  canControl: boolean;
  isHost: boolean;
  phase: ThinkingSessionPhase;
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
  cluster_id?: string;
  cluster_key?: string;
  cluster_label?: string;
  cluster_description?: string;
  cluster_score?: number;
  cluster_rank?: number;
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
  total_clusters: number;
  node_types: Partial<Record<GraphNodeType, number>>;
  edge_types: Partial<Record<GraphEdgeType, number>>;
}

export interface KnowledgeGraphFilters {
  nodeTypes: GraphNodeType[];
  edgeTypes: GraphEdgeType[];
  search: string;
  minWeight: number;
  includeIsolated: boolean;
  dateFrom?: string;
  dateTo?: string;
  clusterIds: string[];
  confidenceThreshold: number;
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  clusters: KnowledgeGraphCluster[];
  stats: KnowledgeGraphStats;
}

export interface KnowledgeGraphCluster {
  id: string;
  key: string;
  label: string;
  description: string;
  importance: number;
  node_ids: string[];
  node_count: number;
  metadata: Record<string, unknown>;
}

export interface GraphClusterInputNode {
  id: string;
  type: GraphNodeType;
  label: string;
  value: number;
  metadata: Record<string, unknown>;
  embedding: number[];
}

export interface GraphClusterInput {
  workspace_id: string;
  nodes: GraphClusterInputNode[];
  stats: {
    total_nodes: number;
    embeddable_nodes: number;
    embedding_dimension: number;
    [key: string]: unknown;
  };
}

export interface Workspace {
  id: string;
  name: string;
  description?: string;
  role?: 'owner' | 'admin' | 'member' | 'viewer';
  owner_id?: string;
  member_count?: number;
  members: WorkspaceMember[];
  createdAt: Date;
  updatedAt: Date;
  created_at?: string;
  updated_at?: string;
}

export type WorkspaceRole = 'owner' | 'admin' | 'member' | 'viewer';
export type WorkspacePermissionSection =
  | 'workspace'
  | 'settings'
  | 'members'
  | 'documents'
  | 'notes'
  | 'search'
  | 'knowledge_graph'
  | 'chat'
  | 'workflows';
export type WorkspacePermissionAction = 'view' | 'create' | 'update' | 'delete' | 'manage';

export interface WorkspacePermissionState {
  view: boolean;
  create: boolean;
  update: boolean;
  delete: boolean;
  manage: boolean;
}

export interface WorkspacePermissions {
  workspace_id: string;
  role: WorkspaceRole;
  permissions: Record<WorkspacePermissionSection, WorkspacePermissionState>;
}

export interface WorkspaceMember {
  userId: string;
  role: WorkspaceRole;
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
  workspaceId: string;
  triggerType: WorkflowTriggerType;
  conditions: WorkflowCondition[];
  actions: WorkflowAction[];
  isActive: boolean;
  createdAt: Date;
  updatedAt?: Date;
}

export type WorkflowTriggerType =
  | 'note.created'
  | 'note.updated'
  | 'note.deleted'
  | 'note.approval_submitted'
  | 'note.approval_approved'
  | 'note.approval_rejected'
  | 'note.approval_needs_changes'
  | 'document.completed'
  | 'thinking_session.completed'
  | 'webhook.received';

export type WorkflowConditionOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'not_contains'
  | 'matches_regex'
  | 'greater_than'
  | 'less_than'
  | 'exists';

export interface WorkflowCondition {
  path?: string;
  operator?: WorkflowConditionOperator;
  value?: unknown;
  all?: WorkflowCondition[];
  any?: WorkflowCondition[];
  not?: WorkflowCondition;
}

export interface WorkflowTrigger {
  type: WorkflowTriggerType;
  config: Record<string, any>;
}

export type WorkflowActionType =
  | 'send_notification'
  | 'create_note'
  | 'update_note'
  | 'append_note_content'
  | 'add_note_tags'
  | 'remove_note_tags'
  | 'set_note_type'
  | 'link_notes'
  | 'submit_for_approval'
  | 'approve_note'
  | 'reject_note'
  | 'request_approval_changes'
  | 'call_webhook'
  | 'send_email';

export interface WorkflowAction {
  id?: string;
  type: WorkflowActionType;
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
