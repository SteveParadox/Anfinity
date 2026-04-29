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

export type PlanName = 'free' | 'pro' | 'team' | 'enterprise';
export type ApprovalWorkflowStatus = 'draft' | 'submitted' | 'needs_changes' | 'approved' | 'rejected' | 'cancelled';
export type ApprovalWorkflowPriority = 'low' | 'normal' | 'high' | 'critical';
export type ThinkingSessionPhase = 'waiting' | 'gathering' | 'synthesizing' | 'refining' | 'completed';
export type NoteType = 'note' | 'web-clip' | 'document' | 'voice' | 'ai-generated';

export interface WorkspacePermissions {
  workspaceId: string;
  role: WorkspaceRole;
  permissions: Record<WorkspacePermissionSection, Record<WorkspacePermissionAction, boolean>>;
  [key: string]: any;
}

export interface User {
  id: string;
  email: string;
  name?: string;
  full_name?: string;
  fullName?: string;
  plan?: PlanName;
  is_active?: boolean;
  created_at?: string;
  [key: string]: any;
}

export interface Workspace {
  id: string;
  name: string;
  description?: string;
  role?: WorkspaceRole;
  member_count?: number;
  memberCount?: number;
  members?: User[];
  created_at?: string;
  updated_at?: string;
  createdAt?: Date | string;
  updatedAt?: Date | string;
  [key: string]: any;
}

export interface NoteAccess {
  noteId: string;
  accessSource: string;
  canView: boolean;
  canUpdate: boolean;
  canDelete: boolean;
  canManage: boolean;
  collaboratorRole?: 'viewer' | 'editor' | 'owner' | string;
  [key: string]: any;
}

export interface Note {
  id: string;
  title: string;
  content: string;
  summary?: string;
  tags: string[];
  connections: string[];
  userId: string;
  workspaceId: string;
  createdAt: Date;
  updatedAt: Date;
  confidence?: number;
  source?: string;
  type: NoteType;
  word_count?: number;
  access?: NoteAccess;
  approvalStatus?: ApprovalWorkflowStatus;
  approvalPriority?: ApprovalWorkflowPriority;
  approvalDueAt?: Date;
  approvalSubmittedAt?: Date;
  approvalSubmittedByUserId?: string;
  approvalDecidedAt?: Date;
  approvalDecidedByUserId?: string;
  [key: string]: any;
}

export interface Document {
  id: string;
  title: string;
  workspaceId?: string;
  status: string;
  sourceType?: string;
  storageUrl?: string;
  tokenCount?: number;
  chunkCount?: number;
  createdAt: Date | string | number;
  updatedAt: Date | string | number;
  [key: string]: any;
}

export interface AIInsight {
  id: string;
  type: 'connection' | 'trend' | 'suggestion' | 'summary';
  title?: string;
  description?: string;
  content?: string;
  sources: string[];
  confidence: number;
  relatedNotes?: string[];
  createdAt: Date | string | number;
  [key: string]: any;
}

export interface PricingPlan {
  id: PlanName | string;
  name: string;
  price: number;
  features: string[];
  limits?: Record<string, number | null>;
  [key: string]: any;
}

export interface SearchResult {
  id: string;
  title: string;
  content?: string;
  snippet?: string;
  score?: number;
  confidence?: number;
  source?: string;
  note?: Note;
  [key: string]: any;
}

export interface NoteCommentUser {
  id: string;
  email?: string;
  name?: string;
  avatarUrl?: string;
  [key: string]: any;
}

export interface NoteCommentMention {
  userId?: string;
  displayName?: string;
  [key: string]: any;
}

export interface NoteCommentReactionSummary {
  emoji: string;
  count: number;
  reactedByCurrentUser?: boolean;
  [key: string]: any;
}

export interface NoteComment {
  id: string;
  noteId: string;
  body: string;
  author?: NoteCommentUser | null;
  authorUserId?: string;
  parentCommentId?: string | null;
  replies: NoteComment[];
  mentions: NoteCommentMention[];
  reactions: NoteCommentReactionSummary[];
  createdAt?: Date;
  updatedAt?: Date;
  deletedAt?: Date | null;
  [key: string]: any;
}

export interface NoteContribution {
  noteId: string;
  workspaceId?: string;
  contributorUserId: string;
  contributorName?: string;
  contributorEmail?: string;
  contributionCount: number;
  breakdown: Record<string, number>;
  firstContributionAt?: Date;
  lastContributionAt?: Date;
  [key: string]: any;
}

