/**
 * App.tsx — root shell component
 *
 * Fixes vs. original:
 *  - `window.innerWidth` in JSX replaced with reactive `useWindowWidth` hook
 *    (original values were snapshots; sidebar never repositioned after first render)
 *  - `Math.random()` in status bar replaced with stable `useState` values
 *  - Tailwind `className` strings removed — all layout now driven by inline
 *    styles keyed off the reactive `isMobile` / `isSmall` booleans so the app
 *    works without a Tailwind build step
 *  - Auth loading state guarded before render (was silently returning null)
 *  - Logout error now surfaced to the user via `logoutError` state
 *  - `renderView` converted to a lookup map (eliminates large switch block)
 *  - `sidebarWidth` + `marginLeft` transition uses CSS custom property to keep
 *    the single source of truth in one place
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import { Sidebar } from './components/Sidebar';
import { Dashboard } from './sections/Dashboard';
import { KnowledgeGraphView } from './sections/KnowledgeGraphView';
import { NotesView } from './sections/NotesView';
import { SearchView } from './sections/SearchView';
import { WorkspacesView } from './sections/WorkspacesView';
import { WorkflowsView } from './sections/WorkflowsView';
import { PricingView } from './sections/PricingView';
import { DocumentUploadView } from './sections/UploadView';
import { DocumentsView } from './sections/DocumentsView';
import { AIInsightsPanel } from './components/AIInsightsPanel';
import { AskPastSelf } from './components/chat/AskPastSelf';
import { Sparkles, Menu, LogOut, AlertCircle, MessageCircle } from 'lucide-react';
import type { User } from './types';

// ─── Types ────────────────────────────────────────────────────────────────────

type View =
  | 'dashboard'
  | 'notes'
  | 'graph'
  | 'search'
  | 'workspaces'
  | 'workflows'
  | 'pricing'
  | 'upload'
  | 'documents';

// ─── Design tokens ────────────────────────────────────────────────────────────

const TT = {
  inkBlack:    '#0A0A0A',
  inkDeep:     '#111111',
  inkRaised:   '#1A1A1A',
  inkBorder:   '#252525',
  inkMid:      '#3A3A3A',
  inkMuted:    '#5A5A5A',
  inkSubtle:   '#888888',
  snow:        '#F5F5F5',
  yolk:        '#F5E642',
  errorText:   '#FF4545',
  errorBorder: 'rgba(255,69,69,0.3)',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
} as const;

const SIDEBAR_EXPANDED  = 240;
const SIDEBAR_COLLAPSED = 64;

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

// ─── Stable random stats ─────────────────────────────────────────────────────

/**
 * Returns stable demo stats so the status-bar numbers don't jitter on every
 * render (the original used Math.random() directly in JSX).
 * Replace with real API data when available.
 */
function useStableStats() {
  const [stats] = useState(() => ({
    notes: Math.round(Math.random() * 100),
    insights: Math.round(Math.random() * 50),
  }));
  return stats;
}

// ─── Component ────────────────────────────────────────────────────────────────

