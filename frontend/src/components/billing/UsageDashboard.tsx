import { useEffect, useMemo, useState } from 'react';
import { ApiError, api, type BillingUsageMetric, type BillingUsageResponse } from '@/lib/api';
import { formatCurrencyFromCents, getUsageProgressState, getUsagePercentage } from '@/lib/billing';

interface UsageDashboardProps {
  workspaceId: string;
  canManageBilling: boolean;
}

const TT = {
  inkDeep: 'var(--theme-panel)',
  inkRaised: 'var(--theme-panel-raised)',
  inkBorder: 'var(--theme-border)',
  inkMuted: 'var(--theme-text-muted)',
  inkSubtle: 'var(--theme-text-subtle)',
  snow: 'var(--theme-text)',
  yolk: 'var(--theme-accent)',
  red: 'var(--theme-error)',
  amber: '#f59e0b',
  green: '#5BE37A',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
};

function billingUsageErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 402) {
      return 'This workspace has reached a plan limit. Upgrade to continue using this feature.';
    }
    if (error.status === 403) {
      return 'You do not have permission to view workspace billing.';
    }
    if (error.status === 409) {
      return error.message || 'Workspace billing state needs attention.';
    }
    if (error.status >= 500) {
      return 'Billing usage is temporarily unavailable. Try again shortly.';
    }
  }
  return error instanceof Error ? error.message : 'Unable to load usage metrics.';
}

function metricUsage(metric: Partial<BillingUsageMetric>): number {
  return typeof metric.current_usage === 'number' ? Math.max(0, metric.current_usage) : 0;
}

function metricProjection(metric: Partial<BillingUsageMetric>): number {
  return typeof metric.projected_usage === 'number' ? Math.max(0, metric.projected_usage) : metricUsage(metric);
}

export function UsageDashboard({ workspaceId, canManageBilling }: UsageDashboardProps) {
  const [payload, setPayload] = useState<BillingUsageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openingPortal, setOpeningPortal] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void api.getBillingUsage(workspaceId)
      .then((response) => {
        if (!active) return;
        setPayload(response);
      })
      .catch((err) => {
        if (!active) return;
        setPayload(null);
        setError(billingUsageErrorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);

  const usageMetrics = payload?.usage_metrics ?? [];
  const projectedCost = payload?.projected_monthly_cost ?? {
    base_monthly_cents: 0,
    projected_overage_cents: 0,
    projected_total_monthly_cents: 0,
  };

  const periodLabel = useMemo(() => {
    const first = usageMetrics[0];
    if (!first?.period_start || !first?.period_end) return null;
    try {
      const start = new Date(first.period_start);
      const end = new Date(first.period_end);
      return `${start.toLocaleDateString()} - ${end.toLocaleDateString()}`;
    } catch {
      return null;
    }
  }, [usageMetrics]);

  const handleManageBilling = async () => {
    setOpeningPortal(true);
    setError(null);
    try {
      const response = await api.createBillingPortalSession(workspaceId);
      window.location.assign(response.url);
    } catch (err) {
      setError(billingUsageErrorMessage(err));
      setOpeningPortal(false);
    }
  };

  return (
    <section style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 4, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ margin: 0, fontFamily: TT.fontDisplay, letterSpacing: '0.06em', fontSize: 28, color: TT.snow }}>
            Usage & Billing
          </h3>
          <p style={{ margin: '6px 0 0', fontFamily: TT.fontBody, color: TT.inkMuted, fontSize: 12.5, lineHeight: 1.6 }}>
            Live metrics from workspace usage counters with projected monthly cost based on current pace.
          </p>
          {periodLabel ? (
            <div style={{ marginTop: 6, fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkSubtle }}>
              Billing period: {periodLabel}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => {
            void handleManageBilling();
          }}
          disabled={!canManageBilling || openingPortal}
          style={{
            height: 36,
            padding: '0 12px',
            borderRadius: 3,
            border: `1px solid ${TT.yolk}`,
            background: canManageBilling ? TT.yolk : TT.inkRaised,
            color: canManageBilling ? '#111' : TT.inkMuted,
            cursor: canManageBilling ? 'pointer' : 'not-allowed',
            fontFamily: TT.fontMono,
            fontSize: 10,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            opacity: openingPortal ? 0.75 : 1,
          }}
        >
          {openingPortal ? 'Opening...' : 'Manage billing'}
        </button>
      </div>

      {loading ? (
        <div style={{ marginTop: 12, fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted }}>
          Loading usage counters...
        </div>
      ) : null}
      {error ? (
        <div style={{ marginTop: 12, border: '1px solid rgba(255,69,69,0.28)', background: 'rgba(255,69,69,0.08)', borderRadius: 3, padding: '10px 12px', color: '#ffb7b7', fontFamily: TT.fontBody, fontSize: 12.5 }}>
          {error}
        </div>
      ) : null}

      {payload ? (
        <>
          {usageMetrics.length === 0 ? (
            <div style={{ marginTop: 12, border: `1px solid ${TT.inkBorder}`, background: TT.inkRaised, borderRadius: 3, padding: '10px 12px', color: TT.inkMuted, fontFamily: TT.fontBody, fontSize: 12.5 }}>
              No usage has been recorded for this billing period.
            </div>
          ) : null}
          <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
            {usageMetrics.map((metric) => {
              const percentage = getUsagePercentage(metric);
              const state = getUsageProgressState(percentage);
              const barColor = state === 'red' ? TT.red : state === 'amber' ? TT.amber : TT.green;
              const width = percentage === null ? 100 : Math.min(percentage, 100);
              const currentUsage = metricUsage(metric);
              const projectedUsage = metricProjection(metric);
              const limitLabel = typeof metric.limit === 'number' ? metric.limit.toLocaleString() : 'unlimited';
              return (
                <article key={metric.metric_key} style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <div style={{ fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted }}>
                      {metric.label || metric.metric_key}
                    </div>
                    <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.snow }}>
                      {currentUsage.toLocaleString()}/{limitLabel}
                    </div>
                  </div>
                  <div style={{ marginTop: 8, height: 8, borderRadius: 999, background: '#111', border: `1px solid ${TT.inkBorder}`, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${width}%`, background: barColor }} />
                  </div>
                  <div style={{ marginTop: 7, display: 'flex', justifyContent: 'space-between', gap: 8, fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkSubtle }}>
                    <span>{percentage === null ? 'Unlimited' : `${percentage.toFixed(1)}% used`}</span>
                    <span>Projected: {projectedUsage.toLocaleString()}</span>
                  </div>
                </article>
              );
            })}
          </div>

          <div style={{ marginTop: 12, border: `1px solid ${TT.inkBorder}`, background: TT.inkRaised, borderRadius: 3, padding: '10px 12px', display: 'grid', gap: 4 }}>
            <div style={{ fontFamily: TT.fontMono, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: TT.inkMuted }}>
              Projected monthly cost
            </div>
            <div style={{ fontFamily: TT.fontDisplay, fontSize: 30, color: TT.snow, letterSpacing: '0.04em' }}>
              {formatCurrencyFromCents(projectedCost.projected_total_monthly_cents)}
            </div>
            <div style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted }}>
              Base {formatCurrencyFromCents(projectedCost.base_monthly_cents)} + projected overage {formatCurrencyFromCents(projectedCost.projected_overage_cents)}.
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