export interface NoteVersion {
  id: string;
  noteId: string;
  versionNumber: number;
  title?: string;
  content?: string;
  tags: string[];
  snapshot?: Record<string, any>;
  changeType?: string;
  changeReason: string;
  metadata?: Record<string, any>;
  diffSegments: Array<{ type: 'added' | 'deleted' | 'unchanged' | string; text?: string; wordCount: number }>;
  wordCount?: number;
  changedByUserId?: string;
  changedBy?: NoteCommentUser;
  createdAt: Date;
  [key: string]: any;
}

export interface NoteConnectionSuggestion {
  id: string;
  sourceNoteId?: string;
  noteId?: string;
  suggestedNoteId?: string;
  suggestedNote: {
    id?: string;
    title?: string;
    contentPreview?: string;
    tags: string[];
    [key: string]: any;
  };
  score?: number;
  similarityScore: number;
  reason?: string;
  status?: string;
  createdAt?: Date;
  updatedAt?: Date;
  [key: string]: any;
}

export interface NoteInvite {
  id: string;
  noteId: string;
  inviterUserId?: string;
  inviteeEmail?: string;
  inviteeUserId?: string;
  role: 'viewer' | 'editor' | string;
  status: 'pending' | 'accepted' | 'revoked' | 'expired' | string;
  expiresAt: Date;
  acceptedAt?: Date;
  revokedAt?: Date;
  message?: string;
  createdAt: Date;
  updatedAt: Date;
  [key: string]: any;
}

export interface UserNotification {
  id: string;
  type?: string;
  title?: string;
  body?: string;
  readAt?: Date | null;
  createdAt?: Date;
  [key: string]: any;
}

export interface ApprovalWorkflowItem {
  id?: string;
  noteId: string;
  workspaceId?: string;
  title: string;
  noteTitle?: string;
  status?: ApprovalWorkflowStatus;
  priority?: ApprovalWorkflowPriority;
  approvalStatus: ApprovalWorkflowStatus;
  approvalPriority: ApprovalWorkflowPriority;
  approvalDueAt?: Date;
  approvalSubmittedAt?: Date;
  approvalSubmittedByUserId?: string;
  approvalDecidedAt?: Date;
  approvalDecidedByUserId?: string;
  dueAt?: Date | null;
  submittedAt?: Date | null;
  decidedAt?: Date | null;
  submittedBy?: NoteCommentUser | null;
  decidedBy?: NoteCommentUser | null;
  isOverdue?: boolean;
  latestTransition?: ApprovalWorkflowTransition | null;
  [key: string]: any;
}

export interface ApprovalWorkflowSummary {
  countsByStatus: Record<ApprovalWorkflowStatus, number>;
  total: number;
  overdue: number;
  [key: string]: any;
}

export interface ApprovalWorkflowTransition {
  id: string;
  workflowItemId?: string;
  fromStatus?: ApprovalWorkflowStatus | null;
  toStatus: ApprovalWorkflowStatus;
  actor?: NoteCommentUser | null;
  comment?: string;
  createdAt?: Date;
  dueAtSnapshot?: Date;
  prioritySnapshot?: ApprovalWorkflowPriority;
  [key: string]: any;
}

export interface OnboardingReadingItem {
  noteId: string;
  title: string;
  reason: string;
  [key: string]: any;
}

export interface OnboardingWeek {
  weekNumber: number;
  theme: string;
  objectives: string[];
  readingList: OnboardingReadingItem[];
  conceptCheckpoints: string[];
  supportNoteIds: string[];
  [key: string]: any;
}

export interface OnboardingGlossaryEntry {
  term: string;
  definition: string;
  supportNoteIds: string[];
  [key: string]: any;
}

export interface OnboardingGroundingMetadata {
  groundingConfidence: 'low' | 'medium' | 'high';
  usedNoteIds: string[];
  modelCandidateNoteCount: number;
  roleQueries: string[];
  warnings: string[];
  [key: string]: any;
}

export interface OnboardingCandidateNote {
  noteId: string;
  title: string;
  score?: number;
  [key: string]: any;
}

export interface OnboardingCurriculum {
  role: string;
  summary: string;
  weeks: OnboardingWeek[];
  glossary: OnboardingGlossaryEntry[];
  grounding: OnboardingGroundingMetadata;
  candidateNotes: OnboardingCandidateNote[];
  [key: string]: any;
}

export interface ThinkingParticipant {
  id: string;
  userId: string;
  user?: NoteCommentUser | null;
  joinedAt?: Date;
  lastSeenAt?: Date | null;
  [key: string]: any;
}

