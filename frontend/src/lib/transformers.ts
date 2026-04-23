/**
 * Centralized Data Transformers for API Responses
 * 
 * Handles snake_case → camelCase conversion and type coercion
 * Ensures consistent data format across the application
 */

import type {
  ApprovalWorkflowItem,
  ApprovalWorkflowSummary,
  ApprovalWorkflowTransition,
  Note, NoteComment, NoteCommentMention, NoteCommentReactionSummary, NoteCommentUser, NoteContribution, UserNotification, Workspace, Document, 
  AIInsight, SearchResult, PricingPlan,
  ThinkingContribution,
  ThinkingParticipant,
  ThinkingSession,
  ThinkingSessionAccess,
  ThinkingSessionSummary,
  ThinkingSynthesisRun,
} from '@/types';

/**
 * Transform note from API (snake_case) to frontend (camelCase)
 */
export function transformNoteFromAPI(note: any): Note {
  const createdAtStr = note.created_at || note.createdAt;
  const updatedAtStr = note.updated_at || note.updatedAt;
  
  return {
    id: note.id,
    title: note.title,
    content: note.content,
    summary: note.summary || undefined,
    tags: note.tags || [],
    connections: note.connections || [],
    userId: note.user_id || note.userId,
    workspaceId: note.workspace_id || note.workspaceId,
    createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
    updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
    confidence: note.confidence_score || note.confidence,
    source: note.source_url || note.source,
    type: note.note_type || note.type || 'note',
    word_count: note.word_count,
    approvalStatus: note.approval_status || note.approvalStatus || 'draft',
    approvalPriority: note.approval_priority || note.approvalPriority || 'normal',
    approvalDueAt: note.approval_due_at || note.approvalDueAt ? new Date(note.approval_due_at || note.approvalDueAt) : undefined,
    approvalSubmittedAt: note.approval_submitted_at || note.approvalSubmittedAt ? new Date(note.approval_submitted_at || note.approvalSubmittedAt) : undefined,
    approvalSubmittedByUserId: note.approval_submitted_by_user_id || note.approvalSubmittedByUserId || undefined,
    approvalDecidedAt: note.approval_decided_at || note.approvalDecidedAt ? new Date(note.approval_decided_at || note.approvalDecidedAt) : undefined,
    approvalDecidedByUserId: note.approval_decided_by_user_id || note.approvalDecidedByUserId || undefined,
    access: note.access
      ? {
          noteId: note.access.note_id || note.access.noteId || note.id,
          accessSource: note.access.access_source || note.access.accessSource || 'workspace',
          canView: Boolean(note.access.can_view ?? note.access.canView),
          canUpdate: Boolean(note.access.can_update ?? note.access.canUpdate),
          canDelete: Boolean(note.access.can_delete ?? note.access.canDelete),
          canManage: Boolean(note.access.can_manage ?? note.access.canManage),
          collaboratorRole: note.access.collaborator_role || note.access.collaboratorRole || undefined,
        }
      : undefined,
  };
}

export function transformNoteContributionFromAPI(contribution: any): NoteContribution {
  return {
    noteId: contribution.note_id || contribution.noteId,
    workspaceId: contribution.workspace_id || contribution.workspaceId || undefined,
    contributorUserId: contribution.contributor_user_id || contribution.contributorUserId,
    contributorName: contribution.contributor_name || contribution.contributorName || undefined,
    contributorEmail: contribution.contributor_email || contribution.contributorEmail || undefined,
    contributionCount: contribution.contribution_count || contribution.contributionCount || 0,
    breakdown: {
      noteCreated: contribution.breakdown?.note_created || contribution.breakdown?.noteCreated || 0,
      noteUpdated: contribution.breakdown?.note_updated || contribution.breakdown?.noteUpdated || 0,
      noteRestored: contribution.breakdown?.note_restored || contribution.breakdown?.noteRestored || 0,
      thinkingContributions: contribution.breakdown?.thinking_contributions || contribution.breakdown?.thinkingContributions || 0,
      votesCast: contribution.breakdown?.votes_cast || contribution.breakdown?.votesCast || 0,
    },
    firstContributionAt: contribution.first_contribution_at ? new Date(contribution.first_contribution_at) : undefined,
    lastContributionAt: contribution.last_contribution_at ? new Date(contribution.last_contribution_at) : undefined,
  };
}

