import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  FileText,
  Share2,
  Sparkles,
  TrendingUp,
  Clock,
  ArrowRight,
  Zap,
  Users,
  Brain,
} from 'lucide-react';
import type { User, Note, AIInsight, Workspace } from '@/types';
import { formatDistanceToNow } from 'date-fns';
import { useState, useEffect, useContext } from 'react';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface DashboardProps {
  user: User | null;
  onCreateNote: () => void;
  onViewGraph: () => void;
  onViewAllNotes: () => void;
  onViewAllInsights: () => void;
}

function cn(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(' ');
}

// ─── Animation Variants ────────────────────────────────────────────────────

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.08 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] as const },
  },
};

// ─── Sub-components ────────────────────────────────────────────────────────

/** Mono uppercase label chip — used for plan badge, stat labels, etc. */
function MonoChip({ children, yellow = false }: { children: React.ReactNode; yellow?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 text-[9.5px] font-medium tracking-[0.1em] uppercase rounded-none border',
        yellow
          ? 'bg-[rgba(245,230,66,0.1)] text-[#F5E642] border-[rgba(245,230,66,0.25)]'
          : 'bg-[#1A1A1A] text-[#5A5A5A] border-[#252525]'
      )}
      style={{ fontFamily: "'IBM Plex Mono', monospace" }}
    >
      {children}
    </span>
  );
}

/** Yellow dot accent used before labels */
function YellowDot() {
  return (
    <span
      className="inline-block w-1 h-1 rounded-full flex-shrink-0"
      style={{ background: '#F5E642', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }}
    />
  );
}

// ─── Dashboard ─────────────────────────────────────────────────────────────

