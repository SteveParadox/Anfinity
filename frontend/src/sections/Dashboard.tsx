import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowRight,
  Brain,
  CheckCircle2,
  Clock,
  Copy,
  FileText,
  RefreshCw,
  Search,
  Share2,
  Sparkles,
  TrendingUp,
  Users,
  Workflow,
  X,
  Zap,
} from 'lucide-react';
import { format, formatDistanceToNow } from 'date-fns';
import { startTransition, useContext, useDeferredValue, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import type { AIInsight, Note, User } from '@/types';

interface DashboardProps {
  user: User | null;
  onCreateNote: () => void;
  onViewGraph: () => void;
  onViewAllNotes: () => void;
  onViewAllInsights: () => void;
  onOpenWorkflows?: () => void;
}

type MetricFocus = 'documents' | 'indexed' | 'vectors' | 'workspaces';
type InsightFilter = 'all' | AIInsight['type'];
type SortMode = 'recent' | 'alphabetical' | 'confidence';
type RangeMonths = 3 | 6 | 12;
type RequestSource = 'initial' | 'manual' | 'retry';

type DashboardFeedback =
  | {
      tone: 'info' | 'success' | 'error';
      message: string;
    }
  | null;

function cn(...classes: Array<string | undefined | false | null>) {
  return classes.filter(Boolean).join(' ');
}

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.06 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] as const },
  },
};

const RANGE_OPTIONS: Array<{ label: string; value: RangeMonths }> = [
  { label: '3M', value: 3 },
  { label: '6M', value: 6 },
  { label: '12M', value: 12 },
];

const SORT_OPTIONS: Array<{ label: string; value: SortMode }> = [
  { label: 'Recent', value: 'recent' },
  { label: 'A-Z', value: 'alphabetical' },
  { label: 'Confidence', value: 'confidence' },
];

function MonoChip({ children, yellow = false }: { children: React.ReactNode; yellow?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 text-[9.5px] font-medium tracking-[0.1em] uppercase border',
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

function YellowDot() {
  return (
    <span
      className="inline-block h-1 w-1 flex-shrink-0 rounded-full"
      style={{ background: '#F5E642', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }}
    />
  );
}

function PillButton({
  active,
  children,
  onClick,
  ariaLabel,
}: {
  active?: boolean;
  children: React.ReactNode;
  onClick: () => void;
  ariaLabel?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      aria-pressed={active}
      className="transition-all duration-150 focus-visible:outline-none"
      style={{
        height: 30,
        padding: '0 10px',
        borderRadius: 3,
        border: `1px solid ${active ? 'rgba(245,230,66,0.35)' : '#252525'}`,
        background: active ? 'rgba(245,230,66,0.08)' : '#0A0A0A',
        color: active ? '#F5E642' : '#5A5A5A',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 10,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        boxShadow: active ? '0 0 0 1px rgba(245,230,66,0.08)' : 'none',
      }}
      onFocus={(event) => {
        event.currentTarget.style.boxShadow = active
          ? '0 0 0 2px rgba(245,230,66,0.2)'
          : '0 0 0 2px rgba(245,245,245,0.12)';
      }}
      onBlur={(event) => {
        event.currentTarget.style.boxShadow = active ? '0 0 0 1px rgba(245,230,66,0.08)' : 'none';
      }}
    >
      {children}
    </button>
  );
}

function SkeletonBlock({
  width = '100%',
  height = 16,
  style,
}: {
  width?: number | string;
  height?: number | string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className="animate-pulse"
      style={{
        width,
        height,
        borderRadius: 3,
        background: 'linear-gradient(90deg, rgba(26,26,26,0.9) 0%, rgba(42,42,42,0.9) 50%, rgba(26,26,26,0.9) 100%)',
        ...style,
      }}
    />
  );
}

function EmptyPanel({
  title,
  description,
  actionLabel,
  onAction,
}: {
  title: string;
  description: string;
  actionLabel: string;
  onAction: () => void;
}) {
  return (
    <div
      style={{
        border: '1px dashed #252525',
        borderRadius: 3,
        padding: '20px 18px',
        background: '#0A0A0A',
      }}
    >
      <p
        className="text-[11px] uppercase tracking-[0.1em]"
        style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Mono', monospace" }}
      >
        {title}
      </p>
      <p
        className="mt-2 text-[12px] leading-[1.6]"
        style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Sans', sans-serif" }}
      >
        {description}
      </p>
      <button
        type="button"
        onClick={onAction}
        className="mt-4 inline-flex items-center gap-2 transition-colors duration-150 focus-visible:outline-none"
        style={{
          color: '#F5E642',
          background: 'transparent',
          border: 'none',
          padding: 0,
          fontFamily: "'IBM Plex Mono', monospace",
          fontSize: 10,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          cursor: 'pointer',
        }}
      >
        {actionLabel}
        <ArrowRight size={12} />
      </button>
    </div>
  );
}

function getMonthKey(value: Date | string | undefined) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  return `${date.getFullYear()}-${date.getMonth()}`;
}

function buildInsights(
  notes: Note[],
  stats: { documents: { total: number; indexed: number; processing: number }; vectors: number }
): AIInsight[] {
  const tagCounts = new Map<string, number>();
  let connectedNotes = 0;
  let staleNotes = 0;
  const now = Date.now();

  for (const note of notes) {
    for (const tag of note.tags ?? []) {
      tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1);
    }
    if ((note.connections?.length ?? 0) > 0) connectedNotes += 1;
    const updatedAt = note.updatedAt instanceof Date ? note.updatedAt.getTime() : new Date(note.updatedAt).getTime();
    if (now - updatedAt > 1000 * 60 * 60 * 24 * 14) staleNotes += 1;
  }

  const topTag = Array.from(tagCounts.entries()).sort((left, right) => right[1] - left[1])[0];
  const tagCount = tagCounts.size;

  return [
    {
      id: 'workspace-summary',
      type: 'summary',
      content: `${notes.length} notes are connected to ${stats.documents.indexed} indexed documents across ${tagCount} active tag groups.`,
      sources: [],
      confidence: 0.96,
      createdAt: new Date(),
    },
    {
      id: 'workspace-trend',
      type: 'trend',
      content: topTag
        ? `#${topTag[0]} is your most active tag with ${topTag[1]} notes, making it the fastest path for drill-down exploration.`
        : `${stats.vectors} vector embeddings are ready, but adding tags will make filtering and clustering more useful.`,
      sources: [],
      confidence: 0.9,
      createdAt: new Date(),
    },
    {
      id: 'workspace-connection',
      type: 'connection',
      content:
        connectedNotes > 0
          ? `${connectedNotes} notes already reference related ideas. Use the graph or note detail panel to continue clustering work without leaving the dashboard.`
          : 'Your graph is still sparse. Connecting a few related notes will unlock more useful exploration patterns.',
      sources: [],
      confidence: 0.84,
      createdAt: new Date(),
    },
    {
      id: 'workspace-suggestion',
      type: 'suggestion',
      content:
        stats.documents.processing > 0
          ? `${stats.documents.processing} documents are still processing. Refresh this dashboard shortly to see expanded search and insight coverage.`
          : staleNotes > 0
            ? `${staleNotes} notes have not been revisited in the last two weeks. Filter by month or tag below to bring them back into focus.`
            : 'Everything looks current. Use the chart and inline actions below to inspect recent activity and move faster inside the workspace.',
      sources: [],
      confidence: 0.88,
      createdAt: new Date(),
    },
  ];
}