export interface ThinkingContribution {
  id: string;
  sessionId: string;
  authorUserId: string;
  author?: NoteCommentUser | null;
  content: string;
  createdPhase: ThinkingSessionPhase;
  voteCount: number;
  voterUserIds: string[];
  rank: number;
  createdAt?: Date;
  updatedAt?: Date;
  [key: string]: any;
}

export interface ThinkingSynthesisRun {
  id: string;
  sessionId: string;
  status: 'pending' | 'streaming' | 'completed' | 'failed' | 'cancelled' | string;
  output?: string;
  error?: string;
  createdAt?: Date;
  updatedAt?: Date;
  [key: string]: any;
}

export interface ThinkingSessionSummary {
  id: string;
  workspaceId: string;
  noteId?: string | null;
  roomId: string;
  title: string;
  phase: ThinkingSessionPhase;
  hostUserId?: string;
  activeSynthesisRunId?: string | null;
  createdAt?: Date;
  updatedAt?: Date;
  [key: string]: any;
}

export interface ThinkingSession extends ThinkingSessionSummary {
  promptContext?: string | null;
  createdByUserId?: string;
  creator?: NoteCommentUser | null;
  host?: NoteCommentUser | null;
  phaseEnteredAt?: Date;
  waitingStartedAt?: Date;
  gatheringStartedAt?: Date;
  synthesizingStartedAt?: Date;
  refiningStartedAt?: Date;
  completedAt?: Date;
  synthesisOutput: string;
  refinedOutput: string;
  finalOutput: string;
  participants: ThinkingParticipant[];
  contributions: ThinkingContribution[];
  synthesisRuns: ThinkingSynthesisRun[];
  activeSynthesisRun?: ThinkingSynthesisRun | null;
  [key: string]: any;
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
  [key: string]: any;
}

export type WorkflowTriggerType =
  | 'note_created'
  | 'note_updated'
  | 'note_deleted'
  | 'approval_submitted'
  | 'approval_approved'
  | 'approval_rejected'
  | 'document_processed'
  | string;

export interface WorkflowCondition {
  field?: string;
  operator?: string;
  value?: any;
  [key: string]: any;
}

export interface WorkflowAction {
  id?: string;
  type: string;
  config?: Record<string, any>;
  [key: string]: any;
}

export interface Workflow {
  id: string;
  workspaceId?: string;
  name: string;
  description?: string;
  triggerType: WorkflowTriggerType;
  conditions: WorkflowCondition[];
  actions: WorkflowAction[];
  enabled?: boolean;
  isActive?: boolean;
  createdAt?: Date;
  updatedAt?: Date;
  [key: string]: any;
}

export type GraphNodeType = 'workspace' | 'note' | 'document' | 'entity' | 'tag';
export type GraphEdgeType =
  | 'workspace_contains_note'
  | 'workspace_contains_document'
  | 'note_mentions_entity'
  | 'note_has_tag'
  | 'note_links_note'
  | 'note_related_note'
  | 'document_mentions_entity'
  | 'document_has_tag'
  | 'entity_co_occurs_with_entity'
  | 'tag_co_occurs_with_tag';

export interface KnowledgeGraphNodeMetadata {
  workspace_id?: string;
  note_id?: string;
  note_ids: string[];
  document_id?: string;
  document_ids?: string[];
  note_type?: string;
  tags?: string[];
  updated_at?: string | null;
  created_at?: string | null;
  entity_type?: string;
  tag_source?: string;
  cluster_id?: string;
  cluster_key?: string;
  cluster_label?: string;
  cluster_description?: string;
  cluster_score?: number;
  cluster_rank?: number;
  [key: string]: any;
}

export interface KnowledgeGraphEdgeMetadata {
  shared_signals?: number;
  confidence?: number;
  [key: string]: any;
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
  limited?: boolean;
  node_limit?: number;
  edge_limit?: number;
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
  nodeLimit?: number;
  edgeLimit?: number;
}

export interface KnowledgeGraphCluster {
  id: string;
  key: string;
  label: string;
  description: string;
  importance: number;
  node_ids: string[];
  node_count: number;
  metadata: Record<string, any>;
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  clusters: KnowledgeGraphCluster[];
  stats: KnowledgeGraphStats;
}

export interface GraphClusterInputNode {
  id: string;
  type: GraphNodeType;
  label: string;
  value: number;
  metadata: Record<string, any>;
  embedding: number[];
}

export interface GraphClusterInput {
  workspace_id: string;
  nodes: GraphClusterInputNode[];
  stats?: Record<string, any>;
}