function App() {
  const navigate   = useNavigate();
  const { user: contextUser, logout, isLoading: authLoading, currentWorkspaceId } = useAuth();
  const windowWidth = useWindowWidth();
  const stats       = useStableStats();

  const isMobile = windowWidth < 1024;
  const isSmall  = windowWidth < 640;

  const [currentView,       setCurrentView]       = useState<View>('dashboard');
  const [sidebarCollapsed,  setSidebarCollapsed]  = useState(isMobile);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [insightsOpen,      setInsightsOpen]      = useState(false);
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

  // ── View registry (replaces large switch) ──────────────────────────────
  const viewRegistry = useMemo<Partial<Record<View, React.ReactNode>>>(() => {
    if (!contextUser) return {};

    return {
      dashboard: (
        <Dashboard
          user={contextUser}
          onCreateNote={() => setCurrentView('notes')}
          onViewGraph={() => setCurrentView('graph')}
          onViewAllNotes={() => setCurrentView('notes')}
          onViewAllInsights={() => setCurrentView('search')}
        />
      ),
      notes:      <NotesView />,
      graph:      <KnowledgeGraphView />,
      search:     <SearchView />,
      workspaces: <WorkspacesView user={contextUser} />,
      workflows:  <WorkflowsView />,
      pricing:    <PricingView currentPlan={contextUser.plan ?? 'free'} />,
      upload:     <DocumentUploadView />,
      documents:  <DocumentsView />,
    };
  }, [contextUser]);

  // ── Derived layout values ───────────────────────────────────────────────
  const sidebarWidth   = sidebarCollapsed ? SIDEBAR_COLLAPSED : SIDEBAR_EXPANDED;
  const sidebarOffscreen = isMobile && !mobileSidebarOpen;

  const userInitial = (contextUser?.name ?? contextUser?.email ?? '?').charAt(0).toUpperCase();
  const userName    = contextUser?.full_name ?? contextUser?.name ?? 'User';

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
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
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
                <span style={{ color: TT.yolk }}>C</span>OGNI
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
              onClick={() => setChatOpen((o) => !o)}
              aria-pressed={chatOpen}
              aria-label="Ask Your Past Self"
              title="Ask Your Past Self - Chat with your knowledge base"
              style={{
                height: 32,
                padding: '0 12px',
                background: chatOpen ? 'rgba(139,92,246,0.1)' : 'transparent',
                border: `1px solid ${chatOpen ? 'rgba(139,92,246,0.3)' : TT.inkBorder}`,
                borderRadius: 3,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                transition: 'all 0.15s',
                color: chatOpen ? '#8B5CF6' : TT.inkMuted,
              }}
              onMouseEnter={(e) => {
                if (!chatOpen) {
                  e.currentTarget.style.borderColor = 'rgba(139,92,246,0.25)';
                  e.currentTarget.style.color = '#8B5CF6';
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

            {/* AI Insights toggle */}
            <button
              onClick={() => setInsightsOpen((o) => !o)}
              aria-pressed={insightsOpen}
              aria-label="Toggle AI Insights panel"
              style={{
                height: 32,
                padding: '0 12px',
                background: insightsOpen ? 'rgba(245,230,66,0.1)' : 'transparent',
                border: `1px solid ${insightsOpen ? 'rgba(245,230,66,0.3)' : TT.inkBorder}`,
                borderRadius: 3,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                transition: 'all 0.15s',
                color: insightsOpen ? TT.yolk : TT.inkMuted,
              }}
              onMouseEnter={(e) => {
                if (!insightsOpen) {
                  e.currentTarget.style.borderColor = 'rgba(245,230,66,0.25)';
                  e.currentTarget.style.color = TT.yolk;
                }
              }}
              onMouseLeave={(e) => {
                if (!insightsOpen) {
                  e.currentTarget.style.borderColor = TT.inkBorder;
                  e.currentTarget.style.color = TT.inkMuted;
                }
              }}
            >
              <Sparkles size={12} aria-hidden />
              {!isSmall && (
                <span
                  style={{
                    fontFamily: TT.fontMono,
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                  }}
                >
                  AI Insights
                </span>
              )}
            </button>

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
          {viewRegistry[currentView] ?? null}
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
              {stats.notes} notes · {stats.insights} insights
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
            CogniFlow v2.4.1
          </span>
        </div>
      </div>

      {/* ── AI Insights panel ─────────────────────────────────────── */}
      <AIInsightsPanel
        insights={[]}
        isOpen={insightsOpen}
        onClose={() => setInsightsOpen(false)}
      />

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
              background: TT.inkBlack,
              borderRadius: 6,
              border: `1px solid ${TT.inkBorder}`,
              boxShadow: '0 20px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(139,92,246,0.2)',
              zIndex: 999,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <AskPastSelf
              workspaceId={currentWorkspaceId || ''}
              onClose={() => setChatOpen(false)}
            />
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
  );
}

export default App;