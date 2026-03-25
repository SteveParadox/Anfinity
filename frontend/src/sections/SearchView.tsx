import { useState, useMemo, useEffect, useContext } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search, Sparkles, Brain, Filter, Clock,
  ArrowRight, X, Lightbulb, Quote, CheckCircle, XCircle,
} from 'lucide-react';
import type { Note, SearchResult } from '@/types';
import { formatDistanceToNow } from 'date-fns';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface QueryResult {
  query_id: string;
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

interface QueryResult {
  query_id: string;
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

export function SearchView() {
  const [query, setQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [selectedNote, setSelectedNote] = useState<Note | null>(null);
  const [activeFilters, setActiveFilters] = useState<string[]>([]);
  const [queryResults, setQueryResults] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  
  // STEP 8: Feedback state
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [feedbackComment, setFeedbackComment] = useState('');
  const [showFeedbackComment, setShowFeedbackComment] = useState(false);
  const [answerFeedbackStatus, setAnswerFeedbackStatus] = useState<'verified' | 'rejected' | null>(null);
  
  // STEP 7: Output format state
  const [showStep7Format, setShowStep7Format] = useState(false);
  const [step7Output, setStep7Output] = useState<{ answer: string; confidence: number; sources: Array<{ document_id: string; chunk_index: number; similarity: number }> } | null>(null);
  
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

  // Load notes from workspace for generating suggestions
  useEffect(() => {
    if (!workspaceId) {
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
  }, [workspaceId]);

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
    if (!queryResults?.sources) return [];
    
    return queryResults.sources.map((source) => ({
      note: {
        id: source.chunk_id,
        title: source.document_title,
        content: source.text,
        summary: source.text.substring(0, 150),
        tags: [] as string[],
        connections: [] as string[],
        userId: '',
        workspaceId: '',
        type: 'document' as const,
        createdAt: new Date(),
        updatedAt: new Date(),
      },
      score: source.similarity,
      highlights: [source.text.substring(0, 100) + '...'],
    }));
  }, [queryResults]);

  const filteredResults = useMemo(() => {
    if (!activeFilters.length) return displayResults;
    return displayResults.filter((r) => activeFilters.some((f) => r.note.tags.includes(f)));
  }, [displayResults, activeFilters]);

  const allTags = useMemo(() => {
    const s = new Set<string>();
    notes.forEach((n) => n.tags?.forEach((t) => s.add(t)));
    return Array.from(s).sort();
  }, [notes]);

  const toggleFilter = (tag: string) =>
    setActiveFilters((p) => (p.includes(tag) ? p.filter((t) => t !== tag) : [...p, tag]));

  // Execute semantic search via backend API
  const doSearch = async () => {
    console.log('🔍 [SEARCH START] Executing semantic search - Query: "%s", Workspace: %s', query, workspaceId);
    
    if (!query.trim() || !workspaceId) {
      console.warn('⚠️ [VALIDATION] Query or workspace is missing');
      return;
    }
    
    setIsSearching(true);
    setError(null);
    
    try {
      console.debug('📡 [API CALL] Calling api.query()');
      const result = await api.query(query, workspaceId);
      console.log('✅ [SEARCH SUCCESS] Got %d results - Confidence: %d', result.sources?.length || 0, result.confidence_score);
      
      setQueryResults({
        query_id: result.id || '',
        answer: result.answer,
        confidence: result.confidence_score,
        confidence_factors: {
          similarity_avg: 0,
          document_diversity: 0,
          source_coverage: 0,
        },
        sources: result.sources,
        model_used: result.model_used,
        tokens_used: result.tokens_used,
        response_time_ms: 0,
      });
    } catch (err) {
      let errorMsg = 'Search failed. Please try again.';
      
      if (err instanceof Error) {
        const msg = err.message.toLowerCase();
        if (msg.includes('timeout')) {
          errorMsg = 'Query took too long. Try rephrasing or uploading more specific documents.';
        } else if (msg.includes('429')) {
          errorMsg = 'Too many requests. Please wait a moment and try again.';
        } else if (msg.includes('no documents') || msg.includes('no results')) {
          errorMsg = 'No matching documents found. Try uploading more documents or use different keywords.';
        } else if (msg.includes('network')) {
          errorMsg = 'Network error. Check your connection and try again.';
        } else {
          errorMsg = err.message;
        }
      }
      
      console.error('❌ [SEARCH FAILED] Search error:', errorMsg, err);
      setError(errorMsg);
    } finally {
      setIsSearching(false);
    }
  };

  const suggested = generateSuggestions();

  // STEP 8: Submit feedback on answer quality
  const handleSubmitFeedback = async (status: 'verified' | 'rejected') => {
    console.log('👍 [FEEDBACK START] Submitting feedback - Status: %s, Query ID: %s', status, queryResults?.query_id);
    
    if (!queryResults?.query_id) {
      console.warn('⚠️ [VALIDATION] No query ID found');
      return;
    }
    
    setFeedbackSubmitting(true);
    setFeedbackMessage(null);
    
    try {
      console.debug('📡 [API CALL] Calling api.submitAnswerFeedback()');
      const result = await api.submitAnswerFeedback(
        queryResults.query_id,
        status,
        feedbackComment || undefined
      );
      console.log('✅ [FEEDBACK SUCCESS] Feedback submitted - Chunks updated: %d', result.chunks_updated.length);
      
      setAnswerFeedbackStatus(status);
      const message = status === 'verified'
        ? `✓ Answer marked as correct. ${result.chunks_updated.length} source chunk(s) credibility updated.`
        : `✗ Answer marked as incorrect. ${result.chunks_updated.length} source chunk(s) credibility adjusted. Thank you for improving the model!`;
      
      setFeedbackMessage(message);
      console.debug('📢 [MESSAGE] Feedback message set: %s', message);
      
      // Clear form after 2 seconds
      setTimeout(() => {
        console.debug('🧹 [CLEANUP] Clearing feedback form');
        setFeedbackComment('');
        setShowFeedbackComment(false);
        setFeedbackMessage(null);
      }, 2000);
      
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
      
      console.error('❌ [FEEDBACK FAILED] Feedback error:', errorMsg, err);
      setFeedbackMessage(`Error: ${errorMsg}`);
    } finally {
      setFeedbackSubmitting(false);
    }
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
          onClick={doSearch}
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
              onClick={() => { setQuery(q); doSearch(); }}
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
                {queryResults && ` • Confidence: ${Math.round(queryResults.confidence * 100)}%`}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Brain size={11} color={TT.yolk} />
                <span style={{ fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.yolk }}>AI-Enhanced</span>
              </div>
            </div>

            {filteredResults.length === 0 && !error ? (
              <div style={{ textAlign: 'center', padding: '60px 0' }}>
                <Lightbulb size={36} color={TT.inkMid} style={{ margin: '0 auto 16px' }} />
                <div style={{ fontFamily: TT.fontDisplay, fontSize: 24, letterSpacing: '0.06em', color: TT.snow, marginBottom: 8 }}>NO RESULTS</div>
                <p style={{ fontSize: 10.5, letterSpacing: '0.04em', color: TT.inkMuted, textTransform: 'uppercase' }}>Try different keywords or upload more documents</p>
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
                      onClick={() => setSelectedNote(result.note)}
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
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '1px 7px', background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.15)', borderRadius: 2, flexShrink: 0 }}>
                              <Brain size={9} color={TT.yolk} />
                              <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.yolk }}>{Math.round(result.score * 100)}% match</span>
                            </div>
                          </div>

                          {/* Highlights */}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 10 }}>
                            {result.highlights.slice(0, 2).map((h, i) => (
                              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                                {h.includes('...') && <Quote size={9} color={TT.inkMid} style={{ marginTop: 3, flexShrink: 0 }} />}
                                <span style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, lineHeight: 1.5, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{h}</span>
                              </div>
                            ))}
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
                              Semantic match
                            </span>
                          </div>
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
      {selectedNote && (
        <div
          onClick={() => setSelectedNote(null)}
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
                  <span style={{ color: TT.yolk }}>{selectedNote.title.charAt(0)}</span>{selectedNote.title.slice(1)}
                </h2>
                <button
                  onClick={() => setSelectedNote(null)}
                  style={{ background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2, cursor: 'pointer', padding: '4px 6px', color: TT.inkMuted, transition: 'all 0.15s' }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.error; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)'; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
                >
                  <X size={12} />
                </button>
              </div>

              {/* STEP 7: Answer Summary (if displayed from search results) */}
              {queryResults && (
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

              {/* Tags */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 16 }}>
                {selectedNote.tags.map((tag) => (
                  <span key={tag} style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 8px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}>
                    {tag}
                  </span>
                ))}
              </div>

              {/* Content */}
              <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '14px 16px', marginBottom: 16 }}>
                <p style={{ fontFamily: TT.fontBody, fontSize: 13, lineHeight: 1.7, color: TT.inkSubtle, whiteSpace: 'pre-wrap' }}>
                  {selectedNote.content}
                </p>
              </div>

              {/* Footer */}
              <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: `1px solid ${TT.inkBorder}`, paddingTop: 12 }}>
                <span style={{ fontSize: 9.5, color: TT.inkMid, letterSpacing: '0.04em' }}>
                  Semantic match from RAG index
                </span>
                {queryResults?.confidence && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9.5, color: TT.yolk }}>
                    <Brain size={10} /> {Math.round(queryResults.confidence * 100)}% confidence
                  </span>
                )}
              </div>

              {/* STEP 8: Feedback Section */}
              <div style={{ marginTop: 16, borderTop: `1px solid ${TT.inkBorder}`, paddingTop: 12 }}>
                <div style={{ fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 10 }}>
                  STEP 8: Is this answer correct?
                </div>
                
                {/* Feedback message */}
                {feedbackMessage && (
                  <div style={{
                    background: answerFeedbackStatus === 'verified' ? 'rgba(76,175,80,0.1)' : 'rgba(255,69,69,0.1)',
                    border: `1px solid ${answerFeedbackStatus === 'verified' ? 'rgba(76,175,80,0.3)' : 'rgba(255,69,69,0.3)'}`,
                    borderRadius: 3,
                    padding: '8px 12px',
                    marginBottom: 10,
                    color: answerFeedbackStatus === 'verified' ? '#4CAF50' : TT.error,
                    fontSize: 10,
                    fontFamily: TT.fontMono
                  }}>
                    {feedbackMessage}
                  </div>
                )}
                
                {/* Feedback buttons */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <button
                    onClick={() => handleSubmitFeedback('verified')}
                    disabled={feedbackSubmitting || answerFeedbackStatus === 'verified'}
                    style={{
                      flex: 1,
                      height: 32,
                      background: answerFeedbackStatus === 'verified' ? '#4CAF50' : TT.yolk,
                      border: `1px solid ${answerFeedbackStatus === 'verified' ? '#4CAF50' : TT.yolk}`,
                      borderRadius: 3,
                      color: TT.inkBlack,
                      fontFamily: TT.fontMono,
                      fontSize: 10,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      cursor: feedbackSubmitting || answerFeedbackStatus === 'verified' ? 'not-allowed' : 'pointer',
                      opacity: feedbackSubmitting || answerFeedbackStatus === 'verified' ? 0.7 : 1,
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      if (!feedbackSubmitting && answerFeedbackStatus !== 'verified') {
                        (e.currentTarget as HTMLElement).style.background = TT.yolkBright;
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (answerFeedbackStatus !== 'verified') {
                        (e.currentTarget as HTMLElement).style.background = TT.yolk;
                      }
                    }}
                  >
                    {feedbackSubmitting ? 'Saving...' : answerFeedbackStatus === 'verified' ? '✓ Verified' : 'Verify Correct'}
                  </button>
                  
                  <button
                    onClick={() => handleSubmitFeedback('rejected')}
                    disabled={feedbackSubmitting || answerFeedbackStatus === 'rejected'}
                    style={{
                      flex: 1,
                      height: 32,
                      background: answerFeedbackStatus === 'rejected' ? TT.error : TT.inkRaised,
                      border: `1px solid ${answerFeedbackStatus === 'rejected' ? TT.error : TT.inkBorder}`,
                      borderRadius: 3,
                      color: answerFeedbackStatus === 'rejected' ? TT.snow : TT.inkMuted,
                      fontFamily: TT.fontMono,
                      fontSize: 10,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      cursor: feedbackSubmitting || answerFeedbackStatus === 'rejected' ? 'not-allowed' : 'pointer',
                      opacity: feedbackSubmitting || answerFeedbackStatus === 'rejected' ? 0.7 : 1,
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      if (!feedbackSubmitting && answerFeedbackStatus !== 'rejected') {
                        (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.5)';
                        (e.currentTarget as HTMLElement).style.background = 'rgba(255,69,69,0.08)';
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (answerFeedbackStatus !== 'rejected') {
                        (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                        (e.currentTarget as HTMLElement).style.background = TT.inkRaised;
                      }
                    }}
                  >
                    {feedbackSubmitting ? 'Saving...' : answerFeedbackStatus === 'rejected' ? '✗ Rejected' : 'Reject Incorrect'}
                  </button>
                </div>
                
                {/* Optional comment field */}
                {!answerFeedbackStatus && (
                  <button
                    onClick={() => setShowFeedbackComment(!showFeedbackComment)}
                    style={{
                      width: '100%',
                      height: 28,
                      background: 'transparent',
                      border: `1px dashed ${TT.inkBorder}`,
                      borderRadius: 3,
                      color: TT.inkMuted,
                      fontFamily: TT.fontMono,
                      fontSize: 9,
                      letterSpacing: '0.05em',
                      textTransform: 'uppercase',
                      cursor: 'pointer',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
                      (e.currentTarget as HTMLElement).style.color = TT.yolk;
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                      (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
                    }}
                  >
                    {showFeedbackComment ? '✕ Hide comment' : '+ Add optional comment'}
                  </button>
                )}
                
                {/* Comment textarea */}
                {showFeedbackComment && !answerFeedbackStatus && (
                  <textarea
                    value={feedbackComment}
                    onChange={(e) => setFeedbackComment(e.target.value)}
                    placeholder="Why is this answer correct/incorrect? (optional)"
                    style={{
                      width: '100%',
                      minHeight: 60,
                      marginTop: 8,
                      padding: '8px 12px',
                      background: TT.inkRaised,
                      border: `1px solid ${TT.inkBorder}`,
                      borderRadius: 3,
                      color: TT.snow,
                      fontFamily: TT.fontMono,
                      fontSize: 11,
                      resize: 'none',
                      outline: 'none',
                      boxSizing: 'border-box',
                      fontStyle: !feedbackComment ? 'italic' : 'normal',
                    }}
                    onFocus={(e) => {
                      (e.currentTarget as HTMLTextAreaElement).style.borderColor = TT.yolk;
                      (e.currentTarget as HTMLTextAreaElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)';
                    }}
                    onBlur={(e) => {
                      (e.currentTarget as HTMLTextAreaElement).style.borderColor = TT.inkBorder;
                      (e.currentTarget as HTMLTextAreaElement).style.boxShadow = 'none';
                    }}
                  />
                )}
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}