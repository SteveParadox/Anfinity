import type { CSSProperties } from 'react';
import type { EntitlementMetadata } from '@/lib/billing';

interface UpgradePromptProps {
  entitlement: EntitlementMetadata;
  onDismiss?: () => void;
  onUpgrade?: () => void;
  upgradeLabel?: string;
  loading?: boolean;
  error?: string | null;
}

const TT = {
  inkDeep: 'var(--theme-panel)',
  inkRaised: 'var(--theme-panel-raised)',
  inkBorder: 'var(--theme-border)',
  inkMuted: 'var(--theme-text-muted)',
  snow: 'var(--theme-text)',
  yolk: 'var(--theme-accent)',
  red: 'var(--theme-error)',
  fontMono: "'IBM Plex Mono', monospace",
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontBody: "'IBM Plex Sans', sans-serif",
};

export function UpgradePrompt({
  entitlement,
  onDismiss,
  onUpgrade,
  upgradeLabel = 'View plans',
  loading = false,
  error = null,
}: UpgradePromptProps) {
  const usageDetail = entitlement.limit !== null && entitlement.usage !== null
    ? `${entitlement.usage}/${entitlement.limit}`
    : 'Unavailable';

  return (
    <div style={containerStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span style={badgeStyle}>Upgrade required</span>
        {onDismiss ? (
          <button type="button" onClick={onDismiss} style={dismissButtonStyle} aria-label="Dismiss upgrade prompt">
            Dismiss
          </button>
        ) : null}
      </div>
      <div style={{ marginTop: 8 }}>
        <div style={{ fontFamily: TT.fontDisplay, letterSpacing: '0.06em', color: TT.snow, fontSize: 22 }}>
          Plan limit reached
        </div>
        <p style={{ margin: '6px 0 0', fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.6 }}>
          {entitlement.message}
        </p>
      </div>

      <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
        <Metric label="Current plan" value={entitlement.current_plan} />
        <Metric label="Required plan" value={entitlement.required_plan || 'Higher tier'} />
        <Metric label="Usage" value={usageDetail} />
      </div>

      {error ? (
        <div style={{ marginTop: 10, fontFamily: TT.fontMono, fontSize: 10.5, color: TT.red }}>
          {error}
        </div>
      ) : null}

      <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={onUpgrade}
          disabled={loading}
          style={{
            height: 34,
            padding: '0 14px',
            borderRadius: 3,
            border: `1px solid ${TT.yolk}`,
            background: TT.yolk,
            color: '#111',
            fontFamily: TT.fontMono,
            fontSize: 10,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            cursor: loading ? 'wait' : 'pointer',
            opacity: loading ? 0.7 : 1,
          }}
        >
          {loading ? 'Opening…' : upgradeLabel}
        </button>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      border: `1px solid ${TT.inkBorder}`,
      background: TT.inkRaised,
      borderRadius: 3,
      padding: '8px 10px',
    }}>
      <div style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{ marginTop: 4, fontFamily: TT.fontMono, fontSize: 11, color: TT.snow, letterSpacing: '0.03em' }}>
        {value}
      </div>
    </div>
  );
}

const containerStyle: CSSProperties = {
  background: TT.inkDeep,
  border: `1px solid rgba(245,230,66,0.3)`,
  borderLeft: `3px solid ${TT.yolk}`,
  borderRadius: 4,
  padding: 14,
};

const badgeStyle: CSSProperties = {
  fontFamily: TT.fontMono,
  fontSize: 9,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: TT.yolk,
  background: 'rgba(245,230,66,0.12)',
  border: '1px solid rgba(245,230,66,0.25)',
  borderRadius: 999,
  padding: '2px 8px',
};

const dismissButtonStyle: CSSProperties = {
  border: `1px solid ${TT.inkBorder}`,
  background: 'transparent',
  borderRadius: 3,
  color: TT.inkMuted,
  padding: '4px 8px',
  fontFamily: TT.fontMono,
  fontSize: 9.5,
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  cursor: 'pointer',
};