export function Dashboard({
  user,
  onCreateNote,
  onViewGraph,
  onViewAllNotes,
  onViewAllInsights,
}: DashboardProps) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [insights, setInsights] = useState<AIInsight[]>([]);
  const [workspaceStats, setWorkspaceStats] = useState<{
    documents: { total: number; indexed: number; processing: number };
    vectors: number;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;
  const workspaces = authContext?.workspaces || [];

  // Load real data from API
  useEffect(() => {
    if (!workspaceId) return;
    
    const loadData = async () => {
      try {
        setIsLoading(true);
        setError(null);
        // Load notes
        const notesResponse = await api.listNotes({ workspace_id: workspaceId });
        // Transform API response to match frontend Note type (camelCase dates)
        const transformedNotes = (notesResponse.items || []).map((note: any) => {
          const createdAtStr = note.created_at || note.createdAt;
          const updatedAtStr = note.updated_at || note.updatedAt;
          return {
            ...note,
            userId: note.user_id || note.userId,
            workspaceId: note.workspace_id || note.workspaceId,
            createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
            updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
            confidence: note.confidence_score || note.confidence,
          };
        });
        setNotes(transformedNotes);
        
        // Load workspace stats
        const stats = await api.getWorkspaceStats(workspaceId);
        setWorkspaceStats(stats);
        
        // Create derived insights from notes and stats
        const notesCount = (notesResponse.items || []).length;
        const indexedDocs = stats.documents.indexed;
        const derivedInsights: AIInsight[] = [
          {
            id: '1',
            type: 'suggestion',
            content: `Your workspace has ${notesCount} notes and ${indexedDocs} indexed documents in the knowledge base.`,
            sources: [],
            confidence: 0.95,
            createdAt: new Date(),
          },
          {
            id: '2',
            type: 'trend',
            content: `Knowledge base contains ${stats.vectors} vector embeddings for semantic search.`,
            sources: [],
            confidence: 0.87,
            createdAt: new Date(),
          },
          {
            id: '3',
            type: 'summary',
            content: `${stats.documents.processing} documents are currently being processed.`,
            sources: [],
            confidence: 0.92,
            createdAt: new Date(),
          },
        ];
        setInsights(derivedInsights);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Failed to load dashboard data';
        console.error('Failed to load dashboard data:', err);
        setError(errorMessage);
        // Set empty data to prevent cascade failures
        setNotes([]);
        setInsights([]);
        setWorkspaceStats(null);
      } finally {
        setIsLoading(false);
      }
    };
    
    loadData();
  }, [workspaceId]);
  
  const recentNotes = notes.slice(0, 5);
  const totalTags = new Set(notes.flatMap((n) => n.tags)).size;

  const stats = [
    { 
      label: 'Total Documents',      
      value: workspaceStats?.documents.total || 0,      
      icon: FileText, 
      change: workspaceStats ? `+${workspaceStats.documents.indexed}` : '+0' 
    },
    { 
      label: 'Indexed Documents',      
      value: workspaceStats?.documents.indexed || 0,         
      icon: Share2,   
      change: workspaceStats ? `${Math.round((workspaceStats.documents.indexed / (workspaceStats.documents.total || 1)) * 100)}%` : '0%'  
    },
    { 
      label: 'Vector Embeddings',          
      value: workspaceStats?.vectors || 0,   
      icon: Brain, 
      change: workspaceStats ? `~${Math.round((workspaceStats.vectors || 0) / (workspaceStats.documents.indexed || 1))}/doc` : '~0' 
    },
    { 
      label: 'Workspaces',           
      value: workspaces.length, 
      icon: Users,    
      change: '+1'   
    },
  ];

  // Calculate monthly growth from notes
  const calculateMonthlyGrowth = () => {
    const now = new Date();
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const monthlyCounts: number[] = new Array(12).fill(0);
    
    notes.forEach(note => {
      const createdDate = note.createdAt instanceof Date ? note.createdAt : new Date(note.createdAt);
      const monthIndex = createdDate.getMonth();
      monthlyCounts[monthIndex]++;
    });
    
    // Convert to heights (40-100 range)
    const maxCount = Math.max(...monthlyCounts, 1);
    const heights = monthlyCounts.map(count => {
      if (count === 0) return 40; // minimum height
      return 40 + (count / maxCount) * 60; // 40 to 100 range
    });
    
    return { heights, months };
  };

  const { heights: chartHeights, months } = calculateMonthlyGrowth();

  // Show error if data failed to load
  if (error) {
    return (
      <div style={{ padding: 32, background: '#0A0A0A', minHeight: '100vh', fontFamily: "'IBM Plex Sans', sans-serif" }}>
        <div style={{
          background: 'rgba(255, 69, 69, 0.1)',
          border: '1px solid rgba(255, 69, 69, 0.3)',
          borderRadius: '6px',
          padding: '16px',
          color: '#FF4545',
          marginBottom: '24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between'
        }}>
          <div>
            <strong>Error loading dashboard:</strong> {error}
          </div>
          <button 
            onClick={() => {
              setError(null);
              window.location.reload();
            }}
            style={{
              padding: '8px 16px',
              background: '#FF4545',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: '600'
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="space-y-8 p-8"
      style={{ background: '#0A0A0A', minHeight: '100vh', fontFamily: "'IBM Plex Sans', sans-serif" }}
    >

      {/* ── Welcome ─────────────────────────────────────────────── */}
      <motion.div variants={itemVariants}>
        {/* Section label */}
        <div className="flex items-center gap-2 mb-4">
          <YellowDot />
          <span
            className="text-[9.5px] tracking-[0.1em] uppercase"
            style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
          >
            Overview
          </span>
        </div>

        <div className="flex items-end gap-4 flex-wrap">
          <h1
            className="text-[44px] leading-none uppercase"
            style={{ fontFamily: "'Bebas Neue', 'Arial Narrow', sans-serif", color: '#F5F5F5', letterSpacing: '0.04em' }}
          >
            {/* First letter yellow */}
            <span style={{ color: '#F5E642' }}>W</span>elcome,{' '}
            {user?.name ? user.name.split(' ')[0] : 'User'}
          </h1>
          <MonoChip yellow>
            {user?.plan
              ? user.plan.charAt(0).toUpperCase() + user.plan.slice(1)
              : 'Free'}
          </MonoChip>
        </div>

        {/* Yellow underbar */}
        <div style={{ width: 36, height: 3, background: '#F5E642', marginTop: 10 }} />

        <p
          className="mt-4 text-[12px] tracking-[0.04em] uppercase"
          style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
        >
          {isLoading ? 'Loading...' : `Your knowledge base contains ${workspaceStats?.documents.total || 0} ${workspaceStats?.documents.total === 1 ? 'document' : 'documents'}`}
        </p>
      </motion.div>

      {/* ── Quick Actions ───────────────────────────────────────── */}
      <motion.div variants={itemVariants} className="flex flex-wrap gap-3">
        {/* Primary CTA — yellow fill */}
        <button
          onClick={onCreateNote}
          className="flex items-center gap-2 h-[42px] px-5 text-[15px] uppercase tracking-[0.12em] cursor-pointer transition-all duration-150"
          style={{
            fontFamily: "'Bebas Neue', sans-serif",
            background: '#F5E642',
            color: '#0A0A0A',
            border: '2px solid #F5E642',
            borderRadius: 3,
            boxShadow: '0 4px 20px rgba(245,230,66,0.15)',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.background = '#FFF176';
            (e.currentTarget as HTMLElement).style.borderColor = '#FFF176';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.background = '#F5E642';
            (e.currentTarget as HTMLElement).style.borderColor = '#F5E642';
          }}
        >
          <FileText size={14} />
          Create Note
        </button>

        {/* Ghost buttons — bordered */}
        {[
          { label: 'Explore Graph', icon: Share2, onClick: onViewGraph },
          { label: 'Run Workflow',  icon: Zap,    onClick: undefined  },
        ].map(({ label, icon: Icon, onClick }) => (
          <button
            key={label}
            onClick={onClick}
            className="flex items-center gap-2 h-[42px] px-5 text-[15px] uppercase tracking-[0.12em] cursor-pointer transition-all duration-150"
            style={{
              fontFamily: "'Bebas Neue', sans-serif",
              background: 'transparent',
              color: '#5A5A5A',
              border: '1px solid #252525',
              borderRadius: 3,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.color = '#F5E642';
              (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.35)';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.color = '#5A5A5A';
              (e.currentTarget as HTMLElement).style.borderColor = '#252525';
            }}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </motion.div>

      {/* ── Stats Grid ──────────────────────────────────────────── */}
      <motion.div variants={itemVariants}>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {stats.map(({ label, value, icon: Icon, change }) => (
            <div
              key={label}
              className="group transition-all duration-200"
              style={{
                background: '#111111',
                border: '1px solid #252525',
                borderLeft: '3px solid #F5E642',
                borderRadius: 3,
                padding: '20px 20px 16px',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
                (e.currentTarget as HTMLElement).style.background = 'rgba(245,230,66,0.03)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = '#252525';
                (e.currentTarget as HTMLElement).style.background = '#111111';
              }}
            >
              {/* Icon row */}
              <div className="flex items-start justify-between mb-3">
                <div
                  style={{
                    width: 32, height: 32,
                    background: 'rgba(245,230,66,0.08)',
                    border: '1px solid rgba(245,230,66,0.15)',
                    borderRadius: 3,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  <Icon size={14} color="#F5E642" />
                </div>
                <span
                  className="text-[9px] tracking-[0.06em] uppercase"
                  style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  {change}
                </span>
              </div>

              {/* Value */}
              <div
                className="text-[38px] leading-none"
                style={{ fontFamily: "'Bebas Neue', sans-serif", color: '#F5F5F5', letterSpacing: '0.02em' }}
              >
                {value}
              </div>

              {/* Label */}
              <div
                className="mt-1 text-[9.5px] tracking-[0.08em] uppercase"
                style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
              >
                {label}
              </div>
            </div>
          ))}
        </div>
      </motion.div>

      {/* ── Main Content Grid ───────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Recent Notes */}
        <motion.div variants={itemVariants} className="lg:col-span-2">
          <div style={{ background: '#111111', border: '1px solid #252525', borderRadius: 3, height: '100%' }}>
            {/* Header */}
            <div
              className="flex items-center justify-between px-5 py-4"
              style={{ borderBottom: '1px solid #1A1A1A' }}
            >
              <div className="flex items-center gap-2">
                <Clock size={13} color="#F5E642" />
                <span
                  className="text-[11px] tracking-[0.1em] uppercase"
                  style={{ color: '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  Recent Notes
                </span>
              </div>
              <button
                onClick={onViewAllNotes}
                className="flex items-center gap-1 transition-colors duration-150"
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase',
                  color: '#F5E642', background: 'none', border: 'none', cursor: 'pointer',
                }}
              >
                View all <ArrowRight size={10} />
              </button>
            </div>

            {/* Note list */}
            <div className="p-4 space-y-2">
              {recentNotes.map((note, index) => (
                <motion.div
                  key={note.id}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.07 }}
                  className="group cursor-pointer transition-all duration-150"
                  style={{
                    background: '#0A0A0A',
                    border: '1px solid #1A1A1A',
                    borderRadius: 3,
                    padding: '12px 14px',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.25)';
                    (e.currentTarget as HTMLElement).style.borderLeftColor = '#F5E642';
                    (e.currentTarget as HTMLElement).style.borderLeftWidth = '3px';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = '#1A1A1A';
                    (e.currentTarget as HTMLElement).style.borderLeftWidth = '1px';
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <h3
                        className="text-[13px] truncate transition-colors duration-150"
                        style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Mono', monospace", fontWeight: 500 }}
                      >
                        {note.title}
                      </h3>
                      <p
                        className="mt-1 line-clamp-2 text-[11px] leading-[1.5]"
                        style={{ color: '#3A3A3A', fontFamily: "'IBM Plex Sans', sans-serif" }}
                      >
                        {note.summary || note.content.substring(0, 100)}...
                      </p>
                      <div className="flex flex-wrap items-center gap-2 mt-2">
                        {note.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="text-[9px] px-1.5 py-px tracking-[0.05em] uppercase"
                            style={{
                              background: '#1A1A1A',
                              color: '#5A5A5A',
                              border: '1px solid #252525',
                              borderRadius: 2,
                              fontFamily: "'IBM Plex Mono', monospace",
                            }}
                          >
                            {tag}
                          </span>
                        ))}
                        <span
                          className="text-[9.5px]"
                          style={{ color: '#3A3A3A', fontFamily: "'IBM Plex Mono', monospace" }}
                        >
                        {formatDistanceToNow(note.updatedAt instanceof Date ? note.updatedAt : new Date(note.updatedAt), { addSuffix: true })}
                        </span>
                      </div>
                    </div>

                    {note.confidence && (
                      <div
                        className="flex items-center gap-1 flex-shrink-0 px-2 py-1"
                        style={{
                          background: 'rgba(245,230,66,0.06)',
                          border: '1px solid rgba(245,230,66,0.12)',
                          borderRadius: 2,
                        }}
                      >
                        <Brain size={10} color="#F5E642" />
                        <span
                          className="text-[9.5px]"
                          style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                        >
                          {Math.round(note.confidence * 100)}%
                        </span>
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </motion.div>

        {/* AI Insights */}
        <motion.div variants={itemVariants}>
          <div style={{ background: '#111111', border: '1px solid #252525', borderRadius: 3, height: '100%' }}>
            <div
              className="flex items-center justify-between px-5 py-4"
              style={{ borderBottom: '1px solid #1A1A1A' }}
            >
              <div className="flex items-center gap-2">
                <Sparkles size={13} color="#F5E642" />
                <span
                  className="text-[11px] tracking-[0.1em] uppercase"
                  style={{ color: '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  AI Insights
                </span>
              </div>
              <MonoChip yellow>{insights.length} new</MonoChip>
            </div>

            <div className="p-4 space-y-2">
              {insights.slice(0, 3).map((insight) => (
                <div
                  key={insight.id}
                  className="transition-all duration-150 cursor-default"
                  style={{
                    background: '#0A0A0A',
                    border: '1px solid #1A1A1A',
                    borderRadius: 3,
                    padding: '12px 14px',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.2)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = '#1A1A1A';
                  }}
                >
                  <p
                    className="text-[12px] leading-[1.6] line-clamp-3"
                    style={{ color: '#888888', fontFamily: "'IBM Plex Sans', sans-serif" }}
                  >
                    {insight.content}
                  </p>
                  <div className="flex items-center justify-between mt-3">
                    <span
                      className="text-[9px] tracking-[0.07em] uppercase"
                      style={{ color: '#3A3A3A', fontFamily: "'IBM Plex Mono', monospace" }}
                    >
                      {insight.type}
                    </span>
                    <span
                      className="text-[9px]"
                      style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace", opacity: 0.7 }}
                    >
                      {Math.round(insight.confidence * 100)}% conf
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <div className="px-4 pb-4">
              <button
                onClick={onViewAllInsights}
                className="w-full h-9 text-[13px] uppercase tracking-[0.1em] transition-all duration-150"
                style={{
                  fontFamily: "'Bebas Neue', sans-serif",
                  background: 'transparent',
                  border: '1px solid #252525',
                  borderRadius: 3,
                  color: '#5A5A5A',
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
                  (e.currentTarget as HTMLElement).style.color = '#F5E642';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = '#252525';
                  (e.currentTarget as HTMLElement).style.color = '#5A5A5A';
                }}
              >
                View all insights
              </button>
            </div>
          </div>
        </motion.div>
      </div>

      {/* ── Knowledge Growth Chart ──────────────────────────────── */}
      <motion.div variants={itemVariants}>
        <div style={{ background: '#111111', border: '1px solid #252525', borderRadius: 3 }}>
          <div
            className="flex items-center gap-2 px-5 py-4"
            style={{ borderBottom: '1px solid #1A1A1A' }}
          >
            <TrendingUp size={13} color="#F5E642" />
            <span
              className="text-[11px] tracking-[0.1em] uppercase"
              style={{ color: '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
            >
              Knowledge Growth
            </span>
          </div>

          <div className="px-5 pt-5 pb-4">
            {/* Chart bars */}
            <div className="flex items-end gap-1.5 h-28">
              {chartHeights.map((height, i) => (
                <div
                  key={i}
                  className="flex-1 cursor-pointer transition-all duration-200 group"
                  style={{
                    height: `${height}%`,
                    background: height === 100
                      ? '#F5E642'                    /* peak bar = full yellow */
                      : 'rgba(245,230,66,0.18)',
                    borderRadius: '2px 2px 0 0',
                    border: '1px solid transparent',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.background = '#F5E642';
                    (e.currentTarget as HTMLElement).style.boxShadow = '0 0 12px rgba(245,230,66,0.3)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.background =
                      height === 100 ? '#F5E642' : 'rgba(245,230,66,0.18)';
                    (e.currentTarget as HTMLElement).style.boxShadow = 'none';
                  }}
                />
              ))}
            </div>

            {/* Month labels */}
            <div className="flex justify-between mt-3">
              {months.map((m) => (
                <span
                  key={m}
                  className="flex-1 text-center text-[8.5px] tracking-[0.04em] uppercase"
                  style={{ color: '#3A3A3A', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  {m}
                </span>
              ))}
            </div>
          </div>
        </div>
      </motion.div>

    </motion.div>
  );
}