function transformNoteCommentUserFromAPI(user: any): NoteCommentUser | null {
  if (!user) return null;
  return {
    id: user.id,
    email: user.email,
    name: user.name || user.full_name || user.email || 'Unknown',
  };
}

function transformNoteCommentMentionFromAPI(mention: any): NoteCommentMention {
  return {
    id: mention.id,
    commentId: mention.comment_id || mention.commentId,
    mentionedUserId: mention.mentioned_user_id || mention.mentionedUserId,
    mentionToken: mention.mention_token || mention.mentionToken || '',
    startOffset: mention.start_offset ?? mention.startOffset ?? 0,
    endOffset: mention.end_offset ?? mention.endOffset ?? 0,
    user: transformNoteCommentUserFromAPI(mention.user),
  };
}

function transformNoteCommentReactionFromAPI(reaction: any): NoteCommentReactionSummary {
  return {
    emoji: reaction.emoji || 'thumbs_up',
    emojiValue: reaction.emoji_value || reaction.emojiValue || '👍',
    count: reaction.count || 0,
    reactedByCurrentUser: Boolean(reaction.reacted_by_current_user ?? reaction.reactedByCurrentUser),
  };
}

export function transformNoteCommentFromAPI(comment: any): NoteComment {
  return {
    id: comment.id,
    noteId: comment.note_id || comment.noteId,
    authorUserId: comment.author_user_id || comment.authorUserId,
    parentCommentId: comment.parent_comment_id || comment.parentCommentId || undefined,
    depth: comment.depth || 0,
    body: comment.body || '',
    isResolved: Boolean(comment.is_resolved ?? comment.isResolved),
    resolvedByUserId: comment.resolved_by_user_id || comment.resolvedByUserId || undefined,
    resolvedAt: comment.resolved_at || comment.resolvedAt ? new Date(comment.resolved_at || comment.resolvedAt) : undefined,
    createdAt: comment.created_at || comment.createdAt ? new Date(comment.created_at || comment.createdAt) : undefined,
    updatedAt: comment.updated_at || comment.updatedAt ? new Date(comment.updated_at || comment.updatedAt) : undefined,
    author: transformNoteCommentUserFromAPI(comment.author),
    resolvedBy: transformNoteCommentUserFromAPI(comment.resolved_by || comment.resolvedBy),
    mentions: Array.isArray(comment.mentions) ? comment.mentions.map(transformNoteCommentMentionFromAPI) : [],
    reactions: Array.isArray(comment.reactions) ? comment.reactions.map(transformNoteCommentReactionFromAPI) : [],
    replies: Array.isArray(comment.replies) ? comment.replies.map(transformNoteCommentFromAPI) : [],
  };
}

export function transformUserNotificationFromAPI(notification: any): UserNotification {
  return {
    id: notification.id,
    userId: notification.user_id || notification.userId,
    actorUserId: notification.actor_user_id || notification.actorUserId || undefined,
    workspaceId: notification.workspace_id || notification.workspaceId || undefined,
    noteId: notification.note_id || notification.noteId || undefined,
    commentId: notification.comment_id || notification.commentId || undefined,
    notificationType: notification.notification_type || notification.notificationType || 'comment_reply',
    payload: notification.payload || {},
    isRead: Boolean(notification.is_read ?? notification.isRead),
    readAt: notification.read_at || notification.readAt ? new Date(notification.read_at || notification.readAt) : undefined,
    createdAt: notification.created_at || notification.createdAt ? new Date(notification.created_at || notification.createdAt) : undefined,
    actor: transformNoteCommentUserFromAPI(notification.actor),
  };
}

