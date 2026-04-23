import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { AlertCircle, CheckCircle2, ExternalLink, RefreshCw, Save, Trash2 } from 'lucide-react';
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

export function IntegrationsView() {
  const { currentWorkspaceId } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [syncItems, setSyncItems] = useState<Record<string, SyncItem[]>>({});
  const [draftConfigs, setDraftConfigs] = useState<Record<string, Record<string, any>>>({});
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const [providerResponse, connectorResponse] = await Promise.all([
        api.listIntegrationProviders(),
        api.listConnectors(currentWorkspaceId),
      ]);
      setProviders(providerResponse);
      setConnectors(connectorResponse.connectors || []);
      setDraftConfigs(
        Object.fromEntries(
          (connectorResponse.connectors || []).map((connector: Connector) => [
            connector.id,
            providerConfigDefaults(connector.connector_type, connector.config || {}),
          ])
        )
      );

      const itemPairs = await Promise.all(
        (connectorResponse.connectors || []).map(async (connector: Connector) => {
          const response = await api.listConnectorSyncItems(connector.id, currentWorkspaceId, 8).catch(() => ({ items: [] }));
          return [connector.id, response.items || []] as const;
        })
      );
      setSyncItems(Object.fromEntries(itemPairs));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load integrations');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [currentWorkspaceId]);

  const connectProvider = async (provider: string) => {
    if (!currentWorkspaceId) return;
    setError(null);
    try {
      const response = await api.getIntegrationOAuthUrl(provider, currentWorkspaceId);
      window.location.assign(response.authorization_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start OAuth flow');
    }
  };

  const queueSync = async (connector: Connector) => {
    if (!currentWorkspaceId) return;
    setMessage(null);
    setError(null);
    try {
      const response = await api.syncConnector(connector.id, currentWorkspaceId);
      setMessage(response.task_id ? `Sync queued for ${connector.connector_type}` : `Sync started for ${connector.connector_type}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to queue sync');
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
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <p style={eyebrowStyle}>Feature 2</p>
          <h1 style={titleStyle}>Integrations</h1>
          <p style={mutedStyle}>One shared OAuth layer powers Slack, Notion, Gmail, and Calendar syncs.</p>
        </div>
        <button type="button" onClick={refresh} disabled={loading} style={secondaryButtonStyle}>
          <RefreshCw size={13} aria-hidden />
          Refresh
        </button>
      </div>

      {error && <StatusBanner tone="error" message={error} />}
      {message && <StatusBanner tone="success" message={message} />}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14, marginBottom: 24 }}>
        {providers.map((provider) => {
          const connected = connectors.some((connector) => connector.connector_type === provider.provider);
          return (
            <article key={provider.provider} style={cardStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <h2 style={cardTitleStyle}>{provider.label}</h2>
                  <p style={smallTextStyle}>{providerDescription(provider)}</p>
                </div>
                <span style={{ ...pillStyle, color: connected ? TT.green : TT.inkSubtle, borderColor: connected ? 'rgba(68,209,122,0.35)' : TT.inkBorder }}>
                  {connected ? 'Connected' : 'Not connected'}
                </span>
              </div>
              <p style={{ ...smallTextStyle, marginTop: 14 }}>
                Capabilities: {provider.capabilities.join(', ')}
              </p>
              <p style={smallTextStyle}>
                Scopes: {provider.required_scopes.length ? provider.required_scopes.join(', ') : 'Provider-managed Notion access'}
              </p>
              <button type="button" onClick={() => connectProvider(provider.provider)} style={primaryButtonStyle}>
                <ExternalLink size={13} aria-hidden />
                {connected ? 'Reconnect' : 'Connect'}
              </button>
            </article>
          );
        })}
      </div>

      <div style={{ display: 'grid', gap: 14 }}>
        {connectors.map((connector) => (
          <article key={connector.id} style={cardStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
              <div>
                <h2 style={cardTitleStyle}>{connector.connector_type.toUpperCase()}</h2>
                <p style={smallTextStyle}>
                  Account: {connector.external_account_metadata?.name || connector.external_account_metadata?.email || connector.id}
                </p>
              </div>
              <span style={{ ...pillStyle, color: ['failed', 'reauth_required'].includes(connector.sync_status) ? TT.red : TT.green }}>
                {connector.sync_status || 'idle'}
              </span>
            </div>
            {connector.last_sync_at && (
              <p style={smallTextStyle}>Last sync: {new Date(connector.last_sync_at).toLocaleString()}</p>
            )}

            {connector.last_sync_error && (
              <p style={{ ...smallTextStyle, color: TT.red, marginTop: 10 }}>{connector.last_sync_error}</p>
            )}

            <ConnectorConfigForm
              connector={connector}
              draft={draftConfigs[connector.id] || providerConfigDefaults(connector.connector_type, connector.config)}
              onChange={(key, value) => updateDraft(connector.id, key, value)}
            />

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
              {connector.connector_type !== 'slack' && (
                <button type="button" onClick={() => queueSync(connector)} style={primaryButtonStyle}>
                  <RefreshCw size={13} aria-hidden />
                  Queue sync
                </button>
              )}
              <button type="button" onClick={() => saveConfig(connector)} style={secondaryButtonStyle}>
                <Save size={13} aria-hidden />
                Save config
              </button>
              <button type="button" onClick={() => disconnect(connector)} style={dangerButtonStyle}>
                <Trash2 size={13} aria-hidden />
                Disconnect
              </button>
            </div>

            <div style={{ marginTop: 16 }}>
              <h3 style={sectionTitleStyle}>Recent sync items</h3>
              {(syncItems[connector.id] || []).length === 0 ? (
                <p style={smallTextStyle}>No provider items captured yet.</p>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {(syncItems[connector.id] || []).map((item) => (
                    <div key={item.id} style={syncItemStyle}>
                      <span>{item.external_type}</span>
                      <span>{item.sync_direction}</span>
                      <span>{item.sync_status}</span>
                      <span>{item.last_synced_at ? new Date(item.last_synced_at).toLocaleString() : 'Pending'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </article>
        ))}
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
        <input value={draft.default_channel_id || ''} onChange={(event) => onChange('default_channel_id', event.target.value)} style={inputStyle} />
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
      if (enabled) {
        next.add(capability);
      } else {
        next.delete(capability);
      }
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
  marginTop: 16,
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
  marginTop: 14,
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
  gridTemplateColumns: '1fr 90px 90px minmax(150px, 1fr)',
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
