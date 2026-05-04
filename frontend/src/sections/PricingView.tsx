import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Check, Minus } from 'lucide-react';
import { api, type BillingPlanDefinition, type BillingSubscription } from '@/lib/api';
import { formatCurrencyFromCents, getPlanAnnualSavingsLabel, getPlanPriceCents, type BillingInterval } from '@/lib/billing';
import { BILLING_ADD_ONS, PRODUCT_SUBHEADLINE, PRODUCT_TAGLINE, formatPlanLabel } from '@/lib/productModel';
import type { PlanName } from '@/types';

interface PricingViewProps {
  currentPlan?: PlanName;
  workspaceId?: string | null;
  isAuthenticated?: boolean;
}

const TT = {
  inkBlack: 'var(--theme-canvas)',
  inkDeep: 'var(--theme-panel)',
  inkRaised: 'var(--theme-panel-raised)',
  inkBorder: 'var(--theme-border)',
  inkMuted: 'var(--theme-text-muted)',
  inkSubtle: 'var(--theme-text-subtle)',
  snow: 'var(--theme-text)',
  yolk: 'var(--theme-accent)',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
};

function planLimitLabel(plan: BillingPlanDefinition, metricKey: string): string {
  const metric = plan.limits[metricKey];
  if (!metric || metric.limit === null) return 'Unlimited';
  return `${metric.limit.toLocaleString()}`;
}

function metricValueForPlan(plan: BillingPlanDefinition, metricKey: string): string {
  const metric = plan.limits[metricKey];
  if (!metric) return 'Not available';
  if (metric.limit === null) return 'Unlimited';
  return `${metric.limit.toLocaleString()} ${metric.unit_label}`;
}

function statusLabel(subscription: BillingSubscription | null): string {
  if (!subscription) return 'No subscription yet';
  return subscription.status.replace('_', ' ');
}