export function transformApprovalWorkflowItemFromAPI(item: any): ApprovalWorkflowItem {
  return {
    noteId: item.note_id || item.noteId,
    workspaceId: item.workspace_id || item.workspaceId || undefined,
    title: item.title || '',
    summary: item.summary || undefined,
    noteType: item.note_type || item.noteType || 'note',
    authorUserId: item.author_user_id || item.authorUserId,
    approvalStatus: item.approval_status || item.approvalStatus || 'draft',
    approvalPriority: item.approval_priority || item.approvalPriority || 'normal',
    approvalDueAt: item.approval_due_at || item.approvalDueAt ? new Date(item.approval_due_at || item.approvalDueAt) : undefined,
    approvalSubmittedAt: item.approval_submitted_at || item.approvalSubmittedAt ? new Date(item.approval_submitted_at || item.approvalSubmittedAt) : undefined,
    approvalSubmittedByUserId: item.approval_submitted_by_user_id || item.approvalSubmittedByUserId || undefined,
    approvalDecidedAt: item.approval_decided_at || item.approvalDecidedAt ? new Date(item.approval_decided_at || item.approvalDecidedAt) : undefined,
    approvalDecidedByUserId: item.approval_decided_by_user_id || item.approvalDecidedByUserId || undefined,
    isOverdue: Boolean(item.is_overdue ?? item.isOverdue),
    availableActions: {
      submit: Boolean(item.available_actions?.submit ?? item.availableActions?.submit),
      resubmit: Boolean(item.available_actions?.resubmit ?? item.availableActions?.resubmit),
      cancel: Boolean(item.available_actions?.cancel ?? item.availableActions?.cancel),
      approve: Boolean(item.available_actions?.approve ?? item.availableActions?.approve),
      reject: Boolean(item.available_actions?.reject ?? item.availableActions?.reject),
      request_changes: Boolean(item.available_actions?.request_changes ?? item.availableActions?.request_changes),
    },
    author: transformNoteCommentUserFromAPI(item.author),
    submittedBy: transformNoteCommentUserFromAPI(item.submitted_by || item.submittedBy),
    decidedBy: transformNoteCommentUserFromAPI(item.decided_by || item.decidedBy),
  };
}

export function transformApprovalWorkflowSummaryFromAPI(summary: any): ApprovalWorkflowSummary {
  const rawCounts = summary.counts_by_status || summary.countsByStatus || {};
  return {
    countsByStatus: {
      draft: rawCounts.draft || 0,
      submitted: rawCounts.submitted || 0,
      needs_changes: rawCounts.needs_changes || 0,
      approved: rawCounts.approved || 0,
      rejected: rawCounts.rejected || 0,
      cancelled: rawCounts.cancelled || 0,
    },
    total: summary.total || 0,
    overdue: summary.overdue || 0,
  };
}

export function transformApprovalWorkflowTransitionFromAPI(item: any): ApprovalWorkflowTransition {
  return {
    id: item.id,
    noteId: item.note_id || item.noteId,
    workspaceId: item.workspace_id || item.workspaceId || undefined,
    actorUserId: item.actor_user_id || item.actorUserId || undefined,
    fromStatus: item.from_status || item.fromStatus || 'draft',
    toStatus: item.to_status || item.toStatus || 'draft',
    comment: item.comment || undefined,
    dueAtSnapshot: item.due_at_snapshot || item.dueAtSnapshot ? new Date(item.due_at_snapshot || item.dueAtSnapshot) : undefined,
    prioritySnapshot: item.priority_snapshot || item.prioritySnapshot || 'normal',
    createdAt: item.created_at || item.createdAt ? new Date(item.created_at || item.createdAt) : undefined,
    actor: transformNoteCommentUserFromAPI(item.actor),
  };
}

/**
 * Transform workspace from API (snake_case) to frontend (camelCase)
 */
export function transformWorkspaceFromAPI(workspace: any): Workspace {
  const createdAtStr = workspace.created_at || workspace.createdAt;
  const updatedAtStr = workspace.updated_at || workspace.updatedAt;
  
  return {
    id: workspace.id,
    name: workspace.name,
    description: workspace.description || '',
    members: workspace.members || [],
    createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
    updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
  };
}

/**
 * Transform document from API to frontend format
 */
export function transformDocumentFromAPI(doc: any): Document {
  const createdAtStr = doc.created_at || doc.createdAt;
  const updatedAtStr = doc.updated_at || doc.updatedAt;
  
  return {
    id: doc.id,
    workspaceId: doc.workspace_id || doc.workspaceId,
    title: doc.title,
    sourceType: (doc.source_type || doc.sourceType) as any,
    status: doc.status as 'pending' | 'processing' | 'indexed' | 'failed',
    tokenCount: doc.token_count || doc.tokenCount || 0,
    chunkCount: doc.chunk_count || doc.chunkCount || 0,
    storageUrl: doc.storage_url || doc.storageUrl,
    createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
    updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
  };
}

/**
 * Transform query response from API
 */
