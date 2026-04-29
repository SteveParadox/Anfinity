import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  Bell,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  GitBranch,
  Lock,
  Paintbrush,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Users,
  Zap,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useProductSettings } from '../hooks/useProductSettings';
import { useTheme } from '../hooks/useTheme';
import type { ProductUserSettings, ProductWorkspaceSettings } from '../lib/api';
import type { User } from '../types';

type SectionId =
  | 'account'
  | 'ai-search'
  | 'notes'
  | 'collaboration'
  | 'notifications'
  | 'integrations'
  | 'automations'
  | 'approvals'
  | 'appearance';

interface SettingsViewProps {
  user: User;
}

const TT = {
  inkBlack: 'var(--theme-text-inverse)',
  inkDeep: 'var(--theme-panel)',
  inkRaised: 'var(--theme-panel-raised)',
  inkBorder: 'var(--theme-border)',
  inkMid: 'var(--theme-border-strong)',
  inkMuted: 'var(--theme-text-muted)',
  inkSubtle: 'var(--theme-text-subtle)',
  snow: 'var(--theme-text)',
  yolk: 'var(--theme-accent)',
  accentSoft: 'var(--theme-accent-soft)',
  accentBorder: 'var(--theme-accent-border)',
  shadow: 'var(--theme-shadow)',
  ok: 'var(--theme-success)',
  warn: 'var(--theme-warning)',
  danger: 'var(--theme-error)',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

const sections: Array<{
  id: SectionId;
  label: string;
  icon: typeof Settings2;
  keywords: string;
}> = [
  { id: 'account', label: 'Account', icon: ShieldCheck, keywords: 'profile account user plan' },
  { id: 'ai-search', label: 'AI & Search', icon: Bot, keywords: 'ask past self rag source cards similarity semantic search' },
  { id: 'notes', label: 'Notes', icon: Sparkles, keywords: 'capture auto tagging summary suggestions decay notes' },
  { id: 'collaboration', label: 'Collaboration', icon: Users, keywords: 'presence cursors invites comments mentions' },
  { id: 'notifications', label: 'Notifications', icon: Bell, keywords: 'comments mentions replies approvals digest in app' },
  { id: 'integrations', label: 'Integrations', icon: GitBranch, keywords: 'sync gmail calendar notion slack integrations' },
  { id: 'automations', label: 'Automations', icon: Zap, keywords: 'workflow automations webhooks failure' },
  { id: 'approvals', label: 'Approvals', icon: Check, keywords: 'review due date priority approvals workflow' },
  { id: 'appearance', label: 'Appearance', icon: Paintbrush, keywords: 'theme light dark system density compact display' },
];

function clone<T>(value: T | null | undefined): T | null {
  return value ? JSON.parse(JSON.stringify(value)) : null;
}

function isEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function withoutManagedTheme(settings: ProductUserSettings): ProductUserSettings {
  return {
    ...settings,
    appearance: {
      ...settings.appearance,
      theme: 'system',
    },
  };
}

function containerStyle(): CSSProperties {
  return {
    minHeight: '100%',
    padding: '28px',
    color: TT.snow,
    background: 'var(--theme-canvas-glow)',
    fontFamily: TT.fontMono,
  };
}

function panelStyle(): CSSProperties {
  return {
    border: `1px solid ${TT.inkBorder}`,
    background: TT.inkDeep,
    borderRadius: 6,
    boxShadow: TT.shadow,
  };
}

function FieldGroup({
  title,
  description,
  children,
  disabled,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <section
      style={{
        ...panelStyle(),
        padding: 18,
        opacity: disabled ? 0.62 : 1,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 18, alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0, maxWidth: 520 }}>
          <h3 style={{ margin: 0, fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.08em' }}>
            {title}
          </h3>
          {description && (
            <p style={{ margin: '5px 0 0', color: TT.inkSubtle, fontSize: 11, lineHeight: 1.6 }}>
              {description}
            </p>
          )}
        </div>
        <div style={{ display: 'grid', gap: 12, minWidth: 260, width: 'min(360px, 100%)' }}>
          {children}
        </div>
      </div>
    </section>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        minHeight: 34,
        color: disabled ? TT.inkMuted : TT.snow,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      <span style={{ fontSize: 11, letterSpacing: '0.04em', lineHeight: 1.4 }}>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        style={{
          width: 42,
          height: 22,
          borderRadius: 999,
          border: `1px solid ${checked ? TT.yolk : TT.inkMid}`,
          background: checked ? TT.accentSoft : TT.inkRaised,
          padding: 2,
          cursor: disabled ? 'not-allowed' : 'pointer',
          flexShrink: 0,
        }}
      >
        <span
          aria-hidden
          style={{
            display: 'block',
            width: 16,
            height: 16,
            borderRadius: '50%',
            background: checked ? TT.yolk : TT.inkSubtle,
            transform: checked ? 'translateX(18px)' : 'translateX(0)',
            transition: 'transform 0.16s ease',
          }}
        />
      </button>
    </label>
  );
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ display: 'flex', border: `1px solid ${TT.inkBorder}`, borderRadius: 4, overflow: 'hidden' }}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            disabled={disabled}
            style={{
              flex: 1,
              minHeight: 32,
              background: active ? TT.yolk : TT.inkRaised,
              border: 0,
              borderRight: option === options[options.length - 1] ? 0 : `1px solid ${TT.inkBorder}`,
              color: active ? TT.inkBlack : TT.inkSubtle,
              fontFamily: TT.fontMono,
              fontSize: 10,
              letterSpacing: '0.07em',
              textTransform: 'uppercase',
              cursor: disabled ? 'not-allowed' : 'pointer',
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function RangeControl({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <label style={{ display: 'grid', gap: 8, color: disabled ? TT.inkMuted : TT.snow }}>
      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11 }}>
        {label}
        <strong style={{ color: TT.yolk, fontWeight: 500 }}>{format(value)}</strong>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function Banner({ tone, children }: { tone: 'success' | 'warning' | 'error'; children: React.ReactNode }) {
  const color = tone === 'success' ? TT.ok : tone === 'warning' ? TT.warn : TT.danger;
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        border: `1px solid ${color}55`,
        color,
        background: `${color}12`,
        padding: '10px 12px',
        borderRadius: 4,
        fontSize: 11,
      }}
    >
      <CircleAlert size={13} />
      {children}
    </div>
  );
}

export function SettingsView({ user }: SettingsViewProps) {
  const { currentWorkspaceId } = useAuth();
  const { choice, mode, setTheme } = useTheme();
  const {
    user: userSettingsResponse,
    workspace: workspaceSettingsResponse,
    loading,
    saving,
    error,
    savedAt,
    updateUserSettings,
    updateWorkspaceSettings,
    resetUserSettings,
  } = useProductSettings(currentWorkspaceId);

  const [activeSection, setActiveSection] = useState<SectionId>('ai-search');
  const [query, setQuery] = useState('');
  const [draftUser, setDraftUser] = useState<ProductUserSettings | null>(null);
  const [draftWorkspace, setDraftWorkspace] = useState<ProductWorkspaceSettings | null>(null);
  const [resetConfirmation, setResetConfirmation] = useState<{ scope: 'user' | 'workspace'; section: SectionId } | null>(null);

  useEffect(() => {
    setDraftUser(clone(userSettingsResponse?.settings));
  }, [userSettingsResponse?.settings]);

  useEffect(() => {
    setDraftWorkspace(clone(workspaceSettingsResponse?.settings));
  }, [workspaceSettingsResponse?.settings]);

  useEffect(() => {
    setDraftUser((current) =>
      current
        ? {
            ...current,
            appearance: {
              ...current.appearance,
              theme: choice,
            },
          }
        : current,
    );
  }, [choice]);

  const filteredSections = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return sections;
    return sections.filter((section) => `${section.label} ${section.keywords}`.toLowerCase().includes(normalized));
  }, [query]);

  const userDirty = Boolean(
    draftUser
    && userSettingsResponse
    && !isEqual(withoutManagedTheme(draftUser), withoutManagedTheme(userSettingsResponse.settings)),
  );
  const workspaceDirty = Boolean(draftWorkspace && workspaceSettingsResponse && !isEqual(draftWorkspace, workspaceSettingsResponse.settings));
  const hasChanges = userDirty || workspaceDirty;
  const canUpdateWorkspace = Boolean(workspaceSettingsResponse?.can_update);
  const workspaceDisabled = !currentWorkspaceId || !workspaceSettingsResponse || !canUpdateWorkspace;

  const setUserValue = <S extends keyof ProductUserSettings, K extends keyof ProductUserSettings[S]>(
    section: S,
    key: K,
    value: ProductUserSettings[S][K],
  ) => {
    setDraftUser((current) => current ? ({
      ...current,
      [section]: { ...current[section], [key]: value },
    }) : current);
  };

  const setWorkspaceValue = <
    S extends keyof ProductWorkspaceSettings,
    K extends keyof ProductWorkspaceSettings[S]
  >(
    section: S,
    key: K,
    value: ProductWorkspaceSettings[S][K],
  ) => {
    setDraftWorkspace((current) => current ? ({
      ...current,
      [section]: { ...current[section], [key]: value },
    }) : current);
  };

  async function saveChanges() {
    if (!draftUser || !draftWorkspace) return;
    if (userDirty) {
      await updateUserSettings(draftUser);
    }
    if (workspaceDirty && currentWorkspaceId && canUpdateWorkspace) {
      await updateWorkspaceSettings(draftWorkspace);
    }
  }

  async function resetVisibleScope() {
    if (!userSettingsResponse) return;
    if (activeSection === 'account') {
      await resetUserSettings();
      return;
    }
    if (activeSection === 'notifications') {
      await updateUserSettings({ notifications: userSettingsResponse.defaults.notifications });
      return;
    }
    if (activeSection === 'appearance') {
      await updateUserSettings({
        appearance: userSettingsResponse.defaults.appearance,
        onboarding: userSettingsResponse.defaults.onboarding,
      });
      return;
    }
    if (activeSection === 'ai-search') {
      await updateUserSettings({ ai_search: userSettingsResponse.defaults.ai_search });
      if (workspaceSettingsResponse?.defaults.ai_search && canUpdateWorkspace) {
        await updateWorkspaceSettings({ ai_search: workspaceSettingsResponse.defaults.ai_search });
      }
      return;
    }
    if (activeSection === 'collaboration') {
      await updateUserSettings({ collaboration: userSettingsResponse.defaults.collaboration });
      if (workspaceSettingsResponse?.defaults.collaboration && canUpdateWorkspace) {
        await updateWorkspaceSettings({ collaboration: workspaceSettingsResponse.defaults.collaboration });
      }
      return;
    }
    if (activeSection === 'notes' && workspaceSettingsResponse?.defaults.notes && canUpdateWorkspace) {
      await updateWorkspaceSettings({ notes: workspaceSettingsResponse.defaults.notes });
      return;
    }
    if (activeSection === 'integrations' && workspaceSettingsResponse?.defaults.integrations && canUpdateWorkspace) {
      await updateWorkspaceSettings({ integrations: workspaceSettingsResponse.defaults.integrations });
      return;
    }
    if (activeSection === 'automations' && workspaceSettingsResponse?.defaults.automations && canUpdateWorkspace) {
      await updateWorkspaceSettings({ automations: workspaceSettingsResponse.defaults.automations });
      return;
    }
    if (activeSection === 'approvals' && workspaceSettingsResponse?.defaults.approvals && canUpdateWorkspace) {
      await updateWorkspaceSettings({ approvals: workspaceSettingsResponse.defaults.approvals });
    }
  }

  const savedMessage = savedAt ? `Saved ${new Date(savedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : null;

  if (loading || !draftUser) {
    return (
      <div style={containerStyle()}>
        <div style={{ ...panelStyle(), padding: 24, color: TT.inkSubtle, fontSize: 11 }}>Loading settings...</div>
      </div>
    );
  }

  return (
    <div style={containerStyle()}>
      <header style={{ display: 'flex', justifyContent: 'space-between', gap: 18, alignItems: 'flex-end', marginBottom: 22 }}>
        <div>
          <p style={{ margin: '0 0 6px', color: TT.yolk, fontSize: 10, letterSpacing: '0.13em', textTransform: 'uppercase' }}>
            Product settings
          </p>
          <h1 style={{ margin: 0, fontFamily: TT.fontDisplay, fontSize: 46, letterSpacing: '0.06em' }}>
            Control Center
          </h1>
          <p style={{ margin: '6px 0 0', color: TT.inkSubtle, fontSize: 12 }}>
            Tune notes, AI retrieval, collaboration, notifications, automations, and workspace behavior.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {savedMessage && !hasChanges && <span style={{ color: TT.ok, fontSize: 11 }}>{savedMessage}</span>}
          {hasChanges && <span style={{ color: TT.warn, fontSize: 11 }}>Unsaved changes</span>}
          <button type="button" onClick={resetVisibleScope} disabled={saving} style={actionButtonStyle('ghost')}>
            <RotateCcw size={13} /> Reset defaults
          </button>
          <button type="button" onClick={saveChanges} disabled={!hasChanges || saving} style={actionButtonStyle('primary')}>
            {saving ? <RefreshCw size={13} /> : <Save size={13} />}
            {saving ? 'Saving' : 'Save'}
          </button>
        </div>
      </header>

      {error && <div style={{ marginBottom: 14 }}><Banner tone="error">{error}</Banner></div>}
      {!canUpdateWorkspace && currentWorkspaceId && (
        <div style={{ marginBottom: 14 }}>
          <Banner tone="warning">Workspace controls are read-only for your role. Personal preferences remain editable.</Banner>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '260px minmax(0, 1fr)', gap: 18, alignItems: 'start' }}>
        <aside style={{ ...panelStyle(), padding: 12, position: 'sticky', top: 76 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, border: `1px solid ${TT.inkBorder}`, borderRadius: 4, padding: '8px 10px', marginBottom: 12 }}>
            <Search size={13} color={TT.inkSubtle} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search settings"
              style={{ flex: 1, background: 'transparent', border: 0, outline: 0, color: TT.snow, fontFamily: TT.fontMono, fontSize: 11 }}
            />
          </label>
          <nav style={{ display: 'grid', gap: 4 }}>
            {filteredSections.map(({ id, label, icon: Icon }) => {
              const active = activeSection === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setActiveSection(id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 9,
                    minHeight: 38,
                    padding: '0 10px',
                    background: active ? TT.accentSoft : 'transparent',
                    border: `1px solid ${active ? TT.accentBorder : 'transparent'}`,
                    borderLeft: `3px solid ${active ? TT.yolk : 'transparent'}`,
                    borderRadius: 4,
                    color: active ? TT.yolk : TT.inkSubtle,
                    cursor: 'pointer',
                    fontFamily: TT.fontMono,
                    fontSize: 10.5,
                    letterSpacing: '0.07em',
                    textTransform: 'uppercase',
                  }}
                >
                  <Icon size={14} />
                  <span style={{ flex: 1, textAlign: 'left' }}>{label}</span>
                  {active && <ChevronRight size={13} />}
                </button>
              );
            })}
          </nav>
        </aside>

        <main style={{ display: 'grid', gap: 14 }}>
          {activeSection === 'account' && (
            <FieldGroup title="Account" description="Your app identity and current plan. Workspace roles still control shared settings.">
              <InfoRow label="Name" value={user.full_name || user.name || 'Unnamed user'} />
              <InfoRow label="Email" value={user.email} />
              <InfoRow label="Plan" value={user.plan || 'free'} />
            </FieldGroup>
          )}

          {activeSection === 'ai-search' && draftWorkspace && (
            <>
              <FieldGroup title="Ask Your Past Self" description="Strict note-grounded answers stay server enforced. These controls shape retrieval and source display.">
                <Toggle
                  label="Enable Ask Your Past Self"
                  checked={draftWorkspace.ai_search.ask_past_self_enabled}
                  onChange={(value) => setWorkspaceValue('ai_search', 'ask_past_self_enabled', value)}
                  disabled={workspaceDisabled}
                />
                <Toggle
                  label="Show source cards in my chat"
                  checked={draftUser.ai_search.show_source_cards}
                  onChange={(value) => setUserValue('ai_search', 'show_source_cards', value)}
                />
                <Toggle
                  label="Workspace source cards default"
                  checked={draftWorkspace.ai_search.source_cards_default}
                  onChange={(value) => setWorkspaceValue('ai_search', 'source_cards_default', value)}
                  disabled={workspaceDisabled}
                />
                <Toggle
                  label="Show similarity evidence"
                  checked={draftUser.ai_search.show_similarity_scores}
                  onChange={(value) => setUserValue('ai_search', 'show_similarity_scores', value)}
                />
                <RangeControl
                  label="Minimum note evidence"
                  value={draftWorkspace.ai_search.min_note_similarity}
                  min={0.38}
                  max={0.85}
                  step={0.01}
                  format={(value) => `${Math.round(value * 100)}%`}
                  onChange={(value) => setWorkspaceValue('ai_search', 'min_note_similarity', value)}
                  disabled={workspaceDisabled}
                />
                <RangeControl
                  label="Default retrieved notes"
                  value={draftUser.ai_search.default_top_k}
                  min={3}
                  max={12}
                  step={1}
                  format={(value) => `${value}`}
                  onChange={(value) => setUserValue('ai_search', 'default_top_k', value)}
                />
              </FieldGroup>
              <FieldGroup title="Search Experience" description="Personal search presentation without changing workspace data.">
                <Toggle
                  label="Smart highlights in search results"
                  checked={draftUser.ai_search.smart_highlights}
                  onChange={(value) => setUserValue('ai_search', 'smart_highlights', value)}
                />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: TT.inkSubtle, fontSize: 10 }}>
                  <Lock size={12} /> Strict note-only answer mode is locked on by product policy.
                </div>
              </FieldGroup>
            </>
          )}

          {activeSection === 'notes' && draftWorkspace && (
            <FieldGroup title="Note Enrichment" description="These controls feed the centralized note capture pipeline after createNote().">
              <Segmented
                value={draftWorkspace.notes.default_visibility}
                options={[{ value: 'workspace', label: 'Workspace' }, { value: 'private', label: 'Private' }]}
                onChange={(value) => setWorkspaceValue('notes', 'default_visibility', value)}
                disabled={workspaceDisabled}
              />
              <Toggle label="Auto-tag new notes" checked={draftWorkspace.notes.auto_tagging_enabled} onChange={(value) => setWorkspaceValue('notes', 'auto_tagging_enabled', value)} disabled={workspaceDisabled} />
              <Toggle label="Generate summaries" checked={draftWorkspace.notes.summary_generation_enabled} onChange={(value) => setWorkspaceValue('notes', 'summary_generation_enabled', value)} disabled={workspaceDisabled} />
              <Toggle label="Suggest note connections" checked={draftWorkspace.notes.connection_suggestions_enabled} onChange={(value) => setWorkspaceValue('notes', 'connection_suggestions_enabled', value)} disabled={workspaceDisabled} />
              <Toggle label="Classify note decay/reminders" checked={draftWorkspace.notes.decay_classification_enabled} onChange={(value) => setWorkspaceValue('notes', 'decay_classification_enabled', value)} disabled={workspaceDisabled} />
            </FieldGroup>
          )}

          {activeSection === 'collaboration' && draftWorkspace && (
            <>
              <FieldGroup title="Presence & Invites" description="Personal presence preferences plus workspace invite policy.">
                <Toggle label="Show my realtime presence" checked={draftUser.collaboration.presence_visible} onChange={(value) => setUserValue('collaboration', 'presence_visible', value)} />
                <Toggle label="Show collaborator cursors" checked={draftUser.collaboration.show_collaborator_cursors} onChange={(value) => setUserValue('collaboration', 'show_collaborator_cursors', value)} />
                <Toggle label="Allow note invites" checked={draftUser.collaboration.allow_note_invites} onChange={(value) => setUserValue('collaboration', 'allow_note_invites', value)} />
                <Segmented
                  value={draftWorkspace.collaboration.invite_policy}
                  options={[{ value: 'members', label: 'Members' }, { value: 'owners_admins', label: 'Admins' }]}
                  onChange={(value) => setWorkspaceValue('collaboration', 'invite_policy', value)}
                  disabled={workspaceDisabled}
                />
              </FieldGroup>
              <FieldGroup title="Commenting" description="Workspace comment and mention features. Notification delivery is controlled separately.">
                <Toggle label="Enable comment threads" checked={draftWorkspace.collaboration.comment_threads_enabled} onChange={(value) => setWorkspaceValue('collaboration', 'comment_threads_enabled', value)} disabled={workspaceDisabled} />
                <Toggle label="Enable @mentions" checked={draftWorkspace.collaboration.mentions_enabled} onChange={(value) => setWorkspaceValue('collaboration', 'mentions_enabled', value)} disabled={workspaceDisabled} />
              </FieldGroup>
            </>
          )}

          {activeSection === 'notifications' && (
            <FieldGroup title="In-App Notifications" description="These preferences filter comment, mention, reply, and approval notifications before delivery.">
              <Toggle label="New comments on my notes" checked={draftUser.notifications.in_app_comments} onChange={(value) => setUserValue('notifications', 'in_app_comments', value)} />
              <Toggle label="@mentions" checked={draftUser.notifications.in_app_mentions} onChange={(value) => setUserValue('notifications', 'in_app_mentions', value)} />
              <Toggle label="Replies to my comments" checked={draftUser.notifications.in_app_replies} onChange={(value) => setUserValue('notifications', 'in_app_replies', value)} />
              <Toggle label="Approval workflow updates" checked={draftUser.notifications.in_app_approvals} onChange={(value) => setUserValue('notifications', 'in_app_approvals', value)} />
              <Segmented
                value={draftUser.notifications.digest_frequency}
                options={[{ value: 'off', label: 'Off' }, { value: 'daily', label: 'Daily' }, { value: 'weekly', label: 'Weekly' }]}
                onChange={(value) => setUserValue('notifications', 'digest_frequency', value)}
              />
            </FieldGroup>
          )}

          {activeSection === 'integrations' && draftWorkspace && (
            <FieldGroup title="Integration Sync" description="Controls sync behavior for connected providers without exposing tokens or secrets.">
              <Toggle label="Automatic integration sync" checked={draftWorkspace.integrations.auto_sync_enabled} onChange={(value) => setWorkspaceValue('integrations', 'auto_sync_enabled', value)} disabled={workspaceDisabled} />
              <Segmented
                value={draftWorkspace.integrations.sync_frequency}
                options={[{ value: 'manual', label: 'Manual' }, { value: 'hourly', label: 'Hourly' }, { value: 'daily', label: 'Daily' }]}
                onChange={(value) => setWorkspaceValue('integrations', 'sync_frequency', value)}
                disabled={workspaceDisabled}
              />
            </FieldGroup>
          )}

          {activeSection === 'automations' && draftWorkspace && (
            <FieldGroup title="Automation Runtime" description="Workspace-wide switch for workflow execution and failure reporting.">
              <Toggle label="Run enabled automations" checked={draftWorkspace.automations.enabled} onChange={(value) => setWorkspaceValue('automations', 'enabled', value)} disabled={workspaceDisabled} />
              <Toggle label="Notify on automation failures" checked={draftWorkspace.automations.notify_on_failure} onChange={(value) => setWorkspaceValue('automations', 'notify_on_failure', value)} disabled={workspaceDisabled} />
            </FieldGroup>
          )}

          {activeSection === 'approvals' && draftWorkspace && (
            <FieldGroup title="Approval Defaults" description="Defaults applied to note review workflows and their notification behavior.">
              <Toggle label="Approval workflows enabled" checked={draftWorkspace.approvals.enabled} onChange={(value) => setWorkspaceValue('approvals', 'enabled', value)} disabled={workspaceDisabled} />
              <Segmented
                value={draftWorkspace.approvals.default_priority}
                options={[{ value: 'low', label: 'Low' }, { value: 'normal', label: 'Normal' }, { value: 'high', label: 'High' }, { value: 'critical', label: 'Critical' }]}
                onChange={(value) => setWorkspaceValue('approvals', 'default_priority', value)}
                disabled={workspaceDisabled}
              />
              <RangeControl
                label="Default due window"
                value={draftWorkspace.approvals.default_due_days}
                min={1}
                max={30}
                step={1}
                format={(value) => `${value} days`}
                onChange={(value) => setWorkspaceValue('approvals', 'default_due_days', value)}
                disabled={workspaceDisabled}
              />
            </FieldGroup>
          )}

          {activeSection === 'appearance' && (
            <FieldGroup title="Appearance" description="Personal display preferences for the application shell.">
              <Segmented
                value={choice}
                options={[{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }, { value: 'system', label: 'System' }]}
                onChange={(value) => {
                  setTheme(value);
                  setUserValue('appearance', 'theme', value);
                }}
              />
              <div style={{ color: TT.inkSubtle, fontSize: 10, lineHeight: 1.5 }}>
                Active palette: {mode === 'light' ? 'Warm parchment light mode' : 'Cinematic dark mode'}.
              </div>
              <Segmented
                value={draftUser.appearance.density}
                options={[{ value: 'comfortable', label: 'Comfort' }, { value: 'compact', label: 'Compact' }]}
                onChange={(value) => setUserValue('appearance', 'density', value)}
              />
              <Toggle label="Show assistant tips during onboarding" checked={draftUser.onboarding.assistant_tips} onChange={(value) => setUserValue('onboarding', 'assistant_tips', value)} />
            </FieldGroup>
          )}
        </main>
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11, color: TT.inkSubtle }}>
      <span>{label}</span>
      <strong style={{ color: TT.snow, fontWeight: 500, textAlign: 'right' }}>{value}</strong>
    </div>
  );
}

function actionButtonStyle(variant: 'primary' | 'ghost'): CSSProperties {
  const primary = variant === 'primary';
  return {
    minHeight: 34,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 7,
    padding: '0 12px',
    borderRadius: 4,
    border: `1px solid ${primary ? TT.yolk : TT.inkBorder}`,
    background: primary ? TT.yolk : TT.inkRaised,
    color: primary ? TT.inkBlack : TT.inkSubtle,
    fontFamily: TT.fontMono,
    fontSize: 10,
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
    cursor: 'pointer',
  };
}

export default SettingsView;
