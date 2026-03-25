import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  LayoutDashboard,
  FileText,
  Share2,
  Search,
  Users,
  Zap,
  CreditCard,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Settings,
  LogOut,
  Plus,
  Upload,
} from 'lucide-react';
import type { User } from '@/types';

interface SidebarProps {
  currentView: string;
  onViewChange: (view: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  user?: User | null;
}

const navigation = [
  { id: 'dashboard',   label: 'Dashboard',        icon: LayoutDashboard },
  { id: 'notes',       label: 'Notes',             icon: FileText        },
  { id: 'documents',   label: 'Documents',         icon: FileText        },
  { id: 'upload',      label: 'Upload Documents',  icon: Upload          },
  { id: 'graph',       label: 'Knowledge Graph',   icon: Share2          },
  { id: 'search',      label: 'AI Search',         icon: Search, ai: true },
  { id: 'workspaces',  label: 'Workspaces',        icon: Users           },
  { id: 'workflows',   label: 'Workflows',         icon: Zap             },
  { id: 'pricing',     label: 'Pricing',           icon: CreditCard      },
];

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
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

export function Sidebar({
  currentView,
  onViewChange,
  collapsed,
  onToggleCollapse,
  user,
}: SidebarProps) {
  const initial = user?.name?.charAt(0) ?? user?.email?.charAt(0) ?? '?';

  return (
    <div
      style={{
        width: collapsed ? 64 : 240,
        transition: 'width 0.25s cubic-bezier(0.22,1,0.36,1)',
        background: TT.inkDeep,
        borderRight: `1px solid ${TT.inkBorder}`,
        // Signature left stripe
        borderLeft: `3px solid ${TT.yolk}`,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        fontFamily: TT.fontMono,
        flexShrink: 0,
      }}
    >
      {/* ── Logo ───────────────────────────────────────────────── */}
      <div
        style={{
          height: 56,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          padding: collapsed ? '0 12px' : '0 16px',
          borderBottom: `1px solid ${TT.inkBorder}`,
          flexShrink: 0,
        }}
      >
        {/* Logo mark */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 30, height: 30,
              borderRadius: 3,
              background: TT.yolk,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
              boxShadow: '0 0 0 1px rgba(245,230,66,0.3), 0 4px 14px rgba(245,230,66,0.15)',
            }}
          >
            <Sparkles size={14} color={TT.inkBlack} />
          </div>
          {!collapsed && (
            <span
              style={{
                fontFamily: TT.fontDisplay,
                fontSize: 22,
                letterSpacing: '0.06em',
                color: TT.snow,
                lineHeight: 1,
              }}
            >
              <span style={{ color: TT.yolk }}>C</span>OGNI
            </span>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={onToggleCollapse}
          className="hidden lg:flex"
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: TT.inkMuted, padding: 4, borderRadius: 2,
            transition: 'color 0.15s',
            marginLeft: collapsed ? 0 : 4,
          }}
          onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = TT.yolk)}
          onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = TT.inkMuted)}
        >
          {collapsed
            ? <ChevronRight size={14} />
            : <ChevronLeft size={14} />}
        </button>
      </div>

      {/* ── New Note CTA ────────────────────────────────────────── */}
      <div style={{ padding: collapsed ? '12px 10px' : '12px 12px', flexShrink: 0 }}>
        <button
          onClick={() => onViewChange('notes')}
          style={{
            width: '100%',
            height: 36,
            background: TT.yolk,
            border: `2px solid ${TT.yolk}`,
            borderRadius: 3,
            color: TT.inkBlack,
            fontFamily: TT.fontDisplay,
            fontSize: 14,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            cursor: 'pointer',
            display: 'flex', alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'center',
            gap: 6,
            transition: 'background 0.15s, border-color 0.15s',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.background = TT.yolkBright;
            (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright;
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.background = TT.yolk;
            (e.currentTarget as HTMLElement).style.borderColor = TT.yolk;
          }}
        >
          <Plus size={13} />
          {!collapsed && 'New Note'}
        </button>
      </div>

      {/* ── Navigation ──────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 8px' }}>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {navigation.map(({ id, label, icon: Icon, ai }) => {
            const active = currentView === id;
            return (
              <button
                key={id}
                onClick={() => onViewChange(id)}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: collapsed ? '9px 0' : '9px 10px',
                  justifyContent: collapsed ? 'center' : 'flex-start',
                  background: active ? 'rgba(245,230,66,0.07)' : 'transparent',
                  border: active ? `1px solid rgba(245,230,66,0.2)` : '1px solid transparent',
                  borderLeft: active ? `3px solid ${TT.yolk}` : `3px solid transparent`,
                  borderRadius: 3,
                  cursor: 'pointer',
                  transition: 'all 0.15s',
                  color: active ? TT.yolk : TT.inkMuted,
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    (e.currentTarget as HTMLElement).style.color = TT.snow;
                    (e.currentTarget as HTMLElement).style.background = `rgba(245,230,66,0.04)`;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
                    (e.currentTarget as HTMLElement).style.background = 'transparent';
                  }
                }}
              >
                <Icon size={14} style={{ flexShrink: 0 }} />
                {!collapsed && (
                  <>
                    <span
                      style={{
                        fontFamily: TT.fontMono,
                        fontSize: 10.5,
                        letterSpacing: '0.07em',
                        textTransform: 'uppercase',
                        flex: 1,
                        textAlign: 'left',
                      }}
                    >
                      {label}
                    </span>
                    {ai && (
                      <span
                        style={{
                          fontSize: 8,
                          letterSpacing: '0.08em',
                          textTransform: 'uppercase',
                          color: TT.yolk,
                          background: 'rgba(245,230,66,0.1)',
                          border: `1px solid rgba(245,230,66,0.2)`,
                          borderRadius: 2,
                          padding: '1px 4px',
                          fontFamily: TT.fontMono,
                        }}
                      >
                        AI
                      </span>
                    )}
                  </>
                )}
              </button>
            );
          })}
        </nav>

        {/* ── Pro badge ─────────────────────────────────────────── */}
        {!collapsed && user?.plan === 'pro' && (
          <div
            style={{
              marginTop: 20,
              padding: '14px',
              background: 'rgba(245,230,66,0.04)',
              border: `1px solid rgba(245,230,66,0.15)`,
              borderLeft: `3px solid ${TT.yolk}`,
              borderRadius: 3,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <div
                style={{
                  width: 4, height: 4, borderRadius: '50%',
                  background: TT.yolk,
                  boxShadow: '0 0 6px rgba(245,230,66,0.8)',
                }}
              />
              <span
                style={{
                  fontFamily: TT.fontDisplay,
                  fontSize: 14,
                  letterSpacing: '0.08em',
                  color: TT.snow,
                }}
              >
                PRO PLAN
              </span>
            </div>
            <p
              style={{
                fontFamily: TT.fontMono,
                fontSize: 10,
                color: TT.inkMuted,
                lineHeight: 1.6,
                letterSpacing: '0.02em',
                marginBottom: 10,
              }}
            >
              Unlimited notes &amp; all AI features active
            </p>
            <button
              onClick={() => onViewChange('pricing')}
              style={{
                width: '100%',
                height: 28,
                background: 'transparent',
                border: `1px solid ${TT.inkBorder}`,
                borderRadius: 3,
                color: TT.inkMuted,
                fontFamily: TT.fontMono,
                fontSize: 9,
                letterSpacing: '0.08em',
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
              Upgrade to Team →
            </button>
          </div>
        )}
      </div>

      {/* ── User Section ────────────────────────────────────────── */}
      <div
        style={{
          padding: collapsed ? '12px 0' : '12px 12px',
          borderTop: `1px solid ${TT.inkBorder}`,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          justifyContent: collapsed ? 'center' : 'flex-start',
          flexShrink: 0,
        }}
      >
        {/* Avatar */}
        <div
          style={{
            width: 30, height: 30,
            borderRadius: 3,
            background: TT.yolk,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: TT.fontDisplay,
            fontSize: 16,
            color: TT.inkBlack,
            flexShrink: 0,
            letterSpacing: '0.04em',
          }}
        >
          {initial.toUpperCase()}
        </div>

        {!collapsed && (
          <>
            <div style={{ flex: 1, minWidth: 0 }}>
              <p
                style={{
                  fontFamily: TT.fontMono,
                  fontSize: 11,
                  color: TT.snow,
                  letterSpacing: '0.03em',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {user?.name}
              </p>
              <p
                style={{
                  fontFamily: TT.fontMono,
                  fontSize: 9.5,
                  color: TT.inkMuted,
                  letterSpacing: '0.02em',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {user?.email}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 2 }}>
              {[
                { Icon: Settings, title: 'Settings' },
                { Icon: LogOut,   title: 'Log out'  },
              ].map(({ Icon, title }) => (
                <button
                  key={title}
                  title={title}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: TT.inkMuted, padding: 4, borderRadius: 2,
                    transition: 'color 0.15s',
                  }}
                  onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = TT.yolk)}
                  onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = TT.inkMuted)}
                >
                  <Icon size={13} />
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}