export interface QueryResponse {
  query_id: string;
  query: string;
  answer: string;
  confidence: number;
  confidence_factors: {
    similarity_avg: number;
    document_diversity: number;
    source_coverage: number;
    chunks_retrieved: number;
    unique_documents: number;
  };
  sources: Array<{
    chunk_id: string;
    document_id: string;
    document_title: string;
    text: string;
    similarity: number;
  }>;
  model_used: string;
  tokens_used: number;
  response_time_ms: number;
}

export function transformQueryResponseFromAPI(response: any): QueryResponse {
  return {
    query_id: response.query_id || response.queryId,
    query: response.query,
    answer: response.answer,
    confidence: response.confidence,
    confidence_factors: {
      similarity_avg: response.confidence_factors?.similarity_avg || 
                      response.confidenceFactors?.similarityAvg || 0,
      document_diversity: response.confidence_factors?.document_diversity || 
                          response.confidenceFactors?.documentDiversity || 0,
      source_coverage: response.confidence_factors?.source_coverage || 
                       response.confidenceFactors?.sourceCoverage || 0,
      chunks_retrieved: response.confidence_factors?.chunks_retrieved || 
                        response.confidenceFactors?.chunksRetrieved || 0,
      unique_documents: response.confidence_factors?.unique_documents || 
                        response.confidenceFactors?.uniqueDocuments || 0,
    },
    sources: (response.sources || []).map((source: any) => ({
      chunk_id: source.chunk_id || source.chunkId,
      document_id: source.document_id || source.documentId,
      document_title: source.document_title || source.documentTitle,
      text: source.text,
      similarity: source.similarity,
    })),
    model_used: response.model_used || response.modelUsed,
    tokens_used: response.tokens_used || response.tokensUsed,
    response_time_ms: response.response_time_ms || response.responseTimeMs,
  };
}

/**
 * Transform paginated notes response
 */
export function transformPaginatedNotes(response: any) {
  return {
    items: (response.items || []).map(transformNoteFromAPI),
    total: response.total || 0,
    page: response.page || 1,
    page_size: response.page_size || response.pageSize || 20,
  };
}

/**
 * Transform paginated workspaces response
 */
export function transformPaginatedWorkspaces(response: any) {
  return {
    items: (response.items || []).map(transformWorkspaceFromAPI),
    total: response.total || 0,
    page: response.page || 1,
    page_size: response.page_size || response.pageSize || 20,
  };
}

/**
 * Transform paginated documents response
 */
export function transformPaginatedDocuments(response: any) {
  return {
    items: (response.items || []).map(transformDocumentFromAPI),
    total: response.total || 0,
    page: response.page || 1,
    page_size: response.page_size || response.pageSize || 20,
  };
}

/**
 * Transform array of notes
 */
export function transformNotesArray(notes: any[]): Note[] {
  return (notes || []).map(transformNoteFromAPI);
}

/**
 * Transform array of workspaces
 */
export function transformWorkspacesArray(workspaces: any[]): Workspace[] {
  return (workspaces || []).map(transformWorkspaceFromAPI);
}

/**
 * Transform array of documents
 */
export function transformDocumentsArray(documents: any[]): Document[] {
  return (documents || []).map(transformDocumentFromAPI);
}

function transformThinkingParticipantFromAPI(participant: any): ThinkingParticipant {
  return {
    id: participant.id,
    userId: participant.user_id || participant.userId,
    user: participant.user
      ? {
          id: participant.user.id,
          email: participant.user.email,
          name: participant.user.name,
        }
      : null,
    joinedAt: participant.joined_at || participant.joinedAt
      ? new Date(participant.joined_at || participant.joinedAt)
      : undefined,
    lastSeenAt: participant.last_seen_at || participant.lastSeenAt
      ? new Date(participant.last_seen_at || participant.lastSeenAt)
      : undefined,
  };
}

