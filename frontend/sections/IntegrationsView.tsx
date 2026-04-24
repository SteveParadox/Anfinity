import { useDeferredValue, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
  Workflow,
  Zap,
} from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

type Provider = {
  provider: string;
  label: string;
  required_scopes: string[];
  capabilities: string[];
};

type Connector = {
  id: string;
  connector_type: string;
  is_active: boolean;
  sync_status: string;
  last_sync_at?: string | null;
  last_sync_error?: string | null;
  external_account_metadata?: Record<string, any>;
  config?: Record<string, any>;
};

type SyncItem = {
  id: string;
  external_type: string;
  external_id: string;
  sync_direction: string;
  sync_status: string;
  local_note_id?: string | null;
  last_seen_at?: string | null;
  last_synced_at?: string | null;
  last_error?: string | null;
};

type IntegrationFilter = 'all' | 'connected' | 'attention' | 'inactive';

const TT = {
  inkBlack: '#0A0A0A',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMid: '#3A3A3A',
  inkMuted: '#5A5A5A',
  inkSubtle: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  green: '#44D17A',
  red: '#FF4545',
  blue: '#7DD3FC',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

function providerDescription(provider: Provider): string {
  const descriptions: Record<string, string> = {
    slack: 'Post rich Block Kit updates with action buttons into workspace channels.',
    notion: 'Two-way note sync using CogniFlowID properties and Notion-safe content chunking.',
    google: 'Capture filtered Gmail messages and sync Calendar events into meeting notes.',
  };
  return descriptions[provider.provider] || provider.capabilities.join(', ');
}

function providerConfigDefaults(provider: string, config: Record<string, any> = {}) {
  if (provider === 'slack') {
    return { default_channel_id: config.default_channel_id || '' };
  }
  if (provider === 'notion') {
    return {
      database_id: config.database_id || '',
      notion_push_tag: config.notion_push_tag || 'notion-sync',
      notion_push_all: Boolean(config.notion_push_all),
    };
  }
  if (provider === 'google') {
    return {
      enabled_capabilities: config.enabled_capabilities || ['gmail_sync', 'calendar_sync'],
      gmail_query: config.gmail_query || 'is:unread',
      calendar_id: config.calendar_id || 'primary',
      calendar_days_ahead: String(config.calendar_days_ahead || 30),
      calendar_match_threshold: String(config.calendar_match_threshold || 0.82),
    };
  }
  return { ...config };
}

function normalizeConfig(provider: string, draft: Record<string, any>) {
  if (provider === 'google') {
    return {
      ...draft,
      enabled_capabilities: Array.isArray(draft.enabled_capabilities) ? draft.enabled_capabilities : ['gmail_sync', 'calendar_sync'],
      calendar_days_ahead: Number(draft.calendar_days_ahead || 30),
      calendar_match_threshold: Math.max(0.82, Number(draft.calendar_match_threshold || 0.82)),
    };
  }
  return draft;
}

function stableSerialize(value: unknown): string {
  return JSON.stringify(value, Object.keys(value as Record<string, unknown>).sort());
}

function connectorNeedsAttention(connector: Connector) {
  return ['failed', 'reauth_required'].includes(connector.sync_status) || Boolean(connector.last_sync_error);
}

function connectorStatusLabel(connector: Connector) {
  if (!connector.is_active) return 'Paused';
  return connector.sync_status || 'idle';
}

function ConnectorStatePill({ connector }: { connector: Connector }) {
  const needsAttention = connectorNeedsAttention(connector);
  const color = !connector.is_active ? TT.inkSubtle : needsAttention ? TT.red : TT.green;
  const borderColor = !connector.is_active
    ? 'rgba(136,136,136,0.35)'
    : needsAttention
      ? 'rgba(255,69,69,0.35)'
      : 'rgba(68,209,122,0.35)';

  return (
    <span style={{ ...pillStyle, color, borderColor }}>
      {connectorStatusLabel(connector)}
    </span>
  );
}

function FilterButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      style={{
        borderRadius: 999,
        border: `1px solid ${active ? 'rgba(245,230,66,0.28)' : TT.inkBorder}`,
        background: active ? 'rgba(245,230,66,0.08)' : TT.inkDeep,
        color: active ? TT.yolk : TT.inkMuted,
        padding: '6px 10px',
        fontFamily: TT.fontMono,
        fontSize: 10,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
  );
}

export function IntegrationsView() {
  const { currentWorkspaceId } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [syncItems, setSyncItems] = useState<Record<string, SyncItem[]>>({});
  const [draftConfigs, setDraftConfigs] = useState<Record<string, Record<string, any>>>({});
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filter, setFilter] = useState<IntegrationFilter>('all');
  const [selectedProvider, setSelectedProvider] = useState<string>('all');
  const [selectedConnectorId, setSelectedConnectorId] = useState<string | null>(null);
  const [runningInlineSyncId, setRunningInlineSyncId] = useState<string | null>(null);

  const deferredQuery = useDeferredValue(searchQuery.trim().toLowerCase());

  const refresh = async () => {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const [providerResponse, connectorResponse] = await Promise.all([
        api.listIntegrationProviders(),
        api.listConnectors(currentWorkspaceId),
      ]);
      const nextConnectors = connectorResponse.connectors || [];

      setProviders(providerResponse);
      setConnectors(nextConnectors);
      setDraftConfigs((current) => ({
        ...Object.fromEntries(
          nextConnectors.map((connector: Connector) => [
            connector.id,
            current[connector.id] || providerConfigDefaults(connector.connector_type, connector.config || {}),
          ])
        ),
      }));

      const itemPairs = await Promise.all(
        nextConnectors.map(async (connector: Connector) => {
          const response = await api.listConnectorSyncItems(connector.id, currentWorkspaceId, 8).catch(() => ({ items: [] }));
          return [connector.id, response.items || []] as const;
        })
      );
      setSyncItems(Object.fromEntries(itemPairs));
      setSelectedConnectorId((current) => (current && nextConnectors.some((connector: Connector) => connector.id === current) ? current : nextConnectors[0]?.id ?? null));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load integrations');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [currentWorkspaceId]);

  const connectedProviderSet = useMemo(() => new Set(connectors.map((connector) => connector.connector_type)), [connectors]);
  const connectedCount = connectors.length;
  const attentionCount = connectors.filter(connectorNeedsAttention).length;
  const pausedCount = connectors.filter((connector) => !connector.is_active).length;
  const totalSyncedItems = Object.values(syncItems).reduce((count, items) => count + items.length, 0);

  const providerOptions = useMemo(
    () => ['all', ...providers.map((provider) => provider.provider)],
    [providers]
  );

  const filteredProviders = providers.filter((provider) => {
    const matchesSelectedProvider = selectedProvider === 'all' || provider.provider === selectedProvider;
    const matchesConnectionState =
      filter === 'all'
        ? true
        : filter === 'connected'
          ? connectedProviderSet.has(provider.provider)
          : filter === 'attention'
            ? connectors.some((connector) => connector.connector_type === provider.provider && connectorNeedsAttention(connector))
            : connectors.some((connector) => connector.connector_type === provider.provider && !connector.is_active);

    const searchableText = [provider.label, provider.provider, providerDescription(provider), provider.capabilities.join(' '), provider.required_scopes.join(' ')]
      .join(' ')
      .toLowerCase();
    const matchesQuery = !deferredQuery || searchableText.includes(deferredQuery);

    return matchesSelectedProvider && matchesConnectionState && matchesQuery;
  });

  const filteredConnectors = connectors.filter((connector) => {
    const matchesSelectedProvider = selectedProvider === 'all' || connector.connector_type === selectedProvider;
    const matchesConnectionState =
      filter === 'all'
        ? true
        : filter === 'connected'
          ? connector.is_active
          : filter === 'attention'
            ? connectorNeedsAttention(connector)
            : !connector.is_active;

    const searchableText = [
      connector.connector_type,
      connector.external_account_metadata?.name,
      connector.external_account_metadata?.email,
      connector.id,
      connector.sync_status,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    const matchesQuery = !deferredQuery || searchableText.includes(deferredQuery);

    return matchesSelectedProvider && matchesConnectionState && matchesQuery;
  });

  const selectedConnector =
    connectors.find((connector) => connector.id === selectedConnectorId) ??
    filteredConnectors[0] ??
    null;

  const selectedDraft = selectedConnector
    ? draftConfigs[selectedConnector.id] || providerConfigDefaults(selectedConnector.connector_type, selectedConnector.config || {})
    : null;
  const selectedConnectorDirty = selectedConnector && selectedDraft
    ? stableSerialize(normalizeConfig(selectedConnector.connector_type, selectedDraft)) !==
      stableSerialize(normalizeConfig(selectedConnector.connector_type, providerConfigDefaults(selectedConnector.connector_type, selectedConnector.config || {})))
    : false;

  const connectProvider = async (provider: string) => {
    if (!currentWorkspaceId) return;
    setError(null);
    setMessage(null);
    try {
      const response = await api.getIntegrationOAuthUrl(provider, currentWorkspaceId);
      window.location.assign(response.authorization_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start OAuth flow');
    }
  };

  const queueSync = async (connector: Connector, runInline = false) => {
    if (!currentWorkspaceId) return;
    setMessage(null);
    setError(null);
    if (runInline) setRunningInlineSyncId(connector.id);
    try {
      const response = runInline
        ? await api.syncConnectorInline(connector.id, currentWorkspaceId)
        : await api.syncConnector(connector.id, currentWorkspaceId);
      setMessage(
        runInline
          ? `Inline sync completed for ${connector.connector_type}`
          : response.task_id
            ? `Sync queued for ${connector.connector_type}`
            : `Sync started for ${connector.connector_type}`
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sync connector');
    } finally {
      if (runInline) setRunningInlineSyncId(null);
    }
  };

  const saveConfig = async (connector: Connector) => {
    if (!currentWorkspaceId) return;
    const draft = draftConfigs[connector.id] || {};
    setMessage(null);
    setError(null);
    try {
      await api.updateConnector(connector.id, currentWorkspaceId, {
        config: normalizeConfig(connector.connector_type, draft),
      });
      setMessage(`${connector.connector_type} config saved`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save connector config');
    }
  };

  const toggleConnectorActive = async (connector: Connector) => {
    if (!currentWorkspaceId) return;
    setMessage(null);
    setError(null);
    try {
      await api.updateConnector(connector.id, currentWorkspaceId, { is_active: !connector.is_active });
      setMessage(`${connector.connector_type} ${connector.is_active ? 'paused' : 'resumed'}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update connector state');
    }
  };

  const disconnect = async (connector: Connector) => {
    if (!currentWorkspaceId) return;
    setMessage(null);
    setError(null);
    try {
      await api.deleteConnector(connector.id, currentWorkspaceId);
      setMessage(`${connector.connector_type} disconnected`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disconnect connector');
    }
  };

  const updateDraft = (connectorId: string, key: string, value: any) => {
    setDraftConfigs((current) => ({
      ...current,
      [connectorId]: {
        ...(current[connectorId] || {}),
        [key]: value,
      },
    }));
  };

  const resetDraft = (connector: Connector) => {
    setDraftConfigs((current) => ({
      ...current,
      [connector.id]: providerConfigDefaults(connector.connector_type, connector.config || {}),
    }));
    setMessage(`${connector.connector_type} draft reset`);
  };

  if (!currentWorkspaceId) {
    return (
      <section style={shellStyle}>
        <h1 style={titleStyle}>Integrations</h1>
        <p style={mutedStyle}>Select a workspace before connecting providers.</p>
      </section>
    );
  }

  return (
    <section style={shellStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <p style={eyebrowStyle}>Feature 2</p>
          <h1 style={titleStyle}>Integrations</h1>
          <p style={mutedStyle}>One shared OAuth layer powers Slack, Notion, Gmail, and Calendar syncs, with inline controls for sync, pause, config, and attention triage.</p>
        </div>
        <button type="button" onClick={refresh} disabled={loading} style={secondaryButtonStyle}>
          <RefreshCw size={13} aria-hidden className={loading ? 'animate-spin' : undefined} />
          {loading ? 'Refreshing' : 'Refresh'}
        </button>
      </div>

      {error && <StatusBanner tone="error" message={error} />}
      {message && <StatusBanner tone="success" message={message} />}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, marginBottom: 20 }}>
        {[
          { label: 'Providers', value: providers.length, helper: `${connectedProviderSet.size} connected`, icon: Workflow },
          { label: 'Connectors', value: connectedCount, helper: `${pausedCount} paused`, icon: Zap },
          { label: 'Attention', value: attentionCount, helper: 'failed or reauth required', icon: AlertCircle },
          { label: 'Synced Items', value: totalSyncedItems, helper: 'recent provider events', icon: CheckCircle2 },
        ].map(({ label, value, helper, icon: Icon }) => (
          <div key={label} style={summaryCardStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <span style={summaryLabelStyle}>{label}</span>
              <Icon size={14} color={TT.yolk} aria-hidden />
            </div>
            <div style={summaryValueStyle}>{value}</div>
            <div style={smallTextStyle}>{helper}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 18 }}>
        <div style={searchShellStyle}>
          <Search size={13} color={TT.inkMuted} aria-hidden />
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search providers, accounts, or statuses"
            aria-label="Search integrations"
            style={searchInputStyle}
          />
        </div>
        {(['all', 'connected', 'attention', 'inactive'] as IntegrationFilter[]).map((option) => (
          <FilterButton key={option} active={filter === option} onClick={() => setFilter(option)}>
            {option}
          </FilterButton>
        ))}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 20 }}>
        {providerOptions.map((provider) => (
          <FilterButton
            key={provider}
            active={selectedProvider === provider}
            onClick={() => setSelectedProvider(provider)}
          >
            {provider === 'all' ? 'All providers' : provider}
          </FilterButton>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14, marginBottom: 24 }}>
        {filteredProviders.map((provider) => {
          const connected = connectedProviderSet.has(provider.provider);
          const linkedConnector = connectors.find((connector) => connector.connector_type === provider.provider);
          return (
            <article key={provider.provider} style={cardStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <h2 style={cardTitleStyle}>{provider.label}</h2>
                  <p style={smallTextStyle}>{providerDescription(provider)}</p>
                </div>
                <span
                  style={{
                    ...pillStyle,
                    color: connected ? TT.green : TT.inkSubtle,
                    borderColor: connected ? 'rgba(68,209,122,0.35)' : TT.inkBorder,
                  }}
                >
                  {connected ? 'Connected' : 'Available'}
                </span>
              </div>
              <p style={{ ...smallTextStyle, marginTop: 14 }}>
                Capabilities: {provider.capabilities.join(', ')}
              </p>
              <p style={smallTextStyle}>
                Scopes: {provider.required_scopes.length ? provider.required_scopes.join(', ') : 'Provider-managed Notion access'}
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
                <button type="button" onClick={() => connectProvider(provider.provider)} style={primaryButtonStyle}>
                  <ExternalLink size={13} aria-hidden />
                  {connected ? 'Reconnect' : 'Connect'}
                </button>
                {linkedConnector ? (
                  <button
                    type="button"
                    onClick={() => setSelectedConnectorId(linkedConnector.id)}
                    style={secondaryButtonStyle}
                  >
                    Open details
                  </button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 360px) minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'grid', gap: 12 }}>
          <article style={cardStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 12 }}>
              <h2 style={cardTitleStyle}>Connected Services</h2>
              <span style={pillStyle}>{filteredConnectors.length} visible</span>
            </div>

            {filteredConnectors.length === 0 ? (
              <p style={smallTextStyle}>No connectors match the current filters. Clear the query or connect a provider to continue.</p>
            ) : (
              <div style={{ display: 'grid', gap: 8 }}>
                {filteredConnectors.map((connector) => {
                  const selected = connector.id === selectedConnector?.id;
                  const dirty = stableSerialize(
                    normalizeConfig(
                      connector.connector_type,
                      draftConfigs[connector.id] || providerConfigDefaults(connector.connector_type, connector.config || {})
                    )
                  ) !== stableSerialize(normalizeConfig(connector.connector_type, providerConfigDefaults(connector.connector_type, connector.config || {})));

                  return (
                    <button
                      key={connector.id}
                      type="button"
                      onClick={() => setSelectedConnectorId(connector.id)}
                      aria-pressed={selected}
                      style={{
                        ...connectorListButtonStyle,
                        borderColor: selected ? 'rgba(245,230,66,0.28)' : TT.inkBorder,
                        background: selected ? 'rgba(245,230,66,0.05)' : TT.inkRaised,
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
                        <span style={{ color: TT.snow, fontFamily: TT.fontMono, fontSize: 12 }}>
                          {connector.connector_type.toUpperCase()}
                        </span>
                        <ConnectorStatePill connector={connector} />
                      </div>
                      <div style={{ ...smallTextStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {connector.external_account_metadata?.name || connector.external_account_metadata?.email || connector.id}
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                        {dirty ? <span style={{ ...pillStyle, color: TT.blue, borderColor: 'rgba(125,211,252,0.35)' }}>Unsaved</span> : null}
                        {!connector.is_active ? <span style={{ ...pillStyle, color: TT.inkSubtle, borderColor: 'rgba(136,136,136,0.35)' }}>Paused</span> : null}
                        {connectorNeedsAttention(connector) ? <span style={{ ...pillStyle, color: TT.red, borderColor: 'rgba(255,69,69,0.35)' }}>Needs attention</span> : null}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </article>
        </div>

        <article style={cardStyle}>
          {selectedConnector ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <div>
                  <h2 style={cardTitleStyle}>{selectedConnector.connector_type.toUpperCase()}</h2>
                  <p style={smallTextStyle}>
                    Account: {selectedConnector.external_account_metadata?.name || selectedConnector.external_account_metadata?.email || selectedConnector.id}
                  </p>
                  {selectedConnector.last_sync_at ? (
                    <p style={smallTextStyle}>Last sync: {new Date(selectedConnector.last_sync_at).toLocaleString()}</p>
                  ) : null}
                </div>
                <ConnectorStatePill connector={selectedConnector} />
              </div>

              {selectedConnector.last_sync_error ? (
                <div style={{ ...bannerStyle, color: TT.red, borderColor: 'rgba(255,69,69,0.35)', marginTop: 16 }}>
                  <AlertCircle size={14} aria-hidden />
                  {selectedConnector.last_sync_error}
                </div>
              ) : null}

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
                {selectedConnector.connector_type !== 'slack' ? (
                  <button type="button" onClick={() => void queueSync(selectedConnector)} style={primaryButtonStyle}>
                    <RefreshCw size={13} aria-hidden />
                    Queue sync
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void queueSync(selectedConnector, true)}
                  disabled={runningInlineSyncId === selectedConnector.id}
                  style={secondaryButtonStyle}
                >
                  <Zap size={13} aria-hidden />
                  {runningInlineSyncId === selectedConnector.id ? 'Running inline sync' : 'Sync now'}
                </button>
                <button
                  type="button"
                  onClick={() => void toggleConnectorActive(selectedConnector)}
                  style={secondaryButtonStyle}
                >
                  {selectedConnector.is_active ? <PauseCircle size={13} aria-hidden /> : <PlayCircle size={13} aria-hidden />}
                  {selectedConnector.is_active ? 'Pause' : 'Resume'}
                </button>
                <button type="button" onClick={() => void disconnect(selectedConnector)} style={dangerButtonStyle}>
                  <Trash2 size={13} aria-hidden />
                  Disconnect
                </button>
              </div>

              <div style={{ marginTop: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10 }}>
                  <h3 style={sectionTitleStyle}>Configuration</h3>
                  {selectedConnectorDirty ? (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button type="button" onClick={() => resetDraft(selectedConnector)} style={secondaryButtonStyle}>
                        <RotateCcw size={13} aria-hidden />
                        Reset draft
                      </button>
                      <button type="button" onClick={() => void saveConfig(selectedConnector)} style={primaryButtonStyle}>
                        <Save size={13} aria-hidden />
                        Save config
                      </button>
                    </div>
                  ) : (
                    <span style={{ ...pillStyle, color: TT.green, borderColor: 'rgba(68,209,122,0.35)' }}>Saved</span>
                  )}
                </div>

                <ConnectorConfigForm
                  connector={selectedConnector}
                  draft={selectedDraft || providerConfigDefaults(selectedConnector.connector_type, selectedConnector.config)}
                  onChange={(key, value) => updateDraft(selectedConnector.id, key, value)}
                />
              </div>

              <div style={{ marginTop: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10 }}>
                  <h3 style={sectionTitleStyle}>Recent Sync Items</h3>
                  <span style={pillStyle}>{(syncItems[selectedConnector.id] || []).length} items</span>
                </div>
                {(syncItems[selectedConnector.id] || []).length === 0 ? (
                  <p style={smallTextStyle}>No provider items captured yet. Run a sync to populate recent activity.</p>
                ) : (
                  <div style={{ display: 'grid', gap: 8 }}>
                    {(syncItems[selectedConnector.id] || []).map((item) => (
                      <div key={item.id} style={syncItemStyle}>
                        <div>
                          <div style={{ color: TT.snow }}>{item.external_type}</div>
                          <div style={smallTextStyle}>{item.external_id}</div>
                        </div>
                        <span>{item.sync_direction}</span>
                        <span style={{ color: item.last_error ? TT.red : TT.inkSubtle }}>{item.sync_status}</span>
                        <span>{item.last_synced_at ? new Date(item.last_synced_at).toLocaleString() : 'Pending'}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <h2 style={cardTitleStyle}>Connector Detail</h2>
              <p style={{ ...mutedStyle, marginTop: 8 }}>
                Select a connected service to review sync state, adjust configuration, or run maintenance actions inline.
              </p>
            </>
          )}
        </article>
      </div>
    </section>
  );
}

function ConnectorConfigForm({
  connector,
  draft,
  onChange,
}: {
  connector: Connector;
  draft: Record<string, any>;
  onChange: (key: string, value: any) => void;
}) {
  if (connector.connector_type === 'slack') {
    return (
      <Field label="Default Slack channel ID">
        <input
          value={draft.default_channel_id || ''}
          onChange={(event) => onChange('default_channel_id', event.target.value)}
          style={inputStyle}
        />
      </Field>
    );
  }

  if (connector.connector_type === 'notion') {
    return (
      <div style={formGridStyle}>
        <Field label="Notion database ID">
          <input value={draft.database_id || ''} onChange={(event) => onChange('database_id', event.target.value)} style={inputStyle} />
        </Field>
        <Field label="CogniFlow push tag">
          <input value={draft.notion_push_tag || ''} onChange={(event) => onChange('notion_push_tag', event.target.value)} style={inputStyle} />
        </Field>
        <label style={checkboxLabelStyle}>
          <input type="checkbox" checked={Boolean(draft.notion_push_all)} onChange={(event) => onChange('notion_push_all', event.target.checked)} />
          Push all notes
        </label>
      </div>
    );
  }

  if (connector.connector_type === 'google') {
    const enabledCapabilities = new Set(draft.enabled_capabilities || ['gmail_sync', 'calendar_sync']);
    const toggleCapability = (capability: string, enabled: boolean) => {
      const next = new Set(enabledCapabilities);
      if (enabled) next.add(capability);
      else next.delete(capability);
      onChange('enabled_capabilities', Array.from(next));
    };

    return (
      <div style={formGridStyle}>
        <label style={checkboxLabelStyle}>
          <input type="checkbox" checked={enabledCapabilities.has('gmail_sync')} onChange={(event) => toggleCapability('gmail_sync', event.target.checked)} />
          Enable Gmail polling
        </label>
        <label style={checkboxLabelStyle}>
          <input type="checkbox" checked={enabledCapabilities.has('calendar_sync')} onChange={(event) => toggleCapability('calendar_sync', event.target.checked)} />
          Enable Calendar sync
        </label>
        <Field label="Gmail query">
          <input value={draft.gmail_query || ''} onChange={(event) => onChange('gmail_query', event.target.value)} style={inputStyle} />
        </Field>
        <Field label="Calendar ID">
          <input value={draft.calendar_id || ''} onChange={(event) => onChange('calendar_id', event.target.value)} style={inputStyle} />
        </Field>
        <Field label="Calendar days ahead">
          <input value={draft.calendar_days_ahead || ''} onChange={(event) => onChange('calendar_days_ahead', event.target.value)} style={inputStyle} />
        </Field>
        <Field label="Match threshold">
          <input value={draft.calendar_match_threshold || ''} onChange={(event) => onChange('calendar_match_threshold', event.target.value)} style={inputStyle} />
        </Field>
      </div>
    );
  }

  return null;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: 6 }}>
      <span style={labelStyle}>{label}</span>
      {children}
    </label>
  );
}

function StatusBanner({ tone, message }: { tone: 'success' | 'error'; message: string }) {
  const Icon = tone === 'success' ? CheckCircle2 : AlertCircle;
  const color = tone === 'success' ? TT.green : TT.red;
  return (
    <div role="status" style={{ ...bannerStyle, color, borderColor: tone === 'success' ? 'rgba(68,209,122,0.35)' : 'rgba(255,69,69,0.35)' }}>
      <Icon size={14} aria-hidden />
      {message}
    </div>
  );
}

const shellStyle: CSSProperties = {
  minHeight: '100%',
  padding: '28px',
  color: TT.snow,
};

const eyebrowStyle: CSSProperties = {
  fontFamily: TT.fontMono,
  color: TT.yolk,
  letterSpacing: '0.16em',
  textTransform: 'uppercase',
  fontSize: 10,
};

const titleStyle: CSSProperties = {
  fontFamily: TT.fontDisplay,
  fontSize: 42,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  lineHeight: 1,
  margin: '6px 0',
};

const mutedStyle: CSSProperties = {
  color: TT.inkSubtle,
  fontFamily: TT.fontBody,
  maxWidth: 720,
};

const cardStyle: CSSProperties = {
  background: TT.inkDeep,
  border: `1px solid ${TT.inkBorder}`,
  borderLeft: `3px solid ${TT.yolk}`,
  borderRadius: 4,
  padding: 18,
};

const summaryCardStyle: CSSProperties = {
  background: TT.inkDeep,
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 4,
  padding: 16,
};

const summaryLabelStyle: CSSProperties = {
  fontFamily: TT.fontMono,
  fontSize: 10,
  color: TT.inkSubtle,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
};

const summaryValueStyle: CSSProperties = {
  fontFamily: TT.fontDisplay,
  fontSize: 28,
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: TT.snow,
  marginBottom: 4,
};

const cardTitleStyle: CSSProperties = {
  fontFamily: TT.fontDisplay,
  fontSize: 22,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  margin: 0,
};

const sectionTitleStyle: CSSProperties = {
  fontFamily: TT.fontMono,
  fontSize: 10,
  color: TT.inkSubtle,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  marginBottom: 8,
};

const smallTextStyle: CSSProperties = {
  color: TT.inkMuted,
  fontFamily: TT.fontMono,
  fontSize: 11,
  lineHeight: 1.6,
};

const pillStyle: CSSProperties = {
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 999,
  padding: '4px 8px',
  fontFamily: TT.fontMono,
  fontSize: 9,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  height: 24,
};

const buttonBaseStyle: CSSProperties = {
  borderRadius: 3,
  height: 34,
  padding: '0 12px',
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 7,
};

const primaryButtonStyle: CSSProperties = {
  ...buttonBaseStyle,
  background: TT.yolk,
  border: `1px solid ${TT.yolk}`,
  color: TT.inkBlack,
};

const secondaryButtonStyle: CSSProperties = {
  ...buttonBaseStyle,
  background: TT.inkRaised,
  border: `1px solid ${TT.inkBorder}`,
  color: TT.snow,
};

const dangerButtonStyle: CSSProperties = {
  ...buttonBaseStyle,
  background: 'transparent',
  border: '1px solid rgba(255,69,69,0.35)',
  color: TT.red,
};

const bannerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  border: '1px solid',
  borderRadius: 4,
  padding: '10px 12px',
  marginBottom: 14,
  fontFamily: TT.fontMono,
  fontSize: 11,
};

const formGridStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
  gap: 12,
};

const labelStyle: CSSProperties = {
  color: TT.inkSubtle,
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
};

const inputStyle: CSSProperties = {
  height: 34,
  background: TT.inkBlack,
  color: TT.snow,
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 3,
  padding: '0 10px',
  fontFamily: TT.fontMono,
  fontSize: 11,
};

const checkboxLabelStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  color: TT.inkSubtle,
  fontFamily: TT.fontMono,
  fontSize: 11,
  marginTop: 20,
};

const syncItemStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1.2fr) 90px 90px minmax(150px, 1fr)',
  gap: 10,
  alignItems: 'center',
  background: TT.inkRaised,
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 3,
  padding: '8px 10px',
  color: TT.inkSubtle,
  fontFamily: TT.fontMono,
  fontSize: 10,
};

const connectorListButtonStyle: CSSProperties = {
  textAlign: 'left',
  width: '100%',
  padding: '12px',
  borderRadius: 4,
  border: `1px solid ${TT.inkBorder}`,
  cursor: 'pointer',
};

const searchShellStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  background: TT.inkDeep,
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 4,
  padding: '0 10px',
  minWidth: 280,
  height: 38,
};

const searchInputStyle: CSSProperties = {
  flex: 1,
  height: '100%',
  background: 'transparent',
  border: 'none',
  color: TT.snow,
  fontFamily: TT.fontBody,
  fontSize: 13,
  outline: 'none',
};