function getNotePreview(note: Note) {
  const summary = note.summary?.trim();
  const content = note.content?.trim();
  if (summary) return summary;
  if (!content) return 'No summary available yet.';
  return content.length > 140 ? `${content.slice(0, 140)}...` : content;
}

export function Dashboard({
  user,
  onCreateNote,
  onViewGraph,
  onViewAllNotes,
  onViewAllInsights,
  onOpenWorkflows,
}: DashboardProps) {
  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;
  const workspaces = authContext?.workspaces ?? [];

  const [notes, setNotes] = useState<Note[]>([]);
  const [insights, setInsights] = useState<AIInsight[]>([]);
  const [workspaceStats, setWorkspaceStats] = useState<{
    documents: { total: number; indexed: number; processing: number };
    vectors: number;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [requestState, setRequestState] = useState<{ seed: number; source: RequestSource }>({
    seed: 0,
    source: 'initial',
  });
  const [selectedMetric, setSelectedMetric] = useState<MetricFocus>('documents');
  const [selectedInsightFilter, setSelectedInsightFilter] = useState<InsightFilter>('all');
  const [expandedInsightId, setExpandedInsightId] = useState<string | null>(null);
  const [selectedRangeMonths, setSelectedRangeMonths] = useState<RangeMonths>(12);
  const [selectedChartKey, setSelectedChartKey] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>('recent');
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [noteSheetOpen, setNoteSheetOpen] = useState(false);
  const [feedback, setFeedback] = useState<DashboardFeedback>(null);

  const deferredSearchQuery = useDeferredValue(searchQuery.trim().toLowerCase());
  const hasDashboardData = notes.length > 0 || insights.length > 0 || workspaceStats !== null;
  const showInitialLoading = isLoading && !hasDashboardData;
  const isRefreshing = isLoading && hasDashboardData;

  useEffect(() => {
    if (!feedback) return;

    const timeoutId = window.setTimeout(() => {
      setFeedback(null);
    }, 2600);

    return () => window.clearTimeout(timeoutId);
  }, [feedback]);

  useEffect(() => {
    if (!workspaceId) {
      setNotes([]);
      setInsights([]);
      setWorkspaceStats(null);
      setError(null);
      setLastUpdatedAt(null);
      return;
    }

    let cancelled = false;

    const loadData = async () => {
      setIsLoading(true);
      setError(null);

      try {
        const [notesResponse, stats] = await Promise.all([
          api.listNotes({ workspace_id: workspaceId, page_size: 100 }),
          api.getWorkspaceStats(workspaceId),
        ]);

        if (cancelled) return;

        const nextNotes = notesResponse.items ?? [];
        setNotes(nextNotes);
        setWorkspaceStats(stats);
        setInsights(buildInsights(nextNotes, stats));
        setLastUpdatedAt(new Date());

        if (requestState.source !== 'initial') {
          setFeedback({
            tone: 'success',
            message: requestState.source === 'retry' ? 'Retry succeeded. Dashboard is current.' : 'Dashboard refreshed.',
          });
        }
      } catch (err) {
        if (cancelled) return;

        const message = err instanceof Error ? err.message : 'Failed to load dashboard data.';
        setError(message);
        setFeedback({ tone: 'error', message });
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    loadData();

    return () => {
      cancelled = true;
    };
  }, [requestState.seed, requestState.source, workspaceId]);

  useEffect(() => {
    if (!selectedNoteId) return;
    if (!notes.some((note) => note.id === selectedNoteId)) {
      setSelectedNoteId(null);
      setNoteSheetOpen(false);
    }
  }, [notes, selectedNoteId]);

  const tagCounts = new Map<string, number>();
  for (const note of notes) {
    for (const tag of note.tags ?? []) {
      tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1);
    }
  }

  const topTags = Array.from(tagCounts.entries())
    .sort((left, right) => right[1] - left[1])
    .slice(0, 5)
    .map(([tag, count]) => ({ tag, count }));

  const recentNotes = [...notes]
    .sort((left, right) => {
      const leftTime = left.updatedAt instanceof Date ? left.updatedAt.getTime() : new Date(left.updatedAt).getTime();
      const rightTime = right.updatedAt instanceof Date ? right.updatedAt.getTime() : new Date(right.updatedAt).getTime();
      return rightTime - leftTime;
    });

  const totalTags = tagCounts.size;

  const chartBuckets: Array<{
    key: string;
    label: string;
    description: string;
    count: number;
    height: number;
    isCurrent: boolean;
  }> = [];

  const now = new Date();
  for (let offset = selectedRangeMonths - 1; offset >= 0; offset -= 1) {
    const bucketDate = new Date(now.getFullYear(), now.getMonth() - offset, 1);
    const key = getMonthKey(bucketDate);
    const label = format(bucketDate, 'MMM');
    const description = format(bucketDate, 'MMMM yyyy');
    const count = notes.filter((note) => getMonthKey(note.updatedAt) === key).length;

    chartBuckets.push({
      key,
      label,
      description,
      count,
      height: 0,
      isCurrent: offset === 0,
    });
  }

  const peakBucketCount = Math.max(...chartBuckets.map((bucket) => bucket.count), 1);
  for (const bucket of chartBuckets) {
    bucket.height = bucket.count === 0 ? 18 : 18 + (bucket.count / peakBucketCount) * 82;
  }

  const selectedBucket =
    chartBuckets.find((bucket) => bucket.key === selectedChartKey) ?? chartBuckets[chartBuckets.length - 1] ?? null;

  useEffect(() => {
    if (!selectedChartKey) return;

    const validKeys = new Set<string>();
    const comparisonDate = new Date();

    for (let offset = selectedRangeMonths - 1; offset >= 0; offset -= 1) {
      validKeys.add(getMonthKey(new Date(comparisonDate.getFullYear(), comparisonDate.getMonth() - offset, 1)));
    }

    if (!validKeys.has(selectedChartKey)) {
      setSelectedChartKey(null);
    }
  }, [selectedChartKey, selectedRangeMonths]);

  const filteredNotes = recentNotes
    .filter((note) => {
      if (selectedTag && !(note.tags ?? []).includes(selectedTag)) return false;
      if (selectedChartKey && getMonthKey(note.updatedAt) !== selectedChartKey) return false;
      if (!deferredSearchQuery) return true;

      const haystack = [note.title, note.summary, note.content, (note.tags ?? []).join(' ')]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      return haystack.includes(deferredSearchQuery);
    })
    .sort((left, right) => {
      if (sortMode === 'alphabetical') {
        return left.title.localeCompare(right.title);
      }

      if (sortMode === 'confidence') {
        return (right.confidence ?? 0) - (left.confidence ?? 0);
      }

      const leftTime = left.updatedAt instanceof Date ? left.updatedAt.getTime() : new Date(left.updatedAt).getTime();
      const rightTime = right.updatedAt instanceof Date ? right.updatedAt.getTime() : new Date(right.updatedAt).getTime();
      return rightTime - leftTime;
    });

  const visibleNotes = filteredNotes.slice(0, 5);
  const selectedNote = notes.find((note) => note.id === selectedNoteId) ?? null;
  const filteredInsights =
    selectedInsightFilter === 'all'
      ? insights
      : insights.filter((insight) => insight.type === selectedInsightFilter);

  const activeFilterCount = [Boolean(selectedTag), Boolean(selectedChartKey), Boolean(deferredSearchQuery)].filter(Boolean)
    .length;

  const stats = [
    {
      id: 'documents' as const,
      label: 'Total Documents',
      value: workspaceStats?.documents.total ?? 0,
      icon: FileText,
      change: workspaceStats ? `${workspaceStats.documents.processing} in flight` : '0 in flight',
      description: 'Track ingestion volume and jump back into note capture quickly.',
      primaryActionLabel: 'Create Note',
      primaryAction: onCreateNote,
      secondaryActionLabel: 'Refresh',
      secondaryAction: () => {
        setFeedback({ tone: 'info', message: 'Refreshing dashboard data...' });
        startTransition(() => {
          setRequestState((current) => ({ seed: current.seed + 1, source: 'manual' }));
        });
      },
    },
    {
      id: 'indexed' as const,
      label: 'Indexed Documents',
      value: workspaceStats?.documents.indexed ?? 0,
      icon: Share2,
      change: workspaceStats
        ? `${Math.round((workspaceStats.documents.indexed / (workspaceStats.documents.total || 1)) * 100)}% ready`
        : '0% ready',
      description: 'These documents are ready for retrieval, search, and insight generation.',
      primaryActionLabel: 'Search Workspace',
      primaryAction: onViewAllInsights,
      secondaryActionLabel: 'View Notes',
      secondaryAction: onViewAllNotes,
    },
    {
      id: 'vectors' as const,
      label: 'Vector Embeddings',
      value: workspaceStats?.vectors ?? 0,
      icon: Brain,
      change: workspaceStats ? `~${Math.round((workspaceStats.vectors || 0) / (workspaceStats.documents.indexed || 1))}/doc` : '~0/doc',
      description: 'Vector coverage controls how richly the graph and semantic search can connect ideas.',
      primaryActionLabel: 'Explore Graph',
      primaryAction: onViewGraph,
      secondaryActionLabel: 'Search',
      secondaryAction: onViewAllInsights,
    },
    {
      id: 'workspaces' as const,
      label: 'Workspaces',
      value: workspaces.length,
      icon: Users,
      change: workspaces.length > 1 ? `${workspaces.length - 1} shared` : 'solo',
      description: 'Jump into cross-team review flows or switch context without losing the dashboard thread.',
      primaryActionLabel: onOpenWorkflows ? 'Review Queue' : 'View Notes',
      primaryAction: onOpenWorkflows ?? onViewAllNotes,
      secondaryActionLabel: 'View Notes',
      secondaryAction: onViewAllNotes,
    },
  ];

  const activeStat = stats.find((stat) => stat.id === selectedMetric) ?? stats[0];

  const handleRefresh = (source: RequestSource = 'manual') => {
    setFeedback({ tone: 'info', message: source === 'retry' ? 'Retrying dashboard load...' : 'Refreshing dashboard data...' });
    startTransition(() => {
      setRequestState((current) => ({ seed: current.seed + 1, source }));
    });
  };

  const clearFilters = () => {
    startTransition(() => {
      setSearchQuery('');
      setSelectedTag(null);
      setSelectedChartKey(null);
      setSortMode('recent');
      setSelectedInsightFilter('all');
      setExpandedInsightId(null);
    });
    setFeedback({ tone: 'info', message: 'Dashboard filters cleared.' });
  };

  const handleCopyNote = async (note: Note) => {
    try {
      await navigator.clipboard.writeText(note.title);
      setFeedback({ tone: 'success', message: `Copied "${note.title}" to the clipboard.` });
    } catch {
      setFeedback({ tone: 'error', message: 'Could not copy that note title.' });
    }
  };

  const openNoteDetails = (note: Note) => {
    setSelectedNoteId(note.id);
    setNoteSheetOpen(true);
  };

  const handleNoteCardKeyDown = (event: React.KeyboardEvent<HTMLDivElement>, note: Note) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openNoteDetails(note);
    }
  };

  const insightActionLabel =
    selectedInsightFilter === 'connection'
      ? 'Explore Graph'
      : selectedInsightFilter === 'suggestion'
        ? 'Review Queue'
        : 'Search Workspace';

  const insightAction =
    selectedInsightFilter === 'connection'
      ? onViewGraph
      : selectedInsightFilter === 'suggestion'
        ? onOpenWorkflows ?? onViewAllNotes
        : onViewAllInsights;

  if (!workspaceId) {
    return (
      <div
        className="space-y-6 p-8"
        style={{ background: '#0A0A0A', minHeight: '100vh', fontFamily: "'IBM Plex Sans', sans-serif" }}
      >
        <div className="flex items-center gap-2">
          <YellowDot />
          <span
            className="text-[9.5px] uppercase tracking-[0.1em]"
            style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
          >
            Overview
          </span>
        </div>
        <div
          style={{
            background: '#111111',
            border: '1px solid #252525',
            borderRadius: 3,
            padding: 24,
          }}
        >
          <h1
            className="text-[44px] uppercase leading-none"
            style={{ fontFamily: "'Bebas Neue', 'Arial Narrow', sans-serif", color: '#F5F5F5', letterSpacing: '0.04em' }}
          >
            <span style={{ color: '#F5E642' }}>S</span>elect a workspace
          </h1>
          <p
            className="mt-4 max-w-xl text-[13px] leading-[1.7]"
            style={{ color: '#888888', fontFamily: "'IBM Plex Sans', sans-serif" }}
          >
            The dashboard needs a workspace context before it can show activity, insights, and inline actions.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
        className="space-y-6 p-8"
        style={{ background: '#0A0A0A', minHeight: '100vh', fontFamily: "'IBM Plex Sans', sans-serif" }}
      >
        <motion.div variants={itemVariants} className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <div className="mb-4 flex items-center gap-2">
              <YellowDot />
              <span
                className="text-[9.5px] uppercase tracking-[0.1em]"
                style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
              >
                Overview
              </span>
            </div>
            <div className="flex flex-wrap items-end gap-4">
              <h1
                className="text-[44px] uppercase leading-none"
                style={{ fontFamily: "'Bebas Neue', 'Arial Narrow', sans-serif", color: '#F5F5F5', letterSpacing: '0.04em' }}
              >
                <span style={{ color: '#F5E642' }}>W</span>elcome, {user?.name ? user.name.split(' ')[0] : 'User'}
              </h1>
              <MonoChip yellow>{user?.plan ? user.plan.charAt(0).toUpperCase() + user.plan.slice(1) : 'Free'}</MonoChip>
            </div>
            <div style={{ width: 36, height: 3, background: '#F5E642', marginTop: 10 }} />
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {showInitialLoading ? (
                <SkeletonBlock width={280} height={16} />
              ) : (
                <p
                  className="text-[12px] uppercase tracking-[0.04em]"
                  style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  {workspaceStats?.documents.total ?? 0} documents, {notes.length} notes, {totalTags} active tags
                </p>
              )}
              {lastUpdatedAt ? <MonoChip>Updated {formatDistanceToNow(lastUpdatedAt, { addSuffix: true })}</MonoChip> : null}
              {isRefreshing ? <MonoChip yellow>Refreshing</MonoChip> : null}
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => handleRefresh()}
              disabled={isLoading}
              className="flex h-[42px] items-center gap-2 px-4 uppercase tracking-[0.12em] transition-all duration-150 focus-visible:outline-none disabled:cursor-wait disabled:opacity-70"
              style={{
                fontFamily: "'Bebas Neue', sans-serif",
                background: 'transparent',
                color: '#F5E642',
                border: '1px solid rgba(245,230,66,0.28)',
                borderRadius: 3,
              }}
            >
              <RefreshCw size={14} className={cn(isLoading && 'animate-spin')} />
              {isLoading ? 'Refreshing' : 'Refresh'}
            </button>
            {activeFilterCount > 0 ? (
              <button
                type="button"
                onClick={clearFilters}
                className="flex h-[42px] items-center gap-2 px-4 uppercase tracking-[0.12em] transition-all duration-150 focus-visible:outline-none"
                style={{
                  fontFamily: "'Bebas Neue', sans-serif",
                  background: 'transparent',
                  color: '#5A5A5A',
                  border: '1px solid #252525',
                  borderRadius: 3,
                }}
              >
                <X size={14} />
                Reset Filters
              </button>
            ) : null}
          </div>
        </motion.div>

        <motion.div variants={itemVariants} className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={onCreateNote}
            className="flex h-[42px] items-center gap-2 px-5 text-[15px] uppercase tracking-[0.12em] transition-all duration-150 focus-visible:outline-none"
            style={{
              fontFamily: "'Bebas Neue', sans-serif",
              background: '#F5E642',
              color: '#0A0A0A',
              border: '2px solid #F5E642',
              borderRadius: 3,
              boxShadow: '0 4px 20px rgba(245,230,66,0.15)',
            }}
          >
            <FileText size={14} />
            Create Note
          </button>

          {[
            { label: 'Explore Graph', icon: Share2, onClick: onViewGraph },
            { label: 'Search Workspace', icon: Search, onClick: onViewAllInsights },
            { label: onOpenWorkflows ? 'Review Queue' : 'View Notes', icon: onOpenWorkflows ? Workflow : Zap, onClick: onOpenWorkflows ?? onViewAllNotes },
          ].map(({ label, icon: Icon, onClick }) => (
            <button
              key={label}
              type="button"
              onClick={onClick}
              className="flex h-[42px] items-center gap-2 px-5 text-[15px] uppercase tracking-[0.12em] transition-all duration-150 focus-visible:outline-none"
              style={{
                fontFamily: "'Bebas Neue', sans-serif",
                background: 'transparent',
                color: '#5A5A5A',
                border: '1px solid #252525',
                borderRadius: 3,
              }}
            >
              <Icon size={13} />
              {label}
            </button>
          ))}
        </motion.div>

        <AnimatePresence>
          {(feedback || error) && (
            <motion.div
              key={`${feedback?.message ?? ''}-${error ?? ''}`}
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              variants={itemVariants}
              role="status"
              aria-live="polite"
              style={{
                borderRadius: 3,
                border:
                  feedback?.tone === 'error' || error
                    ? '1px solid rgba(255,69,69,0.3)'
                    : feedback?.tone === 'success'
                      ? '1px solid rgba(245,230,66,0.25)'
                      : '1px solid #252525',
                background:
                  feedback?.tone === 'error' || error
                    ? 'rgba(255,69,69,0.08)'
                    : feedback?.tone === 'success'
                      ? 'rgba(245,230,66,0.08)'
                      : '#111111',
                color: feedback?.tone === 'error' || error ? '#FF8A8A' : '#F5F5F5',
                padding: '12px 16px',
              }}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span
                  className="text-[11px] uppercase tracking-[0.08em]"
                  style={{ fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  {feedback?.message ?? error}
                </span>
                {error ? (
                  <button
                    type="button"
                    onClick={() => handleRefresh('retry')}
                    className="inline-flex items-center gap-2 focus-visible:outline-none"
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: '#F5E642',
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 10,
                      letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                      cursor: 'pointer',
                    }}
                  >
                    Retry
                    <ArrowRight size={12} />
                  </button>
                ) : null}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <motion.div variants={itemVariants} className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {showInitialLoading
              ? Array.from({ length: 4 }).map((_, index) => (
                  <div
                    key={`stat-skeleton-${index}`}
                    style={{
                      background: '#111111',
                      border: '1px solid #252525',
                      borderLeft: '3px solid #F5E642',
                      borderRadius: 3,
                      padding: '20px 20px 16px',
                    }}
                  >
                    <SkeletonBlock width={32} height={32} />
                    <SkeletonBlock width="55%" height={34} style={{ marginTop: 16 }} />
                    <SkeletonBlock width="72%" height={12} style={{ marginTop: 10 }} />
                  </div>
                ))
              : stats.map(({ id, label, value, icon: Icon, change }) => {
                  const active = selectedMetric === id;

                  return (
                    <button
                      key={label}
                      type="button"
                      onClick={() => setSelectedMetric(id)}
                      aria-pressed={active}
                      className="text-left transition-all duration-150 focus-visible:outline-none"
                      style={{
                        background: active ? 'rgba(245,230,66,0.04)' : '#111111',
                        border: `1px solid ${active ? 'rgba(245,230,66,0.32)' : '#252525'}`,
                        borderLeft: '3px solid #F5E642',
                        borderRadius: 3,
                        padding: '20px 20px 16px',
                        boxShadow: active ? '0 0 0 1px rgba(245,230,66,0.08)' : 'none',
                      }}
                    >
                      <div className="mb-3 flex items-start justify-between">
                        <div
                          style={{
                            width: 32,
                            height: 32,
                            background: 'rgba(245,230,66,0.08)',
                            border: '1px solid rgba(245,230,66,0.15)',
                            borderRadius: 3,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                          }}
                        >
                          <Icon size={14} color="#F5E642" />
                        </div>
                        <span
                          className="text-[9px] uppercase tracking-[0.06em]"
                          style={{ color: active ? '#F5E642' : '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
                        >
                          {change}
                        </span>
                      </div>

                      <div
                        className="text-[38px] leading-none"
                        style={{ fontFamily: "'Bebas Neue', sans-serif", color: '#F5F5F5', letterSpacing: '0.02em' }}
                      >
                        {value}
                      </div>
                      <div
                        className="mt-1 text-[9.5px] uppercase tracking-[0.08em]"
                        style={{ color: active ? '#F5E642' : '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                      >
                        {label}
                      </div>
                    </button>
                  );
                })}
          </div>

          {!showInitialLoading && activeStat ? (
            <div
              className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between"
              style={{
                background: '#111111',
                border: '1px solid #252525',
                borderRadius: 3,
                padding: '14px 16px',
              }}
            >
              <div>
                <p
                  className="text-[10px] uppercase tracking-[0.08em]"
                  style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  Active Focus
                </p>
                <p
                  className="mt-1 text-[13px] leading-[1.6]"
                  style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Sans', sans-serif" }}
                >
                  {activeStat.description}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <PillButton onClick={activeStat.primaryAction}>{activeStat.primaryActionLabel}</PillButton>
                <PillButton onClick={activeStat.secondaryAction}>{activeStat.secondaryActionLabel}</PillButton>
              </div>
            </div>
          ) : null}
        </motion.div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <motion.div variants={itemVariants} className="lg:col-span-2">
            <div style={{ background: '#111111', border: '1px solid #252525', borderRadius: 3, height: '100%' }}>
              <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4" style={{ borderBottom: '1px solid #1A1A1A' }}>
                <div className="flex items-center gap-2">
                  <Clock size={13} color="#F5E642" />
                  <span
                    className="text-[11px] uppercase tracking-[0.1em]"
                    style={{ color: '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
                  >
                    Recent Notes
                  </span>
                  <MonoChip>{filteredNotes.length} match</MonoChip>
                </div>
                <button
                  type="button"
                  onClick={onViewAllNotes}
                  className="flex items-center gap-1 transition-colors duration-150 focus-visible:outline-none"
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: 10,
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    color: '#F5E642',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                >
                  View all
                  <ArrowRight size={10} />
                </button>
              </div>

              <div className="space-y-4 p-4">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                  <div
                    className="flex items-center gap-2"
                    style={{
                      background: '#0A0A0A',
                      border: '1px solid #1A1A1A',
                      borderRadius: 3,
                      padding: '0 10px',
                      minHeight: 38,
                    }}
                  >
                    <Search size={13} color="#5A5A5A" />
                    <input
                      aria-label="Search notes on dashboard"
                      value={searchQuery}
                      onChange={(event) => setSearchQuery(event.target.value)}
                      placeholder="Search notes, summaries, or tags"
                      className="w-full bg-transparent text-[12px] outline-none placeholder:text-[#3A3A3A]"
                      style={{
                        color: '#F5F5F5',
                        fontFamily: "'IBM Plex Sans', sans-serif",
                      }}
                    />
                    {searchQuery ? (
                      <button
                        type="button"
                        onClick={() => setSearchQuery('')}
                        className="focus-visible:outline-none"
                        aria-label="Clear note search"
                        style={{ background: 'transparent', border: 'none', color: '#5A5A5A', cursor: 'pointer', padding: 0 }}
                      >
                        <X size={12} />
                      </button>
                    ) : null}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {SORT_OPTIONS.map((option) => (
                      <PillButton
                        key={option.value}
                        active={sortMode === option.value}
                        onClick={() => setSortMode(option.value)}
                        ariaLabel={`Sort notes by ${option.label}`}
                      >
                        {option.label}
                      </PillButton>
                    ))}
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {topTags.map(({ tag, count }) => (
                    <PillButton
                      key={tag}
                      active={selectedTag === tag}
                      onClick={() => setSelectedTag((current) => (current === tag ? null : tag))}
                      ariaLabel={`Filter notes by ${tag}`}
                    >
                      {tag} ({count})
                    </PillButton>
                  ))}
                  {selectedBucket ? (
                    <PillButton
                      active={Boolean(selectedChartKey)}
                      onClick={() => setSelectedChartKey((current) => (current ? null : selectedBucket.key))}
                      ariaLabel="Toggle current chart filter"
                    >
                      {selectedChartKey ? `Month: ${selectedBucket.label}` : `Range: ${selectedBucket.label}`}
                    </PillButton>
                  ) : null}
                </div>

                {showInitialLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 4 }).map((_, index) => (
                      <div
                        key={`note-loading-${index}`}
                        style={{ background: '#0A0A0A', border: '1px solid #1A1A1A', borderRadius: 3, padding: '12px 14px' }}
                      >
                        <SkeletonBlock width="40%" height={13} />
                        <SkeletonBlock width="100%" height={12} style={{ marginTop: 10 }} />
                        <SkeletonBlock width="68%" height={12} style={{ marginTop: 8 }} />
                      </div>
                    ))}
                  </div>
                ) : visibleNotes.length === 0 ? (
                  <EmptyPanel
                    title="No notes match the current filters"
                    description="Try clearing a tag or month filter, or create a note to give the dashboard something to work with."
                    actionLabel={activeFilterCount > 0 ? 'Clear filters' : 'Create note'}
                    onAction={activeFilterCount > 0 ? clearFilters : onCreateNote}
                  />
                ) : (
                  <div className="space-y-2">
                    <AnimatePresence initial={false}>
                      {visibleNotes.map((note, index) => {
                        const isSelected = note.id === selectedNoteId;

                        return (
                          <motion.div
                            key={note.id}
                            layout
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            transition={{ duration: 0.18, delay: index * 0.03 }}
                            role="button"
                            tabIndex={0}
                            onClick={() => openNoteDetails(note)}
                            onKeyDown={(event) => handleNoteCardKeyDown(event, note)}
                            className="cursor-pointer focus-visible:outline-none"
                            style={{
                              background: isSelected ? 'rgba(245,230,66,0.04)' : '#0A0A0A',
                              border: `1px solid ${isSelected ? 'rgba(245,230,66,0.28)' : '#1A1A1A'}`,
                              borderLeft: `3px solid ${isSelected ? '#F5E642' : 'transparent'}`,
                              borderRadius: 3,
                              padding: '12px 14px',
                            }}
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <h3
                                    className="truncate text-[13px]"
                                    style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Mono', monospace", fontWeight: 500 }}
                                  >
                                    {note.title}
                                  </h3>
                                  {isSelected ? <MonoChip yellow>Selected</MonoChip> : null}
                                </div>
                                <p
                                  className="mt-1 line-clamp-2 text-[11px] leading-[1.5]"
                                  style={{ color: '#888888', fontFamily: "'IBM Plex Sans', sans-serif" }}
                                >
                                  {getNotePreview(note)}
                                </p>
                                <div className="mt-2 flex flex-wrap items-center gap-2">
                                  {(note.tags ?? []).slice(0, 3).map((tag) => (
                                    <button
                                      key={tag}
                                      type="button"
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        setSelectedTag((current) => (current === tag ? null : tag));
                                      }}
                                      className="focus-visible:outline-none"
                                      style={{
                                        background: selectedTag === tag ? 'rgba(245,230,66,0.1)' : '#1A1A1A',
                                        color: selectedTag === tag ? '#F5E642' : '#5A5A5A',
                                        border: `1px solid ${selectedTag === tag ? 'rgba(245,230,66,0.22)' : '#252525'}`,
                                        borderRadius: 2,
                                        padding: '2px 6px',
                                        fontFamily: "'IBM Plex Mono', monospace",
                                        fontSize: 9,
                                        letterSpacing: '0.05em',
                                        textTransform: 'uppercase',
                                        cursor: 'pointer',
                                      }}
                                    >
                                      {tag}
                                    </button>
                                  ))}
                                  <span
                                    className="text-[9.5px]"
                                    style={{ color: '#3A3A3A', fontFamily: "'IBM Plex Mono', monospace" }}
                                  >
                                    {formatDistanceToNow(
                                      note.updatedAt instanceof Date ? note.updatedAt : new Date(note.updatedAt),
                                      { addSuffix: true }
                                    )}
                                  </span>
                                </div>
                              </div>

                              <div className="flex flex-col items-end gap-2">
                                {note.confidence ? (
                                  <div
                                    className="flex items-center gap-1 px-2 py-1"
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
                                ) : null}

                                <div className="flex flex-wrap justify-end gap-2">
                                  <button
                                    type="button"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      openNoteDetails(note);
                                    }}
                                    className="focus-visible:outline-none"
                                    style={{
                                      background: 'transparent',
                                      border: '1px solid #252525',
                                      borderRadius: 3,
                                      color: '#F5E642',
                                      padding: '5px 8px',
                                      fontFamily: "'IBM Plex Mono', monospace",
                                      fontSize: 9,
                                      letterSpacing: '0.05em',
                                      textTransform: 'uppercase',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    Details
                                  </button>
                                  <button
                                    type="button"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void handleCopyNote(note);
                                    }}
                                    aria-label={`Copy ${note.title}`}
                                    className="focus-visible:outline-none"
                                    style={{
                                      background: 'transparent',
                                      border: '1px solid #252525',
                                      borderRadius: 3,
                                      color: '#5A5A5A',
                                      padding: '5px 8px',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    <Copy size={11} />
                                  </button>
                                </div>
                              </div>
                            </div>
                          </motion.div>
                        );
                      })}
                    </AnimatePresence>
                  </div>
                )}

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p
                    className="text-[10px] uppercase tracking-[0.08em]"
                    style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                  >
                    Showing {visibleNotes.length} of {filteredNotes.length} matching notes
                    {activeFilterCount > 0 ? ` • ${activeFilterCount} active filters` : ''}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {selectedTag ? <MonoChip yellow>Tag: {selectedTag}</MonoChip> : null}
                    {selectedChartKey && selectedBucket ? <MonoChip yellow>{selectedBucket.description}</MonoChip> : null}
                  </div>
                </div>
              </div>
            </div>
          </motion.div>

          <motion.div variants={itemVariants}>
            <div style={{ background: '#111111', border: '1px solid #252525', borderRadius: 3, height: '100%' }}>
              <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4" style={{ borderBottom: '1px solid #1A1A1A' }}>
                <div className="flex items-center gap-2">
                  <Sparkles size={13} color="#F5E642" />
                  <span
                    className="text-[11px] uppercase tracking-[0.1em]"
                    style={{ color: '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
                  >
                    AI Insights
                  </span>
                </div>
                <MonoChip yellow>{filteredInsights.length} visible</MonoChip>
              </div>

              <div className="space-y-4 p-4">
                <div className="flex flex-wrap gap-2">
                  {(['all', 'summary', 'trend', 'connection', 'suggestion'] as InsightFilter[]).map((filter) => (
                    <PillButton
                      key={filter}
                      active={selectedInsightFilter === filter}
                      onClick={() => setSelectedInsightFilter(filter)}
                    >
                      {filter === 'all' ? 'All' : filter}
                    </PillButton>
                  ))}
                </div>

                {showInitialLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 3 }).map((_, index) => (
                      <div
                        key={`insight-loading-${index}`}
                        style={{ background: '#0A0A0A', border: '1px solid #1A1A1A', borderRadius: 3, padding: '12px 14px' }}
                      >
                        <SkeletonBlock width="100%" height={12} />
                        <SkeletonBlock width="84%" height={12} style={{ marginTop: 8 }} />
                        <SkeletonBlock width="60%" height={10} style={{ marginTop: 12 }} />
                      </div>
                    ))}
                  </div>
                ) : filteredInsights.length === 0 ? (
                  <EmptyPanel
                    title="No insights in this view"
                    description="Switch the insight filter or refresh the dashboard after new documents finish processing."
                    actionLabel="Refresh"
                    onAction={() => handleRefresh()}
                  />
                ) : (
                  <div className="space-y-2">
                    {filteredInsights.map((insight) => {
                      const expanded = expandedInsightId === insight.id;

                      return (
                        <button
                          key={insight.id}
                          type="button"
                          onClick={() => setExpandedInsightId((current) => (current === insight.id ? null : insight.id))}
                          aria-expanded={expanded}
                          className="w-full text-left transition-all duration-150 focus-visible:outline-none"
                          style={{
                            background: expanded ? 'rgba(245,230,66,0.04)' : '#0A0A0A',
                            border: `1px solid ${expanded ? 'rgba(245,230,66,0.22)' : '#1A1A1A'}`,
                            borderRadius: 3,
                            padding: '12px 14px',
                          }}
                        >
                          <p
                            className={cn('text-[12px] leading-[1.6]', !expanded && 'line-clamp-3')}
                            style={{ color: '#888888', fontFamily: "'IBM Plex Sans', sans-serif" }}
                          >
                            {insight.content}
                          </p>
                          <div className="mt-3 flex items-center justify-between gap-2">
                            <span
                              className="text-[9px] uppercase tracking-[0.07em]"
                              style={{ color: expanded ? '#F5E642' : '#3A3A3A', fontFamily: "'IBM Plex Mono', monospace" }}
                            >
                              {insight.type}
                            </span>
                            <span
                              className="text-[9px]"
                              style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace", opacity: 0.75 }}
                            >
                              {Math.round(insight.confidence * 100)}% conf
                            </span>
                          </div>

                          <AnimatePresence initial={false}>
                            {expanded ? (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.18 }}
                                className="overflow-hidden"
                              >
                                <div
                                  className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t pt-3"
                                  style={{ borderColor: '#1A1A1A' }}
                                >
                                  <p
                                    className="text-[10px] uppercase tracking-[0.08em]"
                                    style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                                  >
                                    Updated {formatDistanceToNow(insight.createdAt, { addSuffix: true })}
                                  </p>
                                  <div className="flex flex-wrap gap-2">
                                    <PillButton onClick={insightAction}>{insightActionLabel}</PillButton>
                                    <PillButton onClick={() => handleRefresh()}>Refresh</PillButton>
                                  </div>
                                </div>
                              </motion.div>
                            ) : null}
                          </AnimatePresence>
                        </button>
                      );
                    })}
                  </div>
                )}

                <button
                  type="button"
                  onClick={onViewAllInsights}
                  className="w-full h-9 text-[13px] uppercase tracking-[0.1em] transition-all duration-150 focus-visible:outline-none"
                  style={{
                    fontFamily: "'Bebas Neue', sans-serif",
                    background: 'transparent',
                    border: '1px solid #252525',
                    borderRadius: 3,
                    color: '#5A5A5A',
                    cursor: 'pointer',
                  }}
                >
                  Search insights
                </button>
              </div>
            </div>
          </motion.div>
        </div>

        <motion.div variants={itemVariants}>
          <div style={{ background: '#111111', border: '1px solid #252525', borderRadius: 3 }}>
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4" style={{ borderBottom: '1px solid #1A1A1A' }}>
              <div className="flex items-center gap-2">
                <TrendingUp size={13} color="#F5E642" />
                <span
                  className="text-[11px] uppercase tracking-[0.1em]"
                  style={{ color: '#888888', fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  Knowledge Growth
                </span>
                {selectedBucket ? <MonoChip>{selectedBucket.description}</MonoChip> : null}
              </div>
              <div className="flex flex-wrap gap-2">
                {RANGE_OPTIONS.map((option) => (
                  <PillButton
                    key={option.value}
                    active={selectedRangeMonths === option.value}
                    onClick={() => setSelectedRangeMonths(option.value)}
                    ariaLabel={`Show ${option.label} growth`}
                  >
                    {option.label}
                  </PillButton>
                ))}
              </div>
            </div>

            <div className="space-y-4 px-5 pb-4 pt-5">
              {showInitialLoading ? (
                <div className="flex h-28 items-end gap-1.5">
                  {Array.from({ length: selectedRangeMonths }).map((_, index) => (
                    <SkeletonBlock
                      key={`chart-loading-${index}`}
                      width="100%"
                      height={`${24 + (index % 5) * 12}%`}
                      style={{ flex: 1, borderRadius: '2px 2px 0 0' }}
                    />
                  ))}
                </div>
              ) : chartBuckets.length === 0 ? (
                <EmptyPanel
                  title="No activity yet"
                  description="As notes are created or updated, this chart will become a quick drill-down for recent workspace momentum."
                  actionLabel="Create note"
                  onAction={onCreateNote}
                />
              ) : (
                <>
                  <div className="flex h-28 items-end gap-1.5">
                    {chartBuckets.map((bucket) => {
                      const active = selectedChartKey === bucket.key;

                      return (
                        <button
                          key={bucket.key}
                          type="button"
                          aria-pressed={active}
                          aria-label={`${bucket.description}, ${bucket.count} notes`}
                          onClick={() =>
                            startTransition(() => {
                              setSelectedChartKey((current) => (current === bucket.key ? null : bucket.key));
                            })
                          }
                          className="flex-1 focus-visible:outline-none"
                          style={{
                            height: `${bucket.height}%`,
                            background: active || bucket.isCurrent ? '#F5E642' : 'rgba(245,230,66,0.18)',
                            borderRadius: '2px 2px 0 0',
                            border: `1px solid ${active ? '#F5E642' : 'transparent'}`,
                            boxShadow: active ? '0 0 12px rgba(245,230,66,0.25)' : 'none',
                          }}
                        />
                      );
                    })}
                  </div>

                  <div className="flex justify-between gap-1">
                    {chartBuckets.map((bucket) => (
                      <span
                        key={bucket.key}
                        className="flex-1 text-center text-[8.5px] uppercase tracking-[0.04em]"
                        style={{
                          color: selectedChartKey === bucket.key ? '#F5E642' : '#3A3A3A',
                          fontFamily: "'IBM Plex Mono', monospace",
                        }}
                      >
                        {bucket.label}
                      </span>
                    ))}
                  </div>

                  {selectedBucket ? (
                    <div
                      className="flex flex-col gap-3 border-t pt-3 md:flex-row md:items-center md:justify-between"
                      style={{ borderColor: '#1A1A1A' }}
                    >
                      <div>
                        <p
                          className="text-[10px] uppercase tracking-[0.08em]"
                          style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                        >
                          Drill-down
                        </p>
                        <p
                          className="mt-1 text-[13px] leading-[1.6]"
                          style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Sans', sans-serif" }}
                        >
                          {selectedBucket.count} note{selectedBucket.count === 1 ? '' : 's'} updated in {selectedBucket.description}.
                          {selectedChartKey ? ' The recent notes list is filtered to this period.' : ' Select a bar to filter the notes list instantly.'}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <PillButton onClick={() => setSelectedChartKey(selectedBucket.key)}>Filter Notes</PillButton>
                        <PillButton onClick={onViewAllNotes}>Open Notes</PillButton>
                      </div>
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </div>
        </motion.div>
      </motion.div>

      <Sheet open={noteSheetOpen} onOpenChange={setNoteSheetOpen}>
        <SheetContent
          side="right"
          className="border-zinc-800 bg-[#111111] p-0 text-[#F5F5F5] sm:max-w-md"
          aria-describedby={selectedNote ? `dashboard-note-description-${selectedNote.id}` : undefined}
        >
          {selectedNote ? (
            <>
              <SheetHeader className="border-b border-zinc-900 bg-[#0A0A0A] p-5">
                <div className="flex items-start gap-3">
                  <div
                    style={{
                      width: 36,
                      height: 36,
                      background: 'rgba(245,230,66,0.08)',
                      border: '1px solid rgba(245,230,66,0.15)',
                      borderRadius: 3,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <FileText size={16} color="#F5E642" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <SheetTitle
                      className="truncate text-[20px] uppercase tracking-[0.04em]"
                      style={{ fontFamily: "'Bebas Neue', sans-serif", color: '#F5F5F5' }}
                    >
                      {selectedNote.title}
                    </SheetTitle>
                    <SheetDescription
                      id={`dashboard-note-description-${selectedNote.id}`}
                      className="mt-2 text-[12px] leading-[1.6]"
                      style={{ color: '#888888', fontFamily: "'IBM Plex Sans', sans-serif" }}
                    >
                      {getNotePreview(selectedNote)}
                    </SheetDescription>
                  </div>
                </div>
              </SheetHeader>

              <div className="space-y-5 p-5">
                <div className="flex flex-wrap gap-2">
                  <MonoChip yellow>{selectedNote.type}</MonoChip>
                  {(selectedNote.tags ?? []).map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => {
                        setSelectedTag(tag);
                        setNoteSheetOpen(false);
                      }}
                      className="focus-visible:outline-none"
                      style={{
                        background: selectedTag === tag ? 'rgba(245,230,66,0.1)' : '#0A0A0A',
                        color: selectedTag === tag ? '#F5E642' : '#5A5A5A',
                        border: `1px solid ${selectedTag === tag ? 'rgba(245,230,66,0.2)' : '#252525'}`,
                        borderRadius: 2,
                        padding: '4px 7px',
                        fontFamily: "'IBM Plex Mono', monospace",
                        fontSize: 9,
                        letterSpacing: '0.05em',
                        textTransform: 'uppercase',
                        cursor: 'pointer',
                      }}
                    >
                      {tag}
                    </button>
                  ))}
                </div>

                <div
                  style={{
                    background: '#0A0A0A',
                    border: '1px solid #1A1A1A',
                    borderRadius: 3,
                    padding: '14px 16px',
                  }}
                >
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p
                        className="text-[9px] uppercase tracking-[0.08em]"
                        style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                      >
                        Updated
                      </p>
                      <p className="mt-1 text-[12px]" style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Sans', sans-serif" }}>
                        {format(selectedNote.updatedAt instanceof Date ? selectedNote.updatedAt : new Date(selectedNote.updatedAt), 'MMM d, yyyy')}
                      </p>
                    </div>
                    <div>
                      <p
                        className="text-[9px] uppercase tracking-[0.08em]"
                        style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                      >
                        Confidence
                      </p>
                      <p className="mt-1 text-[12px]" style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Sans', sans-serif" }}>
                        {selectedNote.confidence ? `${Math.round(selectedNote.confidence * 100)}%` : 'Not scored'}
                      </p>
                    </div>
                    <div>
                      <p
                        className="text-[9px] uppercase tracking-[0.08em]"
                        style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                      >
                        Connections
                      </p>
                      <p className="mt-1 text-[12px]" style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Sans', sans-serif" }}>
                        {selectedNote.connections?.length ?? 0}
                      </p>
                    </div>
                    <div>
                      <p
                        className="text-[9px] uppercase tracking-[0.08em]"
                        style={{ color: '#5A5A5A', fontFamily: "'IBM Plex Mono', monospace" }}
                      >
                        Words
                      </p>
                      <p className="mt-1 text-[12px]" style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Sans', sans-serif" }}>
                        {selectedNote.word_count ?? selectedNote.content.split(/\s+/).filter(Boolean).length}
                      </p>
                    </div>
                  </div>
                </div>

                <div>
                  <p
                    className="text-[10px] uppercase tracking-[0.08em]"
                    style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                  >
                    Next Actions
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <PillButton onClick={onViewAllNotes}>Open Notes</PillButton>
                    <PillButton onClick={onViewGraph}>Explore Graph</PillButton>
                    <PillButton
                      onClick={() => {
                        void handleCopyNote(selectedNote);
                      }}
                    >
                      Copy Title
                    </PillButton>
                  </div>
                </div>

                {selectedNote.summary ? (
                  <div>
                    <p
                      className="text-[10px] uppercase tracking-[0.08em]"
                      style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                    >
                      Summary
                    </p>
                    <p
                      className="mt-2 text-[13px] leading-[1.7]"
                      style={{ color: '#C8C8C8', fontFamily: "'IBM Plex Sans', sans-serif" }}
                    >
                      {selectedNote.summary}
                    </p>
                  </div>
                ) : null}

                <div>
                  <p
                    className="text-[10px] uppercase tracking-[0.08em]"
                    style={{ color: '#F5E642', fontFamily: "'IBM Plex Mono', monospace" }}
                  >
                    Preview
                  </p>
                  <p
                    className="mt-2 text-[13px] leading-[1.7]"
                    style={{ color: '#888888', fontFamily: "'IBM Plex Sans', sans-serif" }}
                  >
                    {selectedNote.content.length > 420 ? `${selectedNote.content.slice(0, 420)}...` : selectedNote.content}
                  </p>
                </div>

                {selectedNote.approvalStatus ? (
                  <div
                    className="flex items-center justify-between gap-3"
                    style={{
                      background: '#0A0A0A',
                      border: '1px solid #1A1A1A',
                      borderRadius: 3,
                      padding: '12px 14px',
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <CheckCircle2 size={13} color="#F5E642" />
                      <span
                        className="text-[10px] uppercase tracking-[0.08em]"
                        style={{ color: '#F5F5F5', fontFamily: "'IBM Plex Mono', monospace" }}
                      >
                        Approval: {selectedNote.approvalStatus}
                      </span>
                    </div>
                    {onOpenWorkflows ? <PillButton onClick={onOpenWorkflows}>Open Queue</PillButton> : null}
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  );
}