function transformThinkingContributionFromAPI(contribution: any): ThinkingContribution {
  return {
    id: contribution.id,
    sessionId: contribution.session_id || contribution.sessionId,
    authorUserId: contribution.author_user_id || contribution.authorUserId,
    author: contribution.author
      ? {
          id: contribution.author.id,
          email: contribution.author.email,
          name: contribution.author.name,
        }
      : null,
    content: contribution.content || '',
    createdPhase: contribution.created_phase || contribution.createdPhase || 'gathering',
    voteCount: contribution.vote_count || contribution.voteCount || 0,
    voterUserIds: Array.isArray(contribution.voter_user_ids)
      ? contribution.voter_user_ids
      : Array.isArray(contribution.voterUserIds)
        ? contribution.voterUserIds
        : [],
    rank: contribution.rank || 0,
    createdAt: contribution.created_at || contribution.createdAt
      ? new Date(contribution.created_at || contribution.createdAt)
      : undefined,
    updatedAt: contribution.updated_at || contribution.updatedAt
      ? new Date(contribution.updated_at || contribution.updatedAt)
      : undefined,
  };
}

function transformThinkingSynthesisRunFromAPI(run: any): ThinkingSynthesisRun {
  return {
    id: run.id,
    sessionId: run.session_id || run.sessionId,
    triggeredByUserId: run.triggered_by_user_id || run.triggeredByUserId || undefined,
    triggeredBy: run.triggered_by || run.triggeredBy
      ? {
          id: (run.triggered_by || run.triggeredBy).id,
          email: (run.triggered_by || run.triggeredBy).email,
          name: (run.triggered_by || run.triggeredBy).name,
        }
      : null,
    status: run.status || 'pending',
    model: run.model || '',
    contributionCount: run.contribution_count || run.contributionCount || 0,
    outputText: run.output_text || run.outputText || '',
    errorMessage: run.error_message || run.errorMessage || null,
    startedAt: run.started_at || run.startedAt
      ? new Date(run.started_at || run.startedAt)
      : undefined,
    completedAt: run.completed_at || run.completedAt
      ? new Date(run.completed_at || run.completedAt)
      : undefined,
    failedAt: run.failed_at || run.failedAt
      ? new Date(run.failed_at || run.failedAt)
      : undefined,
    createdAt: run.created_at || run.createdAt
      ? new Date(run.created_at || run.createdAt)
      : undefined,
    updatedAt: run.updated_at || run.updatedAt
      ? new Date(run.updated_at || run.updatedAt)
      : undefined,
  };
}

export function transformThinkingSessionFromAPI(session: any): ThinkingSession {
  return {
    id: session.id,
    workspaceId: session.workspace_id || session.workspaceId,
    noteId: session.note_id || session.noteId || null,
    roomId: session.room_id || session.roomId,
    title: session.title || 'Untitled session',
    promptContext: session.prompt_context || session.promptContext || null,
    createdByUserId: session.created_by_user_id || session.createdByUserId,
    hostUserId: session.host_user_id || session.hostUserId,
    creator: session.creator
      ? {
          id: session.creator.id,
          email: session.creator.email,
          name: session.creator.name,
        }
      : null,
    host: session.host
      ? {
          id: session.host.id,
          email: session.host.email,
          name: session.host.name,
        }
      : null,
    phase: session.phase || 'waiting',
    phaseEnteredAt: session.phase_entered_at || session.phaseEnteredAt
      ? new Date(session.phase_entered_at || session.phaseEnteredAt)
      : undefined,
    waitingStartedAt: session.waiting_started_at || session.waitingStartedAt
      ? new Date(session.waiting_started_at || session.waitingStartedAt)
      : undefined,
    gatheringStartedAt: session.gathering_started_at || session.gatheringStartedAt
      ? new Date(session.gathering_started_at || session.gatheringStartedAt)
      : undefined,
    synthesizingStartedAt: session.synthesizing_started_at || session.synthesizingStartedAt
      ? new Date(session.synthesizing_started_at || session.synthesizingStartedAt)
      : undefined,
    refiningStartedAt: session.refining_started_at || session.refiningStartedAt
      ? new Date(session.refining_started_at || session.refiningStartedAt)
      : undefined,
    completedAt: session.completed_at || session.completedAt
      ? new Date(session.completed_at || session.completedAt)
      : undefined,
    activeSynthesisRunId: session.active_synthesis_run_id || session.activeSynthesisRunId || null,
    synthesisOutput: session.synthesis_output || session.synthesisOutput || '',
    refinedOutput: session.refined_output || session.refinedOutput || '',
    finalOutput: session.final_output || session.finalOutput || '',
    lastRefinedByUserId: session.last_refined_by_user_id || session.lastRefinedByUserId || null,
    lastRefinedBy: session.last_refined_by || session.lastRefinedBy
      ? {
          id: (session.last_refined_by || session.lastRefinedBy).id,
          email: (session.last_refined_by || session.lastRefinedBy).email,
          name: (session.last_refined_by || session.lastRefinedBy).name,
        }
      : null,
    createdAt: session.created_at || session.createdAt
      ? new Date(session.created_at || session.createdAt)
      : undefined,
    updatedAt: session.updated_at || session.updatedAt
      ? new Date(session.updated_at || session.updatedAt)
      : undefined,
    participants: Array.isArray(session.participants)
      ? session.participants.map(transformThinkingParticipantFromAPI)
      : [],
    contributions: Array.isArray(session.contributions)
      ? session.contributions.map(transformThinkingContributionFromAPI)
      : [],
    synthesisRuns: Array.isArray(session.synthesis_runs || session.synthesisRuns)
      ? (session.synthesis_runs || session.synthesisRuns).map(transformThinkingSynthesisRunFromAPI)
      : [],
    activeSynthesisRun: session.active_synthesis_run || session.activeSynthesisRun
      ? transformThinkingSynthesisRunFromAPI(session.active_synthesis_run || session.activeSynthesisRun)
      : null,
  };
}

