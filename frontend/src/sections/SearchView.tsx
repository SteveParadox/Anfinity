import { useState, useMemo, useEffect, useContext } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search, Sparkles, Brain, Filter, Clock,
  ArrowRight, X, Lightbulb, Quote,
} from 'lucide-react';
import type { Note } from '@/types';
import { formatDistanceToNow } from 'date-fns';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';
import { useProductSettings } from '@/hooks/useProductSettings';
import { clampSearchTopK } from '@/lib/productSettings';
import {
  SEMANTIC_FEEDBACK_REASON_OPTIONS,
  SEMANTIC_FEEDBACK_TYPE_LABELS,
  ratingForSemanticFeedback,
  requiresReasonForSemanticFeedback,
  semanticFeedbackResultIds,
  semanticFeedbackResultSnapshot,
  type SemanticFeedbackReasonCode,
  type SemanticFeedbackType,
} from '@/lib/semanticFeedback';

interface QueryResult {
  query_id: string;
  answer_id: string;
  answer: string;
  confidence: number;
  confidence_factors?: {
    similarity_avg?: number;
    document_diversity?: number;
    source_coverage?: number;
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

interface SemanticResult {
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  highlight: string;
  tags: string[];
  source_kind: string;
  source_type: string;
  chunk_index: number;
  token_count: number;
  context_before?: string | null;
  context_after?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  interaction_count: number;
  similarity_score: number;
  recency_score: number;
  usage_score: number;
  final_score: number;
  highlights?: SmartHighlight[];
  matched_chunks?: MatchedSearchChunk[];
  confidence?: 'low' | 'medium' | 'high' | string;
  confidence_score?: number;
  match_summary?: Record<string, unknown>;
}

interface SmartHighlight {
  text: string;
  start_offset: number;
  end_offset: number;
  score: number;
  matched_terms: string[];
  heading?: string | null;
  confidence: 'low' | 'medium' | 'high' | string;
}

interface MatchedSearchChunk {
  chunk_id: string;
  note_id: string;
  chunk_index: number;
  text: string;
  start_offset: number;
  end_offset: number;
  heading?: string | null;
  score: number;
  semantic_score: number;
  lexical_score: number;
  evidence_score: number;
  domain_alignment: number;
  off_topic: boolean;
  confidence: 'low' | 'medium' | 'high' | string;
  highlights: SmartHighlight[];
  metadata?: Record<string, unknown>;
}

interface SearchDisplayResult {
  note: Note;
  score: number;
  highlights: SmartHighlight[];
  legacyHighlight: string;
  sourceType: string;
  sourceKind: string;
  chunkIndex: number;
  tokenCount: number;
  contextBefore?: string | null;
  contextAfter?: string | null;
  metadata: Record<string, unknown>;
  matchedChunks: MatchedSearchChunk[];
  confidence: string;
  confidenceScore: number;
  raw: SemanticResult;
}

interface SemanticSearchResponsePayload {
  results: SemanticResult[];
  search_log_id?: string | null;
}

type FeedbackType = SemanticFeedbackType;
type FeedbackReasonCode = SemanticFeedbackReasonCode;

interface SubmittedFeedbackState {
  feedbackId: string;
  feedbackType: FeedbackType;
  reasonCode?: FeedbackReasonCode;
  comment?: string;
  updatedExisting: boolean;
}

interface FeedbackDraftState {
  feedbackType: FeedbackType | null;
  feedbackReason: FeedbackReasonCode | '';
  feedbackComment: string;
  showDetails: boolean;
  submitting: boolean;
  message: string | null;
  error: string | null;
  submitted: SubmittedFeedbackState | null;
}

type FeedbackDraftUpdater =
  | Partial<FeedbackDraftState>
  | ((current: FeedbackDraftState) => FeedbackDraftState);

function createFeedbackDraftState(): FeedbackDraftState {
  return {
    feedbackType: null,
    feedbackReason: '',
    feedbackComment: '',
    showDetails: false,
    submitting: false,
    message: null,
    error: null,
    submitted: null,
  };
}

const TT = {
  inkBlack:  'var(--theme-canvas)',
  inkDeep:   'var(--theme-panel)',
  inkRaised: 'var(--theme-panel-raised)',
  inkBorder: 'var(--theme-border)',
  inkMid:    'var(--theme-border-strong)',
  inkMuted:  'var(--theme-text-muted)',
  inkSubtle: 'var(--theme-text-subtle)',
  snow:      'var(--theme-text)',
  yolk:      'var(--theme-accent)',
  yolkBright:'#FFF176',
  error:     'var(--theme-error)',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function renderHighlightedText(text: string, matchedTerms: string[]) {
  const terms = Array.from(new Set((matchedTerms || []).map((term) => term.trim()).filter(Boolean)))
    .sort((a, b) => b.length - a.length);

  if (!terms.length) return text;

  const regex = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'gi');
  const parts = text.split(regex).filter(Boolean);

  return parts.map((part, index) => {
    const isMatch = terms.some((term) => part.toLowerCase() === term.toLowerCase());
    if (!isMatch) return <span key={`${part}-${index}`}>{part}</span>;
    return (
      <mark
        key={`${part}-${index}`}
        style={{
          color: TT.snow,
          background: 'rgba(245,230,66,0.18)',
          borderBottom: `1px solid ${TT.yolk}`,
          padding: '0 1px',
        }}
      >
        {part}
      </mark>
    );
  });
}

function confidenceLabel(confidence: string, score: number) {
  const normalized = confidence || (score >= 0.72 ? 'high' : score >= 0.46 ? 'medium' : 'low');
  if (normalized === 'high') return 'Strong evidence';
  if (normalized === 'medium') return 'Good evidence';
  return 'Weak evidence';
}

export function SearchView() {
  const [query, setQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [selectedResult, setSelectedResult] = useState<SearchDisplayResult | null>(null);
  const [activeFilters, setActiveFilters] = useState<string[]>([]);
  const [semanticResults, setSemanticResults] = useState<SemanticResult[]>([]);
  const [searchLogId, setSearchLogId] = useState<string | null>(null);
  const [queryResults, setQueryResults] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  
  // Feedback state (separate per output)
  const [answerFeedbackDraft, setAnswerFeedbackDraft] = useState<FeedbackDraftState>(() => createFeedbackDraftState());
  const [resultFeedbackDrafts, setResultFeedbackDrafts] = useState<Record<string, FeedbackDraftState>>({});
  
  const [showStep7Format, setShowStep7Format] = useState(false);
  
  // STEP 8: Credibility analytics state
  const [showCredibilityAnalytics, setShowCredibilityAnalytics] = useState(false);
  const [credibilityScores, setCredibilityScores] = useState<Array<{
    chunk_id: string;
    document_id: string;
    credibility_score: number;
    accuracy_rate: number;
    positive_feedback: number;
    negative_feedback: number;
    total_uses: number;
  }> | null>(null);
  const [modelMetrics, setModelMetrics] = useState<{
    total_feedback: number;
    approved_count: number;
    rejected_count: number;
    approval_rate: number;
    rejection_rate: number;
    average_rating: number;
  } | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  
  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;
  const hasPermission = authContext?.hasPermission ?? (() => false);
  const canUseSearch = Boolean(workspaceId && hasPermission(workspaceId, 'search', 'view'));
  const { user: productSettings } = useProductSettings(workspaceId, Boolean(workspaceId));
  const smartHighlightsEnabled = productSettings?.settings.ai_search.smart_highlights ?? true;
  const showSimilarityEvidence = productSettings?.settings.ai_search.show_similarity_scores ?? true;
  const defaultSearchTopK = clampSearchTopK(productSettings?.settings.ai_search.default_top_k ?? 6);

  const getResultFeedbackDraft = (resultId: string): FeedbackDraftState =>
    resultFeedbackDrafts[resultId] ?? createFeedbackDraftState();

  const patchAnswerFeedbackDraft = (updater: FeedbackDraftUpdater) => {
    setAnswerFeedbackDraft((prev) => (typeof updater === 'function' ? updater(prev) : { ...prev, ...updater }));
  };

  const patchResultFeedbackDraft = (resultId: string, updater: FeedbackDraftUpdater) => {
    setResultFeedbackDrafts((prev) => {
      const current = prev[resultId] ?? createFeedbackDraftState();
      const next = typeof updater === 'function' ? updater(current) : { ...current, ...updater };
      return { ...prev, [resultId]: next };
    });
  };

  const applyFeedbackTypeSelection = (patchDraft: (updater: FeedbackDraftUpdater) => void, nextType: FeedbackType) => {
    patchDraft((current) => ({
      ...current,
      feedbackType: nextType,
      feedbackReason: requiresReasonForSemanticFeedback(nextType) ? current.feedbackReason : '',
      showDetails: current.showDetails || requiresReasonForSemanticFeedback(nextType),
      error: null,
      message: null,
    }));
  };

  // Load notes from workspace for generating suggestions
  useEffect(() => {
    if (!workspaceId || !canUseSearch) {
      console.debug('ℹ️ [NO WORKSPACE] No workspace ID, clearing notes');
      setNotes([]);
      return;
    }

    const loadWorkspaceNotes = async () => {
      console.log('📝 [LOAD NOTES START] Loading workspace notes - Workspace:', workspaceId);
      try {
        console.debug('📡 [API CALL] Calling api.getWorkspaceNotes()');
        const response: any = await api.getWorkspaceNotes(workspaceId, { page_size: 50 });
        const workspaceNotes = Array.isArray(response) ? response : response.items || [];
        console.log('✅ [LOAD NOTES SUCCESS] Loaded %d notes for suggestions', workspaceNotes.length);
        setNotes(workspaceNotes);
      } catch (err) {
        console.error('❌ [LOAD NOTES FAILED] Failed to load workspace notes for search suggestions:', err);
        setNotes([]);
      }
    };

    loadWorkspaceNotes();
  }, [canUseSearch, workspaceId]);

  // Generate suggested searches from workspace note titles and tags
  const generateSuggestions = (): string[] => {
    const suggestions = new Set<string>();
    
    // Add note titles as suggestions
    notes.slice(0, 3).forEach((note: any) => {
      if (note.title && note.title.length > 3) {
        suggestions.add(note.title);
      }
    });
    
    // Add tags as suggestions
    notes.forEach((note: any) => {
      (note.tags || []).forEach((tag: string) => {
        if (tag && tag.length > 2) {
          suggestions.add(tag);
        }
      });
    });
    
    // If we don't have enough suggestions, add some generic ones
    if (suggestions.size < 3) {
      suggestions.add('Recent notes');
      suggestions.add('Workspace overview');
      suggestions.add('Team collaboration');
    }
    
    return Array.from(suggestions).slice(0, 4);
  };

  // Transform query results to Note format for display
  const displayResults = useMemo(() => {
    return semanticResults.map((result) => {
      const structuredHighlights = Array.isArray(result.highlights) && result.highlights.length > 0
        ? result.highlights
        : result.highlight
          ? [{
            text: result.highlight || `${result.content.substring(0, 100)}${result.content.length > 100 ? '...' : ''}`,
            start_offset: 0,
            end_offset: Math.min(result.content.length, result.highlight.length),
            score: result.final_score,
            matched_terms: [],
            heading: null,
            confidence: result.confidence || 'low',
          }]
          : [];

      return {
        note: {
          id: result.source_kind === 'note' ? result.document_id : result.chunk_id,
          title: result.document_title,
          content: result.content,
          summary: structuredHighlights[0]?.text || 'No focused highlight available',
          tags: result.tags || [],
          connections: [] as string[],
          userId: '',
          workspaceId: workspaceId || '',
          type: result.source_kind === 'note' ? 'note' as const : 'document' as const,
          createdAt: new Date(result.created_at),
          updatedAt: new Date(result.created_at),
        },
        score: result.final_score,
        highlights: structuredHighlights,
        legacyHighlight: result.highlight || '',
        sourceType: result.source_type,
        sourceKind: result.source_kind,
        chunkIndex: result.chunk_index,
        tokenCount: result.token_count || 0,
        contextBefore: result.context_before,
        contextAfter: result.context_after,
        metadata: result.metadata || {},
        matchedChunks: Array.isArray(result.matched_chunks) ? result.matched_chunks : [],
        confidence: result.confidence || 'low',
        confidenceScore: result.confidence_score ?? result.final_score,
        raw: result,
      };
    });
  }, [semanticResults, workspaceId]);

  const filteredResults = useMemo(() => {
    if (!activeFilters.length) return displayResults;
    return displayResults.filter((result) =>
      activeFilters.some((filter) => {
        const needle = filter.toLowerCase();
        return (
          result.note.title.toLowerCase().includes(needle) ||
          result.note.content.toLowerCase().includes(needle)
        );
      })
    );
  }, [displayResults, activeFilters]);

  const searchConfidence = useMemo(() => {
    if (!filteredResults.length) return null;
    const topScores = filteredResults
      .slice(0, 3)
      .map((result) => Number(result.score))
      .filter((score) => Number.isFinite(score) && score >= 0);

    if (!topScores.length) return null;
    return Math.round((topScores.reduce((sum, score) => sum + score, 0) / topScores.length) * 100);
  }, [filteredResults]);

  const hasGroundedAnswer = Boolean(
    queryResults
    && queryResults.confidence > 0
    && queryResults.sources.length > 0
    && queryResults.answer.trim()
    && !queryResults.answer.toLowerCase().includes("couldn't find enough reliable information")
  );

  const allTags = useMemo(() => {
    const s = new Set<string>();
    notes.forEach((n) => n.tags?.forEach((t) => s.add(t)));
    return Array.from(s).sort();
  }, [notes]);

  const toggleFilter = (tag: string) =>
    setActiveFilters((p) => (p.includes(tag) ? p.filter((t) => t !== tag) : [...p, tag]));

  // Execute semantic search via backend API
  const legacyDoSearch = async (searchQuery?: string) => {
    // Ensure queryToUse is a string
    let queryToUse = typeof searchQuery === 'string' ? searchQuery : (typeof query === 'string' ? query : '');
    
    console.log('🔍 [SEARCH START] Executing semantic search - Query: "%s", Workspace: %s', queryToUse, workspaceId);
    
    if (!queryToUse.trim()) {
      console.warn('⚠️ [VALIDATION] Query is empty');
      setError('Please enter a search query');
      setIsSearching(false);
      return;
    }
    
    if (!workspaceId || !canUseSearch) {
      console.warn('⚠️ [VALIDATION] Workspace context not loaded');
      setError('Workspace is not loaded. Please try again.');
      setIsSearching(false);
      return;
    }
    
    // Ensure isSearching is set to true BEFORE async call
    setIsSearching(true);
    setError(null);
    setQueryResults(null);  // Clear previous results to signal new search
    
    try {
      console.debug('📡 [API CALL] Calling api.query() at', new Date().toISOString());
      const result = await api.query(queryToUse, workspaceId);
      console.log('✅ [SEARCH SUCCESS] Got %d results - Confidence: %d', result.sources?.length || 0, result.confidence);
      
      setQueryResults({
        query_id: result.query_id,
        answer_id: result.answer_id,
        answer: result.answer,
        confidence: result.confidence,
        confidence_factors: result.confidence_factors || {
          similarity_avg: 0,
          document_diversity: 0,
          source_coverage: 0,
        },
        sources: result.sources,
        model_used: result.model_used,
        tokens_used: result.tokens_used,
        response_time_ms: result.response_time_ms,
      });
    } catch (err) {
      let errorMsg = 'Search failed. Please try again.';
      
      if (err instanceof Error) {
        const msg = err.message.toLowerCase();
        if (msg.includes('503') || msg.includes('unavailable')) {
          errorMsg = 'Embedding service is currently unavailable. Please try again in a few moments.';
        } else if (msg.includes('timeout') && msg.includes('120000')) {
          errorMsg = 'The query took too long to process. This might indicate the backend services are slow or overwhelmed. Please try again or simplify your query.';
        } else if (msg.includes('timeout')) {
          errorMsg = 'The search request timed out. Try rephrasing your query or uploading more specific documents.';
        } else if (msg.includes('429')) {
          errorMsg = 'Too many requests. Please wait a moment and try again.';
        } else if (msg.includes('no documents') || msg.includes('no results')) {
          errorMsg = 'No matching documents found. Try uploading more documents or use different keywords.';
        } else if (msg.includes('network')) {
          errorMsg = 'Network error. Check your connection and try again.';
        } else if (msg.includes('validation')) {
          errorMsg = 'Request validation failed. Please check your input and try again.';
        } else {
          errorMsg = err.message;
        }
      }
      
      console.error('❌ [SEARCH FAILED] Search error:', errorMsg, err);
      setError(errorMsg);
      setQueryResults(null);  // Clear any partial results
    } finally {
      console.debug('🏁 [SEARCH END] Search completed');
      setIsSearching(false);
    }
  };

  const doSearch = async (searchQuery?: string) => {
    const queryToUse =
      typeof searchQuery === 'string' ? searchQuery : (typeof query === 'string' ? query : '');

    if (!queryToUse.trim()) {
      setError('Please enter a search query');
      setIsSearching(false);
      return;
    }

    if (!workspaceId || !canUseSearch) {
      setError('Workspace is not loaded. Please try again.');
      setIsSearching(false);
      return;
    }

    setIsSearching(true);
    setError(null);
    setSelectedResult(null);
    setSemanticResults([]);
    setSearchLogId(null);
    setQueryResults(null);
    setAnswerFeedbackDraft(createFeedbackDraftState());
    setResultFeedbackDrafts({});
    setShowStep7Format(false);

    try {
      const [searchResult, answerResult] = await Promise.allSettled([
        api.search(queryToUse, workspaceId, { limit: defaultSearchTopK }),
        api.query(queryToUse, workspaceId, { limit: defaultSearchTopK }),
      ]);

      const partialErrors: string[] = [];

      if (searchResult.status === 'fulfilled') {
        const semanticPayload = searchResult.value as SemanticSearchResponsePayload;
        setSemanticResults(semanticPayload.results);
        setSearchLogId(semanticPayload.search_log_id ?? null);
      } else {
        partialErrors.push('Semantic search is unavailable right now.');
      }

      if (answerResult.status === 'fulfilled') {
        const result = answerResult.value;
        setQueryResults({
          query_id: result.query_id,
          answer_id: result.answer_id,
          answer: result.answer,
          confidence: result.confidence,
          confidence_factors: result.confidence_factors || {
            similarity_avg: 0,
            document_diversity: 0,
            source_coverage: 0,
          },
          sources: result.sources,
          model_used: result.model_used,
          tokens_used: result.tokens_used,
          response_time_ms: result.response_time_ms,
        });
      } else {
        partialErrors.push('Grounded answer generation is unavailable right now.');
      }

      if (searchResult.status === 'rejected' && answerResult.status === 'rejected') {
        throw searchResult.reason || answerResult.reason;
      }

      setError(partialErrors.length ? partialErrors.join(' ') : null);
    } catch (err) {
      let errorMsg = 'Search failed. Please try again.';

      if (err instanceof Error) {
        const msg = err.message.toLowerCase();
        if (msg.includes('503') || msg.includes('unavailable')) {
          errorMsg = 'Embedding service is currently unavailable. Please try again in a few moments.';
        } else if (msg.includes('timeout')) {
          errorMsg = 'The search request timed out. Try rephrasing your query or uploading more specific documents.';
        } else if (msg.includes('429')) {
          errorMsg = 'Too many requests. Please wait a moment and try again.';
        } else if (msg.includes('no documents') || msg.includes('no results')) {
          errorMsg = 'No matching documents found. Try uploading more documents or use different keywords.';
        } else if (msg.includes('network')) {
          errorMsg = 'Network error. Check your connection and try again.';
        } else if (msg.includes('validation')) {
          errorMsg = 'Request validation failed. Please check your input and try again.';
        } else {
          errorMsg = err.message;
        }
      }

      setError(errorMsg);
      setSemanticResults([]);
      setSearchLogId(null);
      setQueryResults(null);
    } finally {
      setIsSearching(false);
    }
  };

  const handleOpenSearchResult = async (result: SearchDisplayResult) => {
    setSelectedResult(result);

    if (!workspaceId || !searchLogId) {
      return;
    }

    try {
      const clickTargetId = result.sourceKind === 'note' ? result.raw.document_id : result.raw.chunk_id;
      await api.logSearchClick(workspaceId, searchLogId, clickTargetId);
    } catch (err) {
      console.error('Failed to log semantic search click:', err);
    }
  };

  const suggested = generateSuggestions();

  const feedbackResultIds = useMemo(() => semanticFeedbackResultIds(semanticResults), [semanticResults]);
  const feedbackResultSnapshot = useMemo(() => semanticFeedbackResultSnapshot(semanticResults), [semanticResults]);

  const toSubmittedFeedbackState = (item: {
    feedback_id: string;
    feedback_type: string;
    reason_code?: string;
    comment?: string;
  }): SubmittedFeedbackState => ({
    feedbackId: item.feedback_id,
    feedbackType: item.feedback_type as FeedbackType,
    reasonCode: (item.reason_code || undefined) as FeedbackReasonCode | undefined,
    comment: item.comment,
    updatedExisting: true,
  });

  useEffect(() => {
    let cancelled = false;
    const loadFeedbackForCurrentSearch = async () => {
      if (!queryResults?.answer_id) {
        setAnswerFeedbackDraft(createFeedbackDraftState());
        setResultFeedbackDrafts({});
        return;
      }
      try {
        const items = await api.listCurrentAnswerFeedback(queryResults.answer_id, {
          search_log_id: searchLogId || undefined,
          query_id: queryResults.query_id,
        });

        if (cancelled) {
          return;
        }

        const nextAnswerDraft = createFeedbackDraftState();
        const nextResultDrafts: Record<string, FeedbackDraftState> = {};

        for (const item of items) {
          const submitted = toSubmittedFeedbackState(item);
          const hydratedDraft: FeedbackDraftState = {
            ...createFeedbackDraftState(),
            feedbackType: submitted.feedbackType,
            feedbackReason: (submitted.reasonCode || '') as FeedbackReasonCode | '',
            feedbackComment: submitted.comment || '',
            submitted,
          };

          if (item.target_kind === 'answer') {
            nextAnswerDraft.feedbackType = hydratedDraft.feedbackType;
            nextAnswerDraft.feedbackReason = hydratedDraft.feedbackReason;
            nextAnswerDraft.feedbackComment = hydratedDraft.feedbackComment;
            nextAnswerDraft.submitted = hydratedDraft.submitted;
          } else if (item.target_kind === 'result' && item.target_result_id) {
            nextResultDrafts[item.target_result_id] = hydratedDraft;
          }
        }

        setAnswerFeedbackDraft(nextAnswerDraft);
        setResultFeedbackDrafts(nextResultDrafts);
      } catch (err) {
        console.error('Failed to load scoped feedback for current search:', err);
      }
    };
    loadFeedbackForCurrentSearch();
    return () => {
      cancelled = true;
    };
  }, [queryResults?.answer_id, queryResults?.query_id, searchLogId]);

  if (workspaceId && !canUseSearch) {
    return (
      <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>
        <div style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '16px 18px', color: TT.inkMuted, fontSize: 11, letterSpacing: '0.04em' }}>
          Your current role does not allow AI search in this workspace.
        </div>
      </div>
    );
  }

  const submitFeedback = async (targetKind: 'answer' | 'result', targetResultId?: string) => {
    const scopedResultId = targetKind === 'result' ? targetResultId || '' : '';
    const draft = targetKind === 'answer' ? answerFeedbackDraft : getResultFeedbackDraft(scopedResultId);
    const patchDraft = (
      updater: Partial<FeedbackDraftState> | ((current: FeedbackDraftState) => FeedbackDraftState),
    ) => {
      if (targetKind === 'answer') {
        patchAnswerFeedbackDraft(updater);
        return;
      }
      if (!scopedResultId) return;
      patchResultFeedbackDraft(scopedResultId, updater);
    };

    if (!queryResults?.answer_id || !draft.feedbackType) {
      patchDraft({ error: 'Select a feedback option before submitting.' });
      return;
    }
    if (targetKind === 'result' && !targetResultId) {
      patchDraft({ error: 'No search result selected for result-level feedback.' });
      return;
    }
    if (requiresReasonForSemanticFeedback(draft.feedbackType) && !draft.feedbackReason) {
      patchDraft({ error: 'Select a reason for partial/negative feedback.' });
      return;
    }

    patchDraft({ submitting: true, message: null, error: null });

    try {
      const result = await api.submitAnswerFeedback(queryResults.answer_id, {
        feedback_type: draft.feedbackType,
        target_kind: targetKind,
        target_result_id: targetResultId,
        search_log_id: searchLogId || undefined,
        query_id: queryResults.query_id,
        query_text: query,
        rating_value: ratingForSemanticFeedback(draft.feedbackType),
        reason_code: (draft.feedbackReason || undefined) as FeedbackReasonCode | undefined,
        comment: draft.feedbackComment || undefined,
        result_ids: feedbackResultIds,
        result_snapshot: feedbackResultSnapshot,
        answer_snapshot: {
          answer_id: queryResults.answer_id,
          confidence: queryResults.confidence,
          sources: queryResults.sources,
        },
        retrieval_diagnostics: {
          top_k: 10,
          workspace_id: workspaceId,
          source_count: semanticResults.length,
        },
      });

      const submitted: SubmittedFeedbackState = {
        feedbackId: result.feedback_id,
        feedbackType: draft.feedbackType,
        reasonCode: (draft.feedbackReason || undefined) as FeedbackReasonCode | undefined,
        comment: draft.feedbackComment || undefined,
        updatedExisting: result.updated_existing,
      };

      const messagePrefix = result.updated_existing ? 'Feedback updated.' : 'Feedback submitted.';
      const weightMessage = targetKind === 'answer' && result.chunks_updated.length > 0
        ? ` ${result.chunks_updated.length} source chunk(s) reweighted.`
        : '';
      if (result.search_log_id) {
        setSearchLogId((current) => current || result.search_log_id || current);
      }
      patchDraft((current) => ({
        ...current,
        submitting: false,
        error: null,
        message: `${messagePrefix}${weightMessage}`,
        submitted,
      }));
    } catch (err) {
      let errorMsg = 'Failed to submit feedback. Please try again.';
      if (err instanceof Error) {
        const msg = err.message.toLowerCase();
        if (msg.includes('timeout')) {
          errorMsg = 'Request timed out. Please try again.';
        } else if (msg.includes('network')) {
          errorMsg = 'Network error. Check your connection and try again.';
        } else {
          errorMsg = err.message;
        }
      }
      patchDraft((current) => ({
        ...current,
        submitting: false,
        error: errorMsg,
        message: null,
      }));
    } finally {
      patchDraft((current) => ({
        ...current,
        submitting: false,
      }));
    }
  };

  const renderFeedbackPanel = ({
    title,
    subtitle,
    draft,
    patchDraft,
    targetKind,
    targetResultId,
    compact = false,
  }: {
    title: string;
    subtitle?: string;
    draft: FeedbackDraftState;
    patchDraft: (updater: FeedbackDraftUpdater) => void;
    targetKind: 'answer' | 'result';
    targetResultId?: string;
    compact?: boolean;
  }) => {
    const needsReason = Boolean(draft.feedbackType && requiresReasonForSemanticFeedback(draft.feedbackType));
    const savedFeedback = draft.submitted
      ? `${SEMANTIC_FEEDBACK_TYPE_LABELS[draft.submitted.feedbackType]}${draft.submitted.reasonCode ? ` - ${draft.submitted.reasonCode}` : ''}`
      : null;

    return (
      <div
        onClick={(event) => event.stopPropagation()}
        style={{
          background: compact ? TT.inkRaised : 'rgba(245,230,66,0.04)',
          border: `1px solid ${compact ? TT.inkBorder : 'rgba(245,230,66,0.15)'}`,
          borderRadius: 3,
          padding: compact ? '10px 12px' : '12px 14px',
          marginTop: compact ? 10 : 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: compact ? TT.inkMuted : TT.yolk, marginBottom: subtitle ? 4 : 0 }}>
              {title}
            </div>
            {subtitle && (
              <div style={{ fontFamily: TT.fontBody, fontSize: 11, lineHeight: 1.5, color: TT.inkMuted }}>
                {subtitle}
              </div>
            )}
          </div>
          {savedFeedback && (
            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, lineHeight: 1.4, color: TT.inkMid, textAlign: 'right', maxWidth: 180 }}>
              Saved: {savedFeedback}
            </span>
          )}
        </div>

        {draft.message && (
          <div style={{ background: 'rgba(76,175,80,0.1)', border: '1px solid rgba(76,175,80,0.3)', borderRadius: 3, padding: '8px 10px', marginBottom: 8, color: '#4CAF50', fontSize: 10, fontFamily: TT.fontMono }}>
            {draft.message}
          </div>
        )}
        {draft.error && (
          <div style={{ background: 'rgba(255,69,69,0.1)', border: '1px solid rgba(255,69,69,0.3)', borderRadius: 3, padding: '8px 10px', marginBottom: 8, color: TT.error, fontSize: 10, fontFamily: TT.fontMono }}>
            {draft.error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <button
            onClick={(event) => {
              event.stopPropagation();
              applyFeedbackTypeSelection(patchDraft, 'correct');
            }}
            style={{ flex: 1, minWidth: 110, height: 30, background: draft.feedbackType === 'correct' ? '#4CAF50' : TT.yolk, border: `1px solid ${draft.feedbackType === 'correct' ? '#4CAF50' : TT.yolk}`, borderRadius: 3, color: TT.inkBlack, fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer' }}
          >
            Correct
          </button>
          <button
            onClick={(event) => {
              event.stopPropagation();
              applyFeedbackTypeSelection(patchDraft, 'partially_correct');
            }}
            style={{ flex: 1, minWidth: 110, height: 30, background: draft.feedbackType === 'partially_correct' ? TT.yolk : TT.inkRaised, border: `1px solid ${draft.feedbackType === 'partially_correct' ? TT.yolk : TT.inkBorder}`, borderRadius: 3, color: draft.feedbackType === 'partially_correct' ? TT.inkBlack : TT.inkMuted, fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer' }}
          >
            Partial
          </button>
          <button
            onClick={(event) => {
              event.stopPropagation();
              applyFeedbackTypeSelection(patchDraft, 'wrong_result');
            }}
            style={{ flex: 1, minWidth: 110, height: 30, background: draft.feedbackType === 'wrong_result' ? TT.error : TT.inkRaised, border: `1px solid ${draft.feedbackType === 'wrong_result' ? TT.error : TT.inkBorder}`, borderRadius: 3, color: draft.feedbackType === 'wrong_result' ? TT.snow : TT.inkMuted, fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer' }}
          >
            Wrong / Irrelevant
          </button>
        </div>

        {(draft.showDetails || needsReason) && (
          <div style={{ display: 'grid', gap: 8, marginBottom: 8 }}>
            <select
              value={draft.feedbackReason}
              onChange={(event) => {
                event.stopPropagation();
                patchDraft({ feedbackReason: (event.target.value as FeedbackReasonCode) || '' });
              }}
              onClick={(event) => event.stopPropagation()}
              style={{ height: 32, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 10, padding: '0 10px' }}
            >
              <option value="">Select reason</option>
              {SEMANTIC_FEEDBACK_REASON_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <textarea
              value={draft.feedbackComment}
              onChange={(event) => {
                event.stopPropagation();
                patchDraft({ feedbackComment: event.target.value });
              }}
              onClick={(event) => event.stopPropagation()}
              placeholder="Optional details to help improve retrieval and highlights"
              style={{ width: '100%', minHeight: compact ? 56 : 64, padding: '8px 12px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.snow, fontFamily: TT.fontMono, fontSize: 11, resize: 'vertical', boxSizing: 'border-box' }}
            />
          </div>
        )}

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={(event) => {
              event.stopPropagation();
              void submitFeedback(targetKind, targetResultId);
            }}
            disabled={draft.submitting || !draft.feedbackType}
            style={{ flex: 1, height: 30, background: TT.yolk, border: `1px solid ${TT.yolk}`, borderRadius: 3, color: TT.inkBlack, fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', cursor: draft.submitting || !draft.feedbackType ? 'not-allowed' : 'pointer', opacity: draft.submitting || !draft.feedbackType ? 0.6 : 1 }}
          >
            {draft.submitting ? 'Saving...' : targetKind === 'answer' ? 'Save answer feedback' : 'Save result feedback'}
          </button>
          <button
            onClick={(event) => {
              event.stopPropagation();
              patchDraft((current) => ({ ...current, showDetails: !current.showDetails }));
            }}
            style={{ minWidth: 128, height: 30, background: 'transparent', border: `1px dashed ${TT.inkBorder}`, borderRadius: 3, color: TT.inkMuted, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.05em', textTransform: 'uppercase', cursor: 'pointer' }}
          >
            {draft.showDetails || needsReason ? 'Hide details' : 'Add details'}
          </button>
        </div>
      </div>
    );
  };

  // STEP 8: Load credibility analytics
  const loadCredibilityAnalytics = async () => {
    console.log('📊 [ANALYTICS START] Loading credibility analytics - Workspace: %s', workspaceId);
    
    if (!workspaceId) {
      console.warn('⚠️ [VALIDATION] No workspace ID found');
      return;
    }
    
    setAnalyticsLoading(true);
    try {
      console.debug('📡 [API CALLS] Fetching scores and metrics');
      const [scores, metrics] = await Promise.all([
        api.getChunkCredibilityScores(workspaceId, 20),
        api.getModelEvaluationMetrics(workspaceId),
      ]);
      
      console.log('✅ [ANALYTICS SUCCESS] Loaded credibility scores: %d, metrics received', scores.length);
      setCredibilityScores(scores);
      setModelMetrics(metrics);
      setShowCredibilityAnalytics(true);
      console.debug('💾 [STATE UPDATE] Analytics state updated');
    } catch (err) {
      let errorMsg = 'Failed to load analytics. Please try again.';
      
      if (err instanceof Error) {
        const msg = err.message.toLowerCase();
        if (msg.includes('timeout')) {
          errorMsg = 'Request timed out. Please try again.';
        } else if (msg.includes('network')) {
          errorMsg = 'Network error. Check your connection and try again.';
        } else {
          errorMsg = err.message;
        }
      }
      
      console.error('❌ [ANALYTICS FAILED] Failed to load analytics:', errorMsg, err);
    } finally {
      setAnalyticsLoading(false);
      console.debug('✅ [ANALYTICS COMPLETE] Analytics loading finished');
    }
  };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>

      {/* ── Header ──────────────────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>AI-Powered</span>
          </div>
          <button
            onClick={loadCredibilityAnalytics}
            disabled={analyticsLoading || !workspaceId}
            style={{
              fontSize: 8.5,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              background: showCredibilityAnalytics ? TT.yolk : 'transparent',
              border: `1px solid ${showCredibilityAnalytics ? TT.yolk : TT.inkMid}`,
              color: showCredibilityAnalytics ? TT.inkBlack : TT.yolk,
              borderRadius: 2,
              padding: '4px 10px',
              cursor: analyticsLoading || !workspaceId ? 'not-allowed' : 'pointer',
              opacity: analyticsLoading || !workspaceId ? 0.5 : 1,
              transition: 'all 0.15s'
            }}
            onMouseEnter={(e) => {
              if (!analyticsLoading && workspaceId && !showCredibilityAnalytics) {
                (e.currentTarget as HTMLElement).style.borderColor = TT.yolk;
              }
            }}
            onMouseLeave={(e) => {
              if (!showCredibilityAnalytics) {
                (e.currentTarget as HTMLElement).style.borderColor = TT.inkMid;
              }
            }}
          >
            {analyticsLoading ? 'Loading...' : 'Analytics'}
          </button>
        </div>
        <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 44, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase' }}>
          <span style={{ color: TT.yolk }}>S</span>EARCH
        </h1>
        <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
      </div>

      {/* ── Search bar ──────────────────────────────────────────── */}
      <div style={{ position: 'relative', marginBottom: 20 }}>
        <Search size={15} color={TT.inkMuted} style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doSearch()}
          placeholder="Ask anything about your knowledge base..."
          style={{
            width: '100%', height: 52,
            background: TT.inkRaised,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: 3,
            color: TT.snow,
            fontFamily: TT.fontBody,
            fontSize: 15,
            paddingLeft: 42, paddingRight: 130,
            outline: 'none',
            boxSizing: 'border-box',
            transition: 'border-color 0.15s, box-shadow 0.15s',
          }}
          onFocus={(e) => {
            (e.target as HTMLInputElement).style.borderColor = TT.yolk;
            (e.target as HTMLInputElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)';
          }}
          onBlur={(e) => {
            (e.target as HTMLInputElement).style.borderColor = TT.inkBorder;
            (e.target as HTMLInputElement).style.boxShadow = 'none';
          }}
        />
        <button
          onClick={() => doSearch()}
          style={{
            position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)',
            height: 40, padding: '0 16px',
            background: TT.yolk, border: `2px solid ${TT.yolk}`, borderRadius: 3,
            color: TT.inkBlack, fontFamily: TT.fontDisplay,
            fontSize: 14, letterSpacing: '0.1em', textTransform: 'uppercase',
            cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
            transition: 'all 0.15s',
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = TT.yolkBright; (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = TT.yolk; }}
        >
          <Sparkles size={12} /> Search
        </button>
      </div>

      {/* ── Suggested queries ────────────────────────────────────── */}
      {!query && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 28 }}>
          <span style={{ fontSize: 9.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: TT.inkMid }}>Try:</span>
          {suggested.map((q) => (
            <button
              key={q}
              onClick={async () => {
                setQuery(q);
                // Call doSearch with the query directly to avoid state timing issues
                await doSearch(q);
              }}
              style={{
                height: 28, padding: '0 12px',
                background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3,
                color: TT.inkMuted, fontFamily: TT.fontMono, fontSize: 10.5, letterSpacing: '0.04em',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* ── Tag filters ──────────────────────────────────────────── */}
      {query && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 20 }}>
          <Filter size={11} color={TT.inkMuted} />
          <span style={{ fontSize: 9.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: TT.inkMid }}>Filter:</span>
          {allTags.slice(0, 8).map((tag) => {
            const active = activeFilters.includes(tag);
            return (
              <button
                key={tag}
                onClick={() => toggleFilter(tag)}
                style={{
                  height: 24, padding: '0 10px',
                  background: active ? TT.yolk : TT.inkRaised,
                  border: `1px solid ${active ? TT.yolk : TT.inkBorder}`,
                  borderRadius: 2,
                  color: active ? TT.inkBlack : TT.inkMuted,
                  fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.05em', textTransform: 'uppercase',
                  cursor: 'pointer', transition: 'all 0.15s',
                }}
              >
                {tag}
              </button>
            );
          })}
          {activeFilters.length > 0 && (
            <button
              onClick={() => setActiveFilters([])}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: TT.inkMid, display: 'flex', alignItems: 'center', gap: 3, fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: 0, transition: 'color 0.15s' }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.error; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMid; }}
            >
              <X size={10} /> Clear
            </button>
          )}
        </div>
      )}

      {/* ── STEP 8: Credibility Analytics Panel ──────────────────── */}
      {showCredibilityAnalytics && modelMetrics && credibilityScores && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          style={{ background: 'rgba(245,230,66,0.06)', border: `1px solid rgba(245,230,66,0.2)`, borderRadius: 3, padding: '16px', marginBottom: 16 }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk }}>Model Learning Analytics</div>
            <button
              onClick={() => setShowCredibilityAnalytics(false)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: TT.inkMuted, padding: 0 }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.yolk; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; }}
            >
              <X size={14} />
            </button>
          </div>
          
          {/* Metrics summary */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
            <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '10px 12px' }}>
              <div style={{ fontSize: 9, color: TT.inkMid, letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 4 }}>Total Feedback</div>
              <div style={{ fontSize: 16, fontWeight: 600, color: TT.yolk }}>{modelMetrics.total_feedback}</div>
            </div>
            <div style={{ background: '#4CAF5015', border: '1px solid rgba(76,175,80,0.3)', borderRadius: 2, padding: '10px 12px' }}>
              <div style={{ fontSize: 9, color: '#4CAF50', letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 4 }}>Approval Rate</div>
              <div style={{ fontSize: 16, fontWeight: 600, color: '#4CAF50' }}>{Math.round(modelMetrics.approval_rate * 100)}%</div>
            </div>
            <div style={{ background: 'rgba(255,69,69,0.08)', border: '1px solid rgba(255,69,69,0.2)', borderRadius: 2, padding: '10px 12px' }}>
              <div style={{ fontSize: 9, color: TT.error, letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 4 }}>Rejection Rate</div>
              <div style={{ fontSize: 16, fontWeight: 600, color: TT.error }}>{Math.round(modelMetrics.rejection_rate * 100)}%</div>
            </div>
            <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '10px 12px' }}>
              <div style={{ fontSize: 9, color: TT.inkMid, letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 4 }}>Avg Rating</div>
              <div style={{ fontSize: 16, fontWeight: 600, color: TT.snow }}>{modelMetrics.average_rating.toFixed(1)}/5.0 ⭐</div>
            </div>
          </div>
          
          {/* Top credible chunks */}
          <div>
            <div style={{ fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 8 }}>Top Credible Chunks</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 120, overflowY: 'auto' }}>
              {credibilityScores.slice(0, 5).map((score) => (
                <div key={`${score.chunk_id}-${score.document_id}`} style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '8px 10px', fontSize: 9 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ color: TT.inkSubtle, wordBreak: 'break-all' }}>{score.chunk_id.substring(0, 20)}...</span>
                    <span style={{ color: '#4CAF50', fontWeight: 600 }}>{score.credibility_score.toFixed(2)}x</span>
                  </div>
                  <div style={{ fontSize: 8, color: TT.inkMid, display: 'flex', gap: 8 }}>
                    <span>✓ {score.positive_feedback} verified</span>
                    <span>✗ {score.negative_feedback} rejected</span>
                    <span>{Math.round(score.accuracy_rate * 100)}% accurate</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </motion.div>
      )}

      {/* ── Results ──────────────────────────────────────────────── */}
      <AnimatePresence mode="wait">
        {isSearching ? (
          <motion.div key="searching" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '80px 0' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 24, height: 24, borderRadius: '50%', border: `2px solid ${TT.yolk}`, borderTopColor: 'transparent', animation: 'spin 0.6s linear infinite' }} />
              <span style={{ fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted }}>Searching with AI...</span>
            </div>
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
          </motion.div>
        ) : query ? (
          <motion.div key="results" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            {error && (
              <div style={{ background: 'rgba(255,69,69,0.1)', border: '1px solid rgba(255,69,69,0.3)', borderRadius: 3, padding: '12px 16px', marginBottom: 16, color: TT.error, fontSize: 11, fontFamily: TT.fontMono }}>
                {error}
              </div>
            )}
            
            {/* Result count row */}
             <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
               <span style={{ fontSize: 10.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: TT.inkMuted }}>
                 <span style={{ color: TT.snow }}>{filteredResults.length}</span> results
                 {showSimilarityEvidence && searchConfidence !== null && ` • Search confidence: ${searchConfidence}%`}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {queryResults && showSimilarityEvidence && (
                  <span
                    style={{
                      fontSize: 9,
                      letterSpacing: '0.05em',
                      textTransform: 'uppercase',
                      color: hasGroundedAnswer ? TT.snow : TT.inkMuted,
                      padding: '2px 7px',
                      borderRadius: 999,
                      border: `1px solid ${hasGroundedAnswer ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                      background: hasGroundedAnswer ? 'rgba(245,230,66,0.07)' : TT.inkRaised,
                    }}
                  >
                    {hasGroundedAnswer
                      ? `Answer confidence ${Math.round(queryResults.confidence * 100)}%`
                      : 'No grounded answer'}
                  </span>
                )}
                <Brain size={11} color={TT.yolk} />
                 <span style={{ fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk }}>AI-Enhanced</span>
               </div>
             </div>

             {queryResults && hasGroundedAnswer && (
               <div style={{ background: 'rgba(245,230,66,0.04)', border: `1px solid rgba(245,230,66,0.15)`, borderRadius: 3, padding: '14px 16px', marginBottom: 16 }}>
                 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
                   <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk }}>
                     Overall Search Answer
                   </div>
                   {showSimilarityEvidence ? (
                     <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: queryResults.confidence > 0.8 ? '#4CAF50' : queryResults.confidence > 0.5 ? TT.yolk : TT.error }}>
                       {Math.round(queryResults.confidence * 100)}% confidence
                     </span>
                   ) : null}
                 </div>
                 <p style={{ fontFamily: TT.fontBody, fontSize: 12, lineHeight: 1.6, color: TT.snow, margin: 0 }}>
                   {queryResults.answer}
                 </p>
                 {renderFeedbackPanel({
                   title: 'Feedback for the overall answer',
                   subtitle: 'Use this only for the generated answer as a whole. Result cards below capture feedback for each individual search output.',
                   draft: answerFeedbackDraft,
                   patchDraft: patchAnswerFeedbackDraft,
                   targetKind: 'answer',
                 })}
               </div>
             )}

             {filteredResults.length === 0 && !error ? (
               <div style={{ textAlign: 'center', padding: '60px 0' }}>
                 <Lightbulb size={36} color={TT.inkMid} style={{ margin: '0 auto 16px' }} />
                <div style={{ fontFamily: TT.fontDisplay, fontSize: 24, letterSpacing: '0.06em', color: TT.snow, marginBottom: 8 }}>NO STRONG MATCHES</div>
                <p style={{ fontSize: 10.5, letterSpacing: '0.04em', color: TT.inkMuted, textTransform: 'uppercase' }}>Try a more specific natural-language query or add notes with clearer source content</p>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {filteredResults.map((result, index) => (
                  <motion.div
                    key={result.note.id}
                    initial={{ opacity: 0, x: -14 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.04 }}
                  >
                    <div
                      onClick={() => handleOpenSearchResult(result)}
                      style={{
                        background: TT.inkDeep,
                        border: `1px solid ${TT.inkBorder}`,
                        borderRadius: 3, padding: '14px 16px',
                        cursor: 'pointer', transition: 'border-color 0.15s, border-left-width 0.1s',
                      }}
                      onMouseEnter={(e) => {
                        const el = e.currentTarget as HTMLElement;
                        el.style.borderColor = 'rgba(245,230,66,0.2)';
                        el.style.borderLeftColor = TT.yolk;
                        el.style.borderLeftWidth = '3px';
                      }}
                      onMouseLeave={(e) => {
                        const el = e.currentTarget as HTMLElement;
                        el.style.borderColor = TT.inkBorder;
                        el.style.borderLeftWidth = '1px';
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          {/* Title + match score */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 7 }}>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 13, fontWeight: 500, color: TT.snow, letterSpacing: '0.02em' }}>
                              {result.note.title}
                            </span>
                            {showSimilarityEvidence ? (
                              <>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '1px 7px', background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.15)', borderRadius: 2, flexShrink: 0 }}>
                                  <Brain size={9} color={TT.yolk} />
                                  <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.yolk }}>{Math.round(result.score * 100)}% match</span>
                                </div>
                                <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: result.confidence === 'low' ? TT.inkMuted : TT.snow }}>
                                  {confidenceLabel(result.confidence, result.confidenceScore)}
                                </span>
                              </>
                            ) : null}
                          </div>

                          {/* Highlights */}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 10 }}>
                            {smartHighlightsEnabled ? (
                              result.highlights.slice(0, 2).map((h, i) => (
                                <div key={`${h.start_offset}-${i}`} style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                                  <Quote size={9} color={h.confidence === 'low' ? TT.inkMid : TT.yolk} style={{ marginTop: 3, flexShrink: 0 }} />
                                  <span style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, lineHeight: 1.5, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                                    {renderHighlightedText(h.text, h.matched_terms)}
                                  </span>
                                </div>
                              ))
                            ) : (
                              <span style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, lineHeight: 1.5, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                                {result.legacyHighlight || result.note.content.slice(0, 180)}
                              </span>
                            )}
                          </div>

                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 10 }}>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.15)', borderRadius: 2, color: TT.yolk }}>
                              {result.sourceKind}
                            </span>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                              {result.sourceType}
                            </span>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                              Chunk {result.chunkIndex + 1}
                            </span>
                            {smartHighlightsEnabled && result.highlights[0]?.heading && (
                              <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                                {result.highlights[0].heading}
                              </span>
                            )}
                            {result.tokenCount > 0 && (
                              <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                                {result.tokenCount} tokens
                              </span>
                            )}
                          </div>

                          {/* Tags + timestamp */}
                           <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 5 }}>
                             {result.note.tags.slice(0, 4).map((tag) => (
                               <span key={tag} style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                                 {tag}
                              </span>
                            ))}
                            <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMid }}>
                              <Clock size={9} />
                              {Number.isNaN(result.note.createdAt.getTime())
                                ? 'Semantic match'
                               : formatDistanceToNow(result.note.createdAt, { addSuffix: true })}
                             </span>
                           </div>

                           {renderFeedbackPanel({
                             title: 'Feedback for this result',
                             subtitle: 'Rate this specific search output separately from the overall answer.',
                             draft: getResultFeedbackDraft(result.raw.chunk_id),
                             patchDraft: (updater) => patchResultFeedbackDraft(result.raw.chunk_id, updater),
                             targetKind: 'result',
                             targetResultId: result.raw.chunk_id,
                             compact: true,
                           })}
                         </div>

                         <ArrowRight size={14} color={TT.inkMid} style={{ flexShrink: 0, marginTop: 2 }} />
                       </div>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </motion.div>
        ) : (
          /* Empty state — feature cards */
          <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
            {[
              { Icon: Brain,    title: 'Semantic Search',   desc: 'Our AI understands the meaning behind your queries, not just keywords. Search with natural language and get relevant results.' },
              { Icon: Sparkles, title: 'Smart Highlights',  desc: 'AI automatically highlights the most relevant sections of your notes, saving you time when reviewing search results.' },
            ].map(({ Icon, title, desc }) => (
              <div key={title} style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid rgba(245,230,66,0.3)`, borderRadius: 3, padding: '20px 20px' }}>
                <div style={{ width: 32, height: 32, borderRadius: 2, background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12 }}>
                  <Icon size={15} color={TT.yolk} />
                </div>
                <div style={{ fontFamily: TT.fontDisplay, fontSize: 20, letterSpacing: '0.06em', color: TT.snow, marginBottom: 8 }}>{title.toUpperCase()}</div>
                <p style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.65 }}>{desc}</p>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Note detail modal ────────────────────────────────────── */}
      {selectedResult && (
        <div
          onClick={() => setSelectedResult(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={(e) => e.stopPropagation()}
            style={{
              background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`,
              borderTop: `3px solid ${TT.yolk}`,
              borderRadius: 4, maxWidth: 680, width: '100%',
              maxHeight: '82vh', overflowY: 'auto',
              fontFamily: TT.fontMono,
            }}
          >
            <div style={{ padding: '22px 24px' }}>
              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                <h2 style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.06em', color: TT.snow, lineHeight: 1 }}>
                  <span style={{ color: TT.yolk }}>{selectedResult.note.title.charAt(0)}</span>{selectedResult.note.title.slice(1)}
                </h2>
                <button
                  onClick={() => setSelectedResult(null)}
                  style={{ background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2, cursor: 'pointer', padding: '4px 6px', color: TT.inkMuted, transition: 'all 0.15s' }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.error; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)'; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
                >
                  <X size={12} />
                </button>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
                <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.18)', borderRadius: 999, color: TT.yolk }}>
                  {selectedResult.sourceKind}
                </span>
                <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 7px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 999, color: TT.inkMuted }}>
                  {selectedResult.sourceType}
                </span>
                <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 7px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 999, color: TT.inkMuted }}>
                  Chunk {selectedResult.chunkIndex + 1}
                </span>
                {selectedResult.tokenCount > 0 && (
                  <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 7px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 999, color: TT.inkMuted }}>
                    {selectedResult.tokenCount} tokens
                  </span>
                )}
              </div>

              {/* STEP 7: Answer Summary (if displayed from search results) */}
              {queryResults && hasGroundedAnswer && (
                <div style={{ background: 'rgba(245,230,66,0.04)', border: `1px solid rgba(245,230,66,0.15)`, borderRadius: 3, padding: '12px 14px', marginBottom: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <span style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk }}>AI-Generated Answer</span>
                    <span style={{ fontSize: 9, color: queryResults.confidence > 0.8 ? '#4CAF50' : queryResults.confidence > 0.5 ? TT.yolk : TT.error, fontWeight: 600 }}>
                      {Math.round(queryResults.confidence * 100)}% confidence
                    </span>
                  </div>
                  <p style={{ fontFamily: TT.fontBody, fontSize: 12, lineHeight: 1.6, color: TT.snow, marginBottom: 0 }}>
                    {queryResults.answer}
                  </p>
                  <button
                    onClick={() => setShowStep7Format(!showStep7Format)}
                    style={{
                      marginTop: 8,
                      fontSize: 8.5,
                      letterSpacing: '0.05em',
                      textTransform: 'uppercase',
                      background: 'transparent',
                      border: 'none',
                      color: TT.yolk,
                      cursor: 'pointer',
                      opacity: 0.8,
                      transition: 'opacity 0.15s'
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.opacity = '1'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.opacity = '0.8'; }}
                  >
                    {showStep7Format ? '✕ Hide STEP 7 Format' : '→ View STEP 7 Format'}
                  </button>
                  
                  {/* STEP 7 Format JSON */}
                  {showStep7Format && (
                    <div style={{ marginTop: 8, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '8px 10px', fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted, maxHeight: 140, overflowY: 'auto' }}>
                      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordWrap: 'break-word', color: TT.snow }}>
{`{
  "answer": "${queryResults.answer.substring(0, 50)}...",
  "confidence": ${(queryResults.confidence).toFixed(2)},
  "sources": [
    ${queryResults.sources.slice(0, 2).map(s => `{ "chunk_id": "${s.chunk_id}", "document_id": "${s.document_id}", "similarity": ${s.similarity.toFixed(2)} }`).join(',\n    ')}
  ]
}`}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px', marginBottom: 16 }}>
                <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 8 }}>
                  Related Document Details
                </div>
                <div style={{ display: 'grid', gap: 6 }}>
                  <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.snow }}>
                    Document ID: <span style={{ color: TT.inkSubtle }}>{selectedResult.raw.document_id}</span>
                  </div>
                  <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.snow }}>
                    Chunk ID: <span style={{ color: TT.inkSubtle }}>{selectedResult.raw.chunk_id}</span>
                  </div>
                </div>
              </div>

              {selectedResult.note.tags.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 16 }}>
                  {selectedResult.note.tags.map((tag) => (
                    <span key={tag} style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 8px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {selectedResult.contextBefore && (
                <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px', marginBottom: 12 }}>
                  <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 6 }}>
                    Context Before
                  </div>
                  <p style={{ fontFamily: TT.fontBody, fontSize: 11.5, lineHeight: 1.6, color: TT.inkMuted, margin: 0 }}>
                    {selectedResult.contextBefore}
                  </p>
                </div>
              )}

              <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '14px 16px', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                  <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk }}>
                    Matched Source Section
                  </div>
                  <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: TT.inkMuted }}>
                    {confidenceLabel(selectedResult.confidence, selectedResult.confidenceScore)}
                  </span>
                </div>
                <p style={{ fontFamily: TT.fontBody, fontSize: 13, lineHeight: 1.7, color: TT.inkSubtle, whiteSpace: 'pre-wrap', margin: 0 }}>
                  {smartHighlightsEnabled
                    ? renderHighlightedText(
                        selectedResult.note.content,
                        selectedResult.highlights.flatMap((highlight) => highlight.matched_terms || [])
                      )
                    : selectedResult.note.content}
                </p>
              </div>

              {smartHighlightsEnabled && selectedResult.highlights.length > 0 && (
                <div style={{ background: 'rgba(245,230,66,0.04)', border: `1px solid rgba(245,230,66,0.15)`, borderRadius: 3, padding: '12px 14px', marginBottom: 12 }}>
                  <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk, marginBottom: 8 }}>
                    Smart Highlights
                  </div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {selectedResult.highlights.map((highlight, index) => (
                      <div key={`${highlight.start_offset}-${index}`}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.inkMid }}>
                            chars {highlight.start_offset}-{highlight.end_offset}
                          </span>
                          {highlight.heading && (
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.inkMuted }}>
                              {highlight.heading}
                            </span>
                          )}
                        </div>
                        <p style={{ fontFamily: TT.fontBody, fontSize: 11.5, lineHeight: 1.6, color: TT.inkSubtle, margin: 0 }}>
                          {renderHighlightedText(highlight.text, highlight.matched_terms)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedResult.contextAfter && (
                <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px', marginBottom: 12 }}>
                  <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 6 }}>
                    Context After
                  </div>
                  <p style={{ fontFamily: TT.fontBody, fontSize: 11.5, lineHeight: 1.6, color: TT.inkMuted, margin: 0 }}>
                    {selectedResult.contextAfter}
                  </p>
                </div>
              )}

              {Object.keys(selectedResult.metadata).length > 0 && (
                <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px', marginBottom: 16 }}>
                  <div style={{ fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 8 }}>
                    Metadata
                  </div>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: TT.fontMono, fontSize: 10, lineHeight: 1.6, color: TT.inkSubtle }}>
                    {JSON.stringify(selectedResult.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Footer */}
              <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: `1px solid ${TT.inkBorder}`, paddingTop: 12 }}>
                <span style={{ fontSize: 9.5, color: TT.inkMid, letterSpacing: '0.04em' }}>
                  Semantic match grounded in matched source text
                </span>
                {queryResults?.confidence && hasGroundedAnswer && showSimilarityEvidence && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9.5, color: TT.yolk }}>
                    <Brain size={10} /> {Math.round(queryResults.confidence * 100)}% confidence
                  </span>
                )}
              </div>

              <div style={{ marginTop: 16, borderTop: `1px solid ${TT.inkBorder}`, paddingTop: 12, display: 'grid', gap: 12 }}>
                {queryResults && hasGroundedAnswer && renderFeedbackPanel({
                  title: 'Overall answer feedback',
                  subtitle: 'This is separate from result-specific feedback and applies only to the generated answer.',
                  draft: answerFeedbackDraft,
                  patchDraft: patchAnswerFeedbackDraft,
                  targetKind: 'answer',
                })}
                {renderFeedbackPanel({
                  title: 'Feedback for this result',
                  subtitle: 'This saves against the exact note chunk and highlight you are viewing now.',
                  draft: getResultFeedbackDraft(selectedResult.raw.chunk_id),
                  patchDraft: (updater) => patchResultFeedbackDraft(selectedResult.raw.chunk_id, updater),
                  targetKind: 'result',
                  targetResultId: selectedResult.raw.chunk_id,
                })}
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}