export function PricingView({
  currentPlan = 'free',
  workspaceId = null,
  isAuthenticated = true,
}: PricingViewProps) {
  const [interval, setInterval] = useState<BillingInterval>('monthly');
  const [plans, setPlans] = useState<BillingPlanDefinition[]>([]);
  const [subscription, setSubscription] = useState<BillingSubscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [subscriptionLoading, setSubscriptionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ctaMessage, setCtaMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void api.getBillingPlans()
      .then((response) => {
        if (!active) return;
        setPlans(response);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Unable to load plans.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!isAuthenticated || !workspaceId) {
      setSubscription(null);
      return;
    }
    let active = true;
    setSubscriptionLoading(true);
    void api.getWorkspaceBillingSubscription(workspaceId)
      .then((response) => {
        if (!active) return;
        setSubscription(response.subscription);
      })
      .catch(() => {
        if (!active) return;
        setSubscription(null);
      })
      .finally(() => {
        if (active) setSubscriptionLoading(false);
      });
    return () => {
      active = false;
    };
  }, [isAuthenticated, workspaceId]);

  const resolvedCurrentPlan = (subscription?.plan_key as PricingViewProps['currentPlan']) || currentPlan || 'free';

  const comparisonRows = useMemo(() => {
    const baseRows = [
      { key: 'notes_created_monthly', label: 'Notes / month', type: 'limit' as const },
      { key: 'semantic_search_runs_monthly', label: 'Semantic searches / month', type: 'limit' as const },
      { key: 'thinking_sessions_created_monthly', label: 'Thinking sessions / month', type: 'limit' as const },
      { key: 'automations_created_monthly', label: 'Automations / month', type: 'limit' as const },
    ];
    const featureSet = new Set<string>();
    plans.forEach((plan) => {
      plan.features.forEach((feature) => featureSet.add(feature));
    });
    const featureRows = Array.from(featureSet).map((feature) => ({
      key: feature,
      label: feature,
      type: 'feature' as const,
    }));
    return [...baseRows, ...featureRows];
  }, [plans]);

  const handlePrimaryAction = async (plan: BillingPlanDefinition) => {
    setError(null);
    setCtaMessage(null);

    if (!isAuthenticated) {
      setCtaMessage('Sign in to upgrade or manage your workspace billing.');
      return;
    }

    if (!workspaceId) {
      setCtaMessage('Select a workspace to manage plan changes.');
      return;
    }

    if (plan.key === resolvedCurrentPlan) {
      setCtaMessage(`${plan.name} is already active for this workspace.`);
      return;
    }

    if (plan.key === 'enterprise') {
      setCtaMessage('Contact sales for enterprise deployment and custom contracts.');
      return;
    }

    try {
      const response = await api.createBillingPortalSession(workspaceId);
      window.location.assign(response.url);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to open billing portal.';
      setError(message);
    }
  };

  return (
    <div style={{ padding: 28, background: TT.inkBlack, minHeight: '100vh' }}>
      <div style={{ maxWidth: 1180, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: 18 }}>
          <h1 style={{ margin: 0, fontFamily: TT.fontDisplay, fontSize: 48, letterSpacing: '0.05em', color: TT.snow }}>
            PRICING
          </h1>
          <p style={{ margin: '10px auto 0', maxWidth: 680, fontFamily: TT.fontBody, color: TT.inkMuted, lineHeight: 1.7 }}>
            {PRODUCT_TAGLINE} {PRODUCT_SUBHEADLINE}
          </p>
        </div>

        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 18 }}>
          <div style={{ display: 'inline-flex', border: `1px solid ${TT.inkBorder}`, background: TT.inkDeep, borderRadius: 4, padding: 4 }}>
            {(['monthly', 'annual'] as BillingInterval[]).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setInterval(value)}
                aria-pressed={interval === value}
                style={{
                  border: 'none',
                  borderRadius: 3,
                  padding: '8px 12px',
                  minWidth: 100,
                  cursor: 'pointer',
                  background: interval === value ? 'rgba(245,230,66,0.12)' : 'transparent',
                  color: interval === value ? TT.yolk : TT.inkMuted,
                  fontFamily: TT.fontMono,
                  fontSize: 10,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                }}
              >
                {value}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', marginBottom: 20 }}>
          {loading ? (
            <div style={{ color: TT.inkMuted, fontFamily: TT.fontMono, fontSize: 11 }}>Loading plans…</div>
          ) : plans.map((plan) => {
            const isCurrent = resolvedCurrentPlan === plan.key;
            const price = getPlanPriceCents(plan, interval);
            const savingsLabel = interval === 'annual' ? getPlanAnnualSavingsLabel(plan) : null;
            return (
              <article
                key={plan.key}
                style={{
                  background: TT.inkDeep,
                  border: `1px solid ${isCurrent ? 'rgba(245,230,66,0.4)' : TT.inkBorder}`,
                  borderTop: `3px solid ${isCurrent || plan.highlighted ? TT.yolk : TT.inkBorder}`,
                  borderRadius: 4,
                  padding: 16,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
                  <div>
                    <div style={{ fontFamily: TT.fontDisplay, fontSize: 24, letterSpacing: '0.05em', color: TT.snow }}>
                      {plan.name}
                    </div>
                    <p style={{ margin: '4px 0 0', fontFamily: TT.fontBody, fontSize: 12, color: TT.inkMuted, lineHeight: 1.6 }}>
                      {plan.description}
                    </p>
                  </div>
                  {plan.highlighted ? (
                    <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.yolk, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                      Most popular
                    </span>
                  ) : null}
                </div>

                <div style={{ marginTop: 14 }}>
                  <div style={{ fontFamily: TT.fontDisplay, fontSize: 34, color: TT.snow }}>
                    {price === null ? 'Custom' : formatCurrencyFromCents(price)}
                    {price !== null ? (
                      <span style={{ fontFamily: TT.fontMono, fontSize: 10, marginLeft: 6, color: TT.inkMuted }}>
                        /month
                      </span>
                    ) : null}
                  </div>
                  {price !== null && interval === 'annual' && savingsLabel ? (
                    <div style={{ marginTop: 4, fontFamily: TT.fontMono, fontSize: 9.5, color: TT.yolk }}>{savingsLabel}</div>
                  ) : null}
                </div>

                <div style={{ marginTop: 12, display: 'grid', gap: 6 }}>
                  <LimitChip label="Notes" value={planLimitLabel(plan, 'notes_created_monthly')} />
                  <LimitChip label="Searches" value={planLimitLabel(plan, 'semantic_search_runs_monthly')} />
                  <LimitChip label="Sessions" value={planLimitLabel(plan, 'thinking_sessions_created_monthly')} />
                  <LimitChip label="Automations" value={planLimitLabel(plan, 'automations_created_monthly')} />
                </div>

                <button
                  type="button"
                  onClick={() => {
                    void handlePrimaryAction(plan);
                  }}
                  style={{
                    marginTop: 12,
                    width: '100%',
                    height: 36,
                    borderRadius: 3,
                    border: `1px solid ${isCurrent ? TT.inkBorder : TT.yolk}`,
                    background: isCurrent ? TT.inkRaised : TT.yolk,
                    color: isCurrent ? TT.inkMuted : '#111',
                    cursor: 'pointer',
                    fontFamily: TT.fontMono,
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                  }}
                >
                  {isCurrent ? 'Current plan' : plan.cta_label}
                </button>
              </article>
            );
          })}
        </div>

        <div style={{ marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <MetaCard label="Current plan" value={formatPlanLabel(resolvedCurrentPlan)} />
          <MetaCard label="Subscription status" value={subscriptionLoading ? 'Loading…' : statusLabel(subscription)} />
          <MetaCard label="Billing interval" value={subscription?.billing_interval?.toUpperCase() || interval.toUpperCase()} />
        </div>

        <section style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 4, padding: 14, marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.05em', color: TT.snow }}>
            Usage add-ons
          </h2>
          <p style={{ margin: '6px 0 12px', fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.6 }}>
            Add-ons are priced for cost control and expansion. Self-serve checkout appears only after matching Stripe prices are configured.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10 }}>
            {BILLING_ADD_ONS.map((addOn) => (
              <div key={addOn.name} style={{ border: `1px solid ${TT.inkBorder}`, background: TT.inkRaised, borderRadius: 3, padding: '10px 12px' }}>
                <div style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                  {addOn.name}
                </div>
                <div style={{ marginTop: 6, fontFamily: TT.fontDisplay, fontSize: 22, color: TT.snow, letterSpacing: '0.04em' }}>
                  {addOn.price}
                </div>
              </div>
            ))}
          </div>
        </section>

        {ctaMessage ? (
          <div style={{ marginBottom: 12, border: '1px solid rgba(245,230,66,0.3)', background: 'rgba(245,230,66,0.08)', borderRadius: 3, padding: '10px 12px', color: TT.snow, fontFamily: TT.fontBody, fontSize: 12.5 }}>
            {ctaMessage}
          </div>
        ) : null}
        {error ? (
          <div style={{ marginBottom: 12, border: '1px solid rgba(255,69,69,0.3)', background: 'rgba(255,69,69,0.08)', borderRadius: 3, padding: '10px 12px', color: '#ffb7b7', fontFamily: TT.fontBody, fontSize: 12.5 }}>
            {error}
          </div>
        ) : null}

        <section style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 4, padding: 14 }}>
          <h2 style={{ margin: 0, fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.05em', color: TT.snow }}>
            Feature comparison
          </h2>
          <p style={{ margin: '6px 0 12px', fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.6 }}>
            This table is generated from the same backend plan catalog used by entitlement enforcement.
          </p>

          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 760, borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={headerCellStyle}>Capability</th>
                  {plans.map((plan) => (
                    <th key={plan.key} style={headerCellStyle}>{plan.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparisonRows.map((row) => (
                  <tr key={row.key}>
                    <td style={labelCellStyle}>{row.label}</td>
                    {plans.map((plan) => (
                      <td key={`${row.key}:${plan.key}`} style={valueCellStyle}>
                        {row.type === 'limit' ? (
                          <span>{metricValueForPlan(plan, row.key)}</span>
                        ) : plan.features.includes(row.label) ? (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#75dd9b' }}>
                            <Check size={12} /> Included
                          </span>
                        ) : (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: TT.inkMuted }}>
                            <Minus size={12} /> Not included
                          </span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function LimitChip({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      border: `1px solid ${TT.inkBorder}`,
      background: TT.inkRaised,
      borderRadius: 3,
      padding: '8px 10px',
      gap: 8,
    }}>
      <span style={{ fontFamily: TT.fontMono, fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.08em', color: TT.inkMuted }}>
        {label}
      </span>
      <span style={{ fontFamily: TT.fontMono, fontSize: 10.5, color: TT.snow }}>{value}</span>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      border: `1px solid ${TT.inkBorder}`,
      background: TT.inkDeep,
      borderRadius: 3,
      padding: '10px 12px',
      minWidth: 180,
    }}>
      <div style={{ fontFamily: TT.fontMono, fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.08em', color: TT.inkMuted }}>
        {label}
      </div>
      <div style={{ marginTop: 5, fontFamily: TT.fontMono, fontSize: 11, color: TT.snow }}>
        {value}
      </div>
    </div>
  );
}

const headerCellStyle: CSSProperties = {
  textAlign: 'left',
  padding: '8px 10px',
  borderBottom: `1px solid ${TT.inkBorder}`,
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: TT.inkMuted,
};

const labelCellStyle: CSSProperties = {
  padding: '9px 10px',
  borderBottom: `1px solid ${TT.inkBorder}`,
  fontFamily: TT.fontBody,
  fontSize: 12.5,
  color: TT.snow,
};

const valueCellStyle: CSSProperties = {
  padding: '9px 10px',
  borderBottom: `1px solid ${TT.inkBorder}`,
  fontFamily: TT.fontBody,
  fontSize: 12,
  color: TT.inkSubtle,
};