export function transformThinkingSessionSummaryFromAPI(session: any): ThinkingSessionSummary {
  return {
    id: session.id,
    workspaceId: session.workspace_id || session.workspaceId,
    noteId: session.note_id || session.noteId || null,
    roomId: session.room_id || session.roomId,
    title: session.title || 'Untitled session',
    phase: session.phase || 'waiting',
    hostUserId: session.host_user_id || session.hostUserId,
    activeSynthesisRunId: session.active_synthesis_run_id || session.activeSynthesisRunId || null,
    createdAt: session.created_at || session.createdAt
      ? new Date(session.created_at || session.createdAt)
      : undefined,
    updatedAt: session.updated_at || session.updatedAt
      ? new Date(session.updated_at || session.updatedAt)
      : undefined,
  };
}

export function transformThinkingSessionAccessFromAPI(access: any): ThinkingSessionAccess {
  return {
    sessionId: access.session_id || access.sessionId,
    workspaceId: access.workspace_id || access.workspaceId,
    roomId: access.room_id || access.roomId,
    canView: Boolean(access.can_view ?? access.canView),
    canParticipate: Boolean(access.can_participate ?? access.canParticipate),
    canControl: Boolean(access.can_control ?? access.canControl),
    isHost: Boolean(access.is_host ?? access.isHost),
    phase: access.phase || 'waiting',
  };
}

/**
 * Create derived insights from notes
 */
export function deriveInsightsFromNotes(notes: Note[]): AIInsight[] {
  const notesCount = notes.length;
  const recentNotes = notes.slice(0, 5);
  const tagsUsed = new Set<string>();
  
  notes.forEach(note => {
    (note.tags || []).forEach(tag => tagsUsed.add(tag));
  });

  return [
    {
      id: '1',
      content: `Your workspace has ${notesCount} notes in the knowledge base.`,
      sources: [],
      confidence: 0.95,
      createdAt: new Date(),
      type: 'suggestion',
    },
    {
      id: '2',
      content: 'Knowledge base is actively growing with recent additions.',
      sources: recentNotes.map(n => n.id),
      confidence: 0.87,
      createdAt: new Date(),
      type: 'trend',
    },
    ...(tagsUsed.size > 0 ? [{
      id: '3',
      content: `Using ${tagsUsed.size} unique tags to organize knowledge (${Array.from(tagsUsed).slice(0, 3).join(', ')}${tagsUsed.size > 3 ? '...' : ''})`,
      sources: [],
      confidence: 0.8,
      createdAt: new Date(),
      type: 'summary' as const,
    }] : []),
  ];
}

/**
 * Safe transformation with error handling
 */
export function safeTransform<T>(
  data: any,
  transformer: (data: any) => T,
  fallback: T
): T {
  try {
    return transformer(data);
  } catch (error) {
    console.error('Transformation error:', error);
    return fallback;
  }
}

/**
 * Batch safe transformation
 */
export function batchSafeTransform<T>(
  data: any[],
  transformer: (item: any) => T,
  fallback: T
): T[] {
  return (data || []).map((item, index) => {
    try {
      return transformer(item);
    } catch (error) {
      console.error(`Transformation error at index ${index}:`, error);
      return fallback;
    }
  });
}
