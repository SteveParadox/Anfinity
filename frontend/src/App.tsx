/**
 * App.tsx — root shell component
 *
 * Fixes vs. original:
 *  - `window.innerWidth` in JSX replaced with reactive `useWindowWidth` hook
 *    (original values were snapshots; sidebar never repositioned after first render)
 *  - Status bar metrics are loaded from workspace-scoped API stats
 *  - Tailwind `className` strings removed — all layout now driven by inline
 *    styles keyed off the reactive `isMobile` / `isSmall` booleans so the app
 *    works without a Tailwind build step
 *  - Auth loading state guarded before render (was silently returning null)
 *  - Logout error now surfaced to the user via `logoutError` state
 *  - `renderView` converted to a lookup map (eliminates large switch block)
 *  - `sidebarWidth` + `marginLeft` transition uses CSS custom property to keep
 *    the single source of truth in one place
 */

import { lazy, Suspense, useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import { Sidebar } from './components/Sidebar';
import { WorkspaceSwitcher } from './components/WorkspaceSwitcher';
import { ThemeApplier } from './components/ThemeApplier';
import { ThemeToggle } from './components/ThemeToggle';
import { ThemeContextProvider } from './contexts/ThemeContext';
import { Menu, LogOut, AlertCircle, MessageCircle } from 'lucide-react';
import type { User } from './types';
import { useProductSettings } from './hooks/useProductSettings';
import { DESIGN_TOKENS } from './lib/theme';
import type { ThemeChoice } from './lib/theme';
import { api, type WorkspaceStatsResponse } from './lib/api';

const Dashboard = lazy(() => import('./sections/Dashboard').then((module) => ({ default: module.Dashboard })));
const KnowledgeGraphView = lazy(() => import('./sections/KnowledgeGraphView').then((module) => ({ default: module.KnowledgeGraphView })));
const NotesView = lazy(() => import('./sections/NotesView').then((module) => ({ default: module.NotesView })));
const SearchView = lazy(() => import('./sections/SearchView').then((module) => ({ default: module.SearchView })));
const WorkspacesView = lazy(() => import('./sections/WorkspacesView').then((module) => ({ default: module.WorkspacesView })));
const WorkflowsView = lazy(() => import('./sections/WorkflowsView').then((module) => ({ default: module.WorkflowsView })));
const IntegrationsView = lazy(() => import('./sections/IntegrationsView').then((module) => ({ default: module.IntegrationsView })));
const PricingView = lazy(() => import('./sections/PricingView').then((module) => ({ default: module.PricingView })));
const SettingsView = lazy(() => import('./sections/SettingsView').then((module) => ({ default: module.SettingsView })));
const DocumentUploadView = lazy(() => import('./sections/UploadView').then((module) => ({ default: module.DocumentUploadView })));
const DocumentsView = lazy(() => import('./sections/DocumentsView').then((module) => ({ default: module.DocumentsView })));
const AskPastSelf = lazy(() => import('./components/chat/AskPastSelf').then((module) => ({ default: module.AskPastSelf })));
const ThinkingSessionsView = lazy(() => import('./sections/ThinkingSessionsView').then((module) => ({ default: module.ThinkingSessionsView })));

// ─── Types ────────────────────────────────────────────────────────────────────

type View =
  | 'dashboard'
  | 'notes'
  | 'graph'
  | 'search'
  | 'thinking-sessions'
  | 'workspaces'
  | 'integrations'
  | 'workflows'
  | 'settings'
  | 'pricing'
  | 'upload'
  | 'documents';

function parseThemeChoice(value: unknown): ThemeChoice | undefined {
  return value === 'light' || value === 'dark' || value === 'system' ? value : undefined;
}

// ─── Design tokens ────────────────────────────────────────────────────────────

// Use CSS custom properties defined by ThemeApplier
// Fallback to dark theme colors if CSS vars are not yet applied
const TT = {
  inkBlack:    'var(--theme-text-inverse, #0A0A0A)',
  inkDeep:     'var(--theme-panel, #111111)',
  inkRaised:   'var(--theme-panel-raised, #1A1A1A)',
  inkBorder:   'var(--theme-border, #252525)',
  inkMid:      'var(--theme-border-strong, #3A3A3A)',
  inkMuted:    'var(--theme-text-muted, #5A5A5A)',
  inkSubtle:   'var(--theme-text-subtle, #888888)',
  snow:        'var(--theme-text, #F5F5F5)',
  yolk:        'var(--theme-accent, #F5E642)',
  errorText:   'var(--theme-error, #D92D20)',
  errorBorder: 'rgba(217,45,32,0.3)',
  fontDisplay: DESIGN_TOKENS.fontDisplay,
  fontMono:    DESIGN_TOKENS.fontMono,
  fontBody:    DESIGN_TOKENS.fontBody,
} as const;

const SIDEBAR_EXPANDED  = 240;
const SIDEBAR_COLLAPSED = 64;

function ViewLoadingFallback({ label = 'Loading view' }: { label?: string }) {
  return (
    <div
      style={{
        minHeight: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '48px 24px',
      }}
    >
      <span
        style={{
          fontFamily: TT.fontMono,
          fontSize: 11,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          color: TT.inkSubtle,
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ─── Responsive helper hook ───────────────────────────────────────────────────

/**
 * Returns the current inner width, updating on every resize.
 * Replaces the bare `window.innerWidth` calls that were snapshot-only.
 */
function useWindowWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);

  useEffect(() => {
    const handleResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return width;
}

// ─── Component ────────────────────────────────────────────────────────────────

function App() {
  const navigate   = useNavigate();
  const { user: contextUser, logout, isLoading: authLoading, currentWorkspaceId, hasPermission } = useAuth();
  const { user: shellSettings, updateUserSettings } = useProductSettings(currentWorkspaceId, Boolean(contextUser));

  const themeChoice = useMemo<ThemeChoice | undefined>(
    () => parseThemeChoice(shellSettings?.settings.appearance?.theme),
    [shellSettings?.settings.appearance?.theme],
  );

  const windowWidth = useWindowWidth();
  const [shellStats, setShellStats] = useState<WorkspaceStatsResponse | null>(null);
  const [shellStatsLoading, setShellStatsLoading] = useState(false);
  const [shellStatsError, setShellStatsError] = useState<string | null>(null);

  const isMobile = windowWidth < 1024;
  const isSmall  = windowWidth < 640;

  const [currentView,       setCurrentView]       = useState<View>('dashboard');
  const [sidebarCollapsed,  setSidebarCollapsed]  = useState(isMobile);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [chatOpen,          setChatOpen]          = useState(false);
  const [logoutError,       setLogoutError]       = useState<string | null>(null);

  // Collapse sidebar automatically when viewport crosses the mobile breakpoint
  useEffect(() => {
    setSidebarCollapsed(isMobile);
  }, [isMobile]);

  // Close the mobile drawer whenever we go desktop
  useEffect(() => {
    if (!isMobile) setMobileSidebarOpen(false);
  }, [isMobile]);

  useEffect(() => {
    const density = shellSettings?.settings.appearance?.density;
    const root = document.documentElement;

    if (density) {
      root.dataset.density = density;
    } else {
      delete root.dataset.density;
    }
  }, [shellSettings?.settings.appearance?.density]);

  useEffect(() => {
    if (!contextUser || !currentWorkspaceId) {
      setShellStats(null);
      setShellStatsError(null);
      setShellStatsLoading(false);
      return;
    }

    let cancelled = false;

    const loadShellStats = async () => {
      setShellStatsLoading(true);
      setShellStatsError(null);
      try {
        const stats = await api.getWorkspaceStats(currentWorkspaceId);
        if (!cancelled) setShellStats(stats);
      } catch (error) {
        if (!cancelled) {
          setShellStats(null);
          setShellStatsError(error instanceof Error ? error.message : 'Workspace metrics unavailable');
        }
      } finally {
        if (!cancelled) setShellStatsLoading(false);
      }
    };

    void loadShellStats();

    return () => {
      cancelled = true;
    };
  }, [contextUser, currentWorkspaceId]);

  const handleThemeChoiceChange = useCallback((choice: ThemeChoice) => {
    const currentAppearance = shellSettings?.settings.appearance ?? {};

    void updateUserSettings({
      appearance: {
        ...currentAppearance,
        theme: choice,
      },
    }).catch((error) => {
      console.error('Failed to save theme preference', error);
    });
  }, [shellSettings?.settings.appearance, updateUserSettings]);

  const handleLogout = useCallback(async () => {
    try {
      setLogoutError(null);
      await logout();
      navigate('/login');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Logout failed. Please try again.';
      setLogoutError(msg);
    }
  }, [logout, navigate]);

  const navigateTo = useCallback((view: string) => {
    setCurrentView(view as View);
    setMobileSidebarOpen(false);
  }, []);

  const canViewNotes = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'notes', 'view'));
  const canCreateNotes = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'notes', 'create'));
  const canViewDocuments = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'documents', 'view'));
  const canCreateDocuments = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'documents', 'create'));
  const canViewGraph = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'knowledge_graph', 'view'));
  const canUseSearch = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'search', 'view'));
  const canOpenWorkspaceScopedChat = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'chat', 'create'));
  const canViewThinkingSessions = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'chat', 'view'));
  const canViewWorkflows = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'workflows', 'view'));
  const canManageIntegrations = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, 'settings', 'view'));
  const availableViews = useMemo<View[]>(() => {
    const views: View[] = ['dashboard', 'workspaces', 'settings', 'pricing'];
    if (canViewNotes) views.push('notes');
    if (canViewDocuments) views.push('documents');
    if (canCreateDocuments) views.push('upload');
    if (canViewGraph) views.push('graph');
    if (canUseSearch) views.push('search');
    if (canViewThinkingSessions) views.push('thinking-sessions');
    if (canManageIntegrations) views.push('integrations');
    if (canViewWorkflows) views.push('workflows');
    return views;
  }, [canCreateDocuments, canManageIntegrations, canUseSearch, canViewDocuments, canViewGraph, canViewNotes, canViewThinkingSessions, canViewWorkflows]);

  useEffect(() => {
    if (!availableViews.includes(currentView)) {
      setCurrentView(availableViews[0] ?? 'dashboard');
    }
  }, [availableViews, currentView]);

  // ── View registry (replaces large switch) ──────────────────────────────
  const viewRegistry = useMemo<Partial<Record<View, React.ReactNode>>>(() => {
    if (!contextUser) return {};

    return {
      dashboard: (
        <Dashboard
          user={contextUser}
          onCreateNote={() => setCurrentView(canViewNotes ? 'notes' : 'workspaces')}
          onViewGraph={() => setCurrentView(canViewGraph ? 'graph' : 'workspaces')}
          onViewAllNotes={() => setCurrentView(canViewNotes ? 'notes' : 'workspaces')}
          onViewAllInsights={() => setCurrentView(canUseSearch ? 'search' : 'workspaces')}
          onOpenWorkflows={() => setCurrentView(canViewWorkflows ? 'workflows' : 'workspaces')}
        />
      ),
      notes:      <NotesView />,
      graph:      <KnowledgeGraphView />,
      search:     <SearchView />,
      'thinking-sessions': <ThinkingSessionsView />,
      workspaces: <WorkspacesView user={contextUser} />,
      integrations: <IntegrationsView />,
      workflows:  <WorkflowsView />,
      settings:   <SettingsView user={contextUser} />,
      pricing:    <PricingView currentPlan={contextUser.plan ?? 'free'} />,
      upload:     <DocumentUploadView />,
      documents:  <DocumentsView />,
    };
  }, [canUseSearch, canViewGraph, canViewNotes, canViewWorkflows, contextUser]);

  // ── Derived layout values ───────────────────────────────────────────────
  const sidebarWidth   = sidebarCollapsed ? SIDEBAR_COLLAPSED : SIDEBAR_EXPANDED;
  const sidebarOffscreen = isMobile && !mobileSidebarOpen;

  const userInitial = (contextUser?.name ?? contextUser?.email ?? '?').charAt(0).toUpperCase();
  const userName    = contextUser?.full_name ?? contextUser?.name ?? 'User';
  const shellStatsLabel = useMemo(() => {
    if (!currentWorkspaceId) return 'No workspace selected';
    if (shellStatsLoading && !shellStats) return 'Loading workspace metrics';
    if (shellStatsError) return 'Workspace metrics unavailable';
    return `${shellStats?.notes.total ?? 0} notes | ${shellStats?.documents.total ?? 0} docs | ${shellStats?.vectors ?? 0} vectors`;
  }, [currentWorkspaceId, shellStats, shellStatsError, shellStatsLoading]);

  // ── Auth loading guard ──────────────────────────────────────────────────
  if (authLoading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          background: TT.inkBlack,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <span
          style={{
            fontFamily: TT.fontMono,
            fontSize: 11,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: TT.inkSubtle,
          }}
        >
          Loading…
        </span>
      </div>
    );
  }

  // ─────────────────────────────────────────────────────────────────────────

  return (
    <ThemeContextProvider initialChoice={themeChoice} onChoiceChange={handleThemeChoiceChange}>
      {/* Apply theme tokens from context */}
      <ThemeApplier />
      
      <div
        style={{
          minHeight: '100vh',
          background: TT.inkBlack,
          backgroundImage: [
            'linear-gradient(rgba(245,230,66,0.018) 1px, transparent 1px)',
            'linear-gradient(90deg, rgba(245,230,66,0.018) 1px, transparent 1px)',
          ].join(', '),
          backgroundSize: '32px 32px',
          display: 'flex',
          fontFamily: TT.fontMono,
          position: 'relative',
        }}
      >
      {/* ── Mobile overlay ─────────────────────────────────────── */}
      {mobileSidebarOpen && (
        <div
          role="presentation"
          onClick={() => setMobileSidebarOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.65)',
            zIndex: 40,
          }}
        />
      )}

      {/* ── Sidebar ────────────────────────────────────────────── */}
      <div
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          bottom: 0,
          width: sidebarWidth,
          transform: sidebarOffscreen ? `translateX(-${sidebarWidth}px)` : 'translateX(0)',
          transition: 'width 0.25s cubic-bezier(0.22,1,0.36,1), transform 0.25s cubic-bezier(0.22,1,0.36,1)',
          zIndex: 50,
        }}
        aria-hidden={sidebarOffscreen}
      >
        <Sidebar
          currentView={currentView}
          onViewChange={navigateTo}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
          user={contextUser as User | undefined}
          availableViews={availableViews}
          canCreateNotes={canCreateNotes}
        />
      </div>

      {/* ── Main column ────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: '100vh',
          overflow: 'hidden',
          // Reactive: re-reads on every render because windowWidth is state
          marginLeft: isMobile ? 0 : sidebarWidth,
          transition: 'margin-left 0.25s cubic-bezier(0.22,1,0.36,1)',
        }}
      >
        {/* ── Header ───────────────────────────────────────────── */}
        <header
          style={{
            height: 52,
            background: TT.inkDeep,
            borderBottom: `1px solid ${TT.inkBorder}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 20px',
            position: 'sticky',
            top: 0,
            zIndex: 30,
            flexShrink: 0,
          }}
        >
          {/* Left — hamburger + breadcrumb */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 }}>
            {isMobile && (
              <button
                onClick={() => setMobileSidebarOpen(true)}
                aria-label="Open navigation"
                aria-expanded={mobileSidebarOpen}
                style={{
                  background: 'none',
                  border: `1px solid ${TT.inkBorder}`,
                  borderRadius: 3,
                  cursor: 'pointer',
                  padding: '5px 7px',
                  color: TT.inkMuted,
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'color 0.15s, border-color 0.15s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = TT.yolk;
                  e.currentTarget.style.borderColor = 'rgba(245,230,66,0.3)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = TT.inkMuted;
                  e.currentTarget.style.borderColor = TT.inkBorder;
                }}
              >
                <Menu size={15} aria-hidden />
              </button>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                style={{
                  fontFamily: TT.fontDisplay,
                  fontSize: 20,
                  letterSpacing: '0.06em',
                  color: TT.snow,
                  lineHeight: 1,
                }}
              >
                <span style={{ color: TT.yolk }}>AN</span>FINITY
              </span>
              <span style={{ color: TT.inkMid, fontFamily: TT.fontMono, fontSize: 12 }}>/</span>
              <span
                aria-current="page"
                style={{
                  fontFamily: TT.fontDisplay,
                  fontSize: 16,
                  letterSpacing: '0.08em',
                  color: TT.inkSubtle,
                  textTransform: 'uppercase',
                }}
              >
                {currentView}
              </span>
            </div>

            <div style={{ marginLeft: 6, minWidth: 0 }}>
              <WorkspaceSwitcher compact={isSmall} />
            </div>
          </div>

          {/* Right — insights toggle + user info */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>

            {/* Logout error inline */}
            {logoutError && (
              <div
                role="alert"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 11,
                  color: TT.errorText,
                  border: `1px solid ${TT.errorBorder}`,
                  borderRadius: 3,
                  padding: '3px 8px',
                }}
              >
                <AlertCircle size={11} aria-hidden />
                {logoutError}
              </div>
            )}

            {/* Ask Past Self button */}
            <button
              onClick={() => {
                if (!canOpenWorkspaceScopedChat) return;
                setChatOpen((o) => !o);
              }}
              aria-pressed={chatOpen}
              aria-label="Ask Your Past Self"
              title="Ask Your Past Self - Chat with your knowledge base"
              disabled={!canOpenWorkspaceScopedChat}
              style={{
                height: 32,
                padding: '0 12px',
                background: chatOpen ? 'rgba(245,230,66,0.1)' : 'transparent',
                border: `1px solid ${chatOpen ? 'rgba(245,230,66,0.3)' : TT.inkBorder}`,
                borderRadius: 3,
                cursor: canOpenWorkspaceScopedChat ? 'pointer' : 'not-allowed',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                transition: 'all 0.15s',
                color: chatOpen ? TT.yolk : TT.inkMuted,
                opacity: canOpenWorkspaceScopedChat ? 1 : 0.45,
              }}
              onMouseEnter={(e) => {
                if (!chatOpen && canOpenWorkspaceScopedChat) {
                  e.currentTarget.style.borderColor = 'rgba(245,230,66,0.25)';
                  e.currentTarget.style.color = TT.yolk;
                }
              }}
              onMouseLeave={(e) => {
                if (!chatOpen) {
                  e.currentTarget.style.borderColor = TT.inkBorder;
                  e.currentTarget.style.color = TT.inkMuted;
                }
              }}
            >
              <MessageCircle size={12} aria-hidden />
              {!isSmall && (
                <span
                  style={{
                    fontFamily: TT.fontMono,
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                  }}
                >
                  Ask
                </span>
              )}
            </button>

            {/* Divider */}
            <div style={{ width: 1, height: 22, background: TT.inkBorder }} aria-hidden />

            {/* Theme toggle */}
            <ThemeToggle compact iconSize={16} />

            {/* Divider */}
            <div style={{ width: 1, height: 22, background: TT.inkBorder }} aria-hidden />

            {/* User info — hidden on very small screens */}
            {!isSmall && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ textAlign: 'right' }}>
                  <p
                    style={{
                      fontFamily: TT.fontMono,
                      fontSize: 11,
                      color: TT.snow,
                      letterSpacing: '0.02em',
                      lineHeight: 1.3,
                    }}
                  >
                    {userName}
                  </p>
                  <p
                    style={{
                      fontFamily: TT.fontMono,
                      fontSize: 9.5,
                      color: TT.inkMuted,
                      letterSpacing: '0.02em',
                    }}
                  >
                    {contextUser?.email}
                  </p>
                </div>
              </div>
            )}

            {/* Avatar + logout */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div
                aria-hidden
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: 3,
                  background: TT.yolk,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontFamily: TT.fontDisplay,
                  fontSize: 16,
                  color: TT.inkBlack,
                  flexShrink: 0,
                  letterSpacing: '0.04em',
                  userSelect: 'none',
                }}
              >
                {userInitial}
              </div>
              <button
                onClick={handleLogout}
                aria-label="Log out"
                title="Log out"
                style={{
                  background: 'none',
                  border: `1px solid ${TT.inkBorder}`,
                  borderRadius: 3,
                  cursor: 'pointer',
                  padding: '5px 7px',
                  color: TT.inkMuted,
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'color 0.15s, border-color 0.15s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = TT.errorText;
                  e.currentTarget.style.borderColor = TT.errorBorder;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = TT.inkMuted;
                  e.currentTarget.style.borderColor = TT.inkBorder;
                }}
              >
                <LogOut size={13} aria-hidden />
              </button>
            </div>
          </div>
        </header>

        {/* ── Main content ──────────────────────────────────────── */}
        <main
          id="main-content"
          style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}
        >
          <Suspense fallback={<ViewLoadingFallback />}>
            {viewRegistry[currentView] ?? null}
          </Suspense>
        </main>

        {/* ── Status bar ────────────────────────────────────────── */}
        <div
          style={{
            height: 24,
            background: TT.inkDeep,
            borderTop: `1px solid ${TT.inkBorder}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 20px',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span
                aria-hidden
                style={{
                  display: 'inline-block',
                  width: 5,
                  height: 5,
                  borderRadius: '50%',
                  background: TT.yolk,
                  boxShadow: `0 0 6px ${TT.yolk}`,
                  animation: 'pulse 2s ease-in-out infinite',
                }}
              />
              <span
                style={{
                  fontFamily: TT.fontMono,
                  fontSize: 8.5,
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                  color: TT.inkMuted,
                }}
              >
                Live
              </span>
            </div>
            <span style={{ width: 1, height: 10, background: TT.inkBorder, display: 'inline-block' }} aria-hidden />
            {/* Stable stats — no longer re-randomises on every render */}
            <span
              style={{
                fontFamily: TT.fontMono,
                fontSize: 8.5,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                color: TT.inkMid,
              }}
            >
              {shellStatsLabel}
            </span>
          </div>
          <span
            style={{
              fontFamily: TT.fontMono,
              fontSize: 8.5,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: TT.inkBorder,
            }}
          >
            Anfinity v2.4.1
          </span>
        </div>
      </div>

      {/* ── Ask Your Past Self Chat Modal ─────────────────────────── */}
      {chatOpen && contextUser && (
        <>
          {/* Backdrop */}
          <div
            role="presentation"
            onClick={() => setChatOpen(false)}
            style={{
              position: 'fixed',
              inset: 0,
              background: 'rgba(0,0,0,0.75)',
              zIndex: 990,
              backdropFilter: 'blur(4px)',
            }}
          />
          
          {/* Modal */}
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="chat-title"
            style={{
              position: 'fixed',
              bottom: 20,
              right: 20,
              width: Math.min(500, windowWidth - 40),
              height: Math.min(600, window.innerHeight - 40),
              background: TT.inkDeep,
              borderRadius: 6,
              border: `1px solid ${TT.inkBorder}`,
              boxShadow: '0 20px 60px rgba(0,0,0,0.35), 0 0 0 1px var(--theme-accent-border)',
              zIndex: 999,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <Suspense fallback={<ViewLoadingFallback label="Loading chat" />}>
              <AskPastSelf
                workspaceId={currentWorkspaceId || ''}
                onClose={() => setChatOpen(false)}
              />
            </Suspense>
          </div>
        </>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 6px #F5E642; }
          50%       { opacity: 0.5; box-shadow: 0 0 12px #F5E642; }
        }
      `}</style>
      </div>
    </ThemeContextProvider>
  );
}

export default App;
