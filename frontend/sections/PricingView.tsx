import { useDeferredValue, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, Building2, Check, Crown, HelpCircle, Search, Sparkles, Users, Zap } from 'lucide-react';
import type { PricingPlan } from '@/types';

interface PricingViewProps {
  currentPlan: 'free' | 'pro' | 'team' | 'enterprise';
}

type PricingFocus = 'all' | 'individual' | 'team' | 'security';

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
  yolkBright: '#FFF176',
  blue: '#7DD3FC',
  green: '#44D17A',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

const plans: PricingPlan[] = [
  {
    id: 'free',
    name: 'Free',
    description: 'Perfect for getting started',
    price: 0,
    priceUnit: '/month',
    features: ['Up to 100 notes', 'Basic AI search', '3 workspaces', 'Knowledge graph view', 'Web clipper', 'Mobile app access'],
    limitations: ['No AI insights', 'No workflows', 'Limited integrations'],
    cta: 'Get Started',
  },
  {
    id: 'pro',
    name: 'Pro',
    description: 'For power users who want more',
    price: 12,
    priceUnit: '/month',
    features: ['Unlimited notes', 'Advanced AI search', 'Unlimited workspaces', 'AI insights & suggestions', 'Semantic knowledge graph', 'Voice notes & transcription', 'Priority support'],
    limitations: [],
    highlighted: true,
    cta: 'Upgrade to Pro',
  },
  {
    id: 'team',
    name: 'Team',
    description: 'For collaborative teams',
    price: 25,
    priceUnit: '/user/month',
    features: ['Everything in Pro', 'Team workspaces', 'Shared knowledge graphs', 'Workflow automation', 'SSO & advanced security', 'Admin dashboard', 'API access', 'Dedicated support'],
    limitations: [],
    cta: 'Start Team Trial',
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    description: 'For large organizations',
    price: 0,
    priceUnit: 'Custom',
    features: ['Everything in Team', 'Custom AI models', 'On-premise deployment', 'Advanced analytics', 'SLA guarantee', 'Custom integrations', 'Dedicated success manager', '24/7 phone support'],
    limitations: [],
    cta: 'Contact Sales',
  },
];

const faqs = [
  {
    question: 'Can I switch plans anytime?',
    answer: "Yes, you can upgrade or downgrade your plan at any time. Changes take effect immediately, and we'll prorate any differences.",
  },
  {
    question: 'What happens to my data if I cancel?',
    answer: 'Your data remains accessible in read-only mode for 30 days. After that, you can export everything or reactivate your account.',
  },
  {
    question: 'Is there a free trial for paid plans?',
    answer: 'Yes. Pro and Team plans come with a 14-day free trial, so you can test collaboration and AI workflows before committing.',
  },
  {
    question: 'How does AI search work?',
    answer: 'Our AI understands natural language queries and finds semantically related content, not just keyword matches. It learns from your usage patterns to improve over time.',
  },
];

const planIcons: Record<string, React.ElementType> = {
  free: Zap,
  pro: Sparkles,
  team: Crown,
  enterprise: Building2,
};

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

function getMonthlyPrice(plan: PricingPlan, isAnnual: boolean) {
  if (plan.id === 'enterprise') return null;
  if (plan.id === 'free') return 0;
  return isAnnual ? Math.round(plan.price * 0.8) : plan.price;
}

export function PricingView({ currentPlan }: PricingViewProps) {
  const [isAnnual, setIsAnnual] = useState(true);
  const [expandedFaq, setExpandedFaq] = useState<number | null>(0);
  const [selectedPlanId, setSelectedPlanId] = useState<PricingPlan['id']>(currentPlan);
  const [focus, setFocus] = useState<PricingFocus>('all');
  const [teamSize, setTeamSize] = useState(5);
  const [faqQuery, setFaqQuery] = useState('');
  const [ctaMessage, setCtaMessage] = useState<string | null>(null);

  const deferredFaqQuery = useDeferredValue(faqQuery.trim().toLowerCase());
  const selectedPlan = plans.find((plan) => plan.id === selectedPlanId) ?? plans[1];

  const filteredPlans = useMemo(() => {
    return plans.filter((plan) => {
      if (focus === 'all') return true;
      if (focus === 'individual') return plan.id === 'free' || plan.id === 'pro';
      if (focus === 'team') return plan.id === 'pro' || plan.id === 'team';
      return plan.id === 'team' || plan.id === 'enterprise';
    });
  }, [focus]);

  const filteredFaqs = faqs.filter((faq) => {
    if (!deferredFaqQuery) return true;
    return [faq.question, faq.answer].join(' ').toLowerCase().includes(deferredFaqQuery);
  });

  const selectedPlanMonthlyCost = selectedPlan.id === 'enterprise'
    ? null
    : (getMonthlyPrice(selectedPlan, isAnnual) ?? 0) * Math.max(teamSize, 1);
  const yearlySavings = selectedPlan.id === 'enterprise' || selectedPlan.id === 'free'
    ? 0
    : (selectedPlan.price - (getMonthlyPrice(selectedPlan, true) ?? selectedPlan.price)) * Math.max(teamSize, 1) * 12;

  const recommendedPlan =
    focus === 'security'
      ? 'enterprise'
      : teamSize >= 8
        ? 'team'
        : teamSize >= 2
          ? 'pro'
          : 'free';

  const recommendationReason =
    recommendedPlan === 'enterprise'
      ? 'Best fit when security controls, custom deployment, or support guarantees matter most.'
      : recommendedPlan === 'team'
        ? 'Best fit for multiple collaborators who need workflow automation and shared governance.'
        : recommendedPlan === 'pro'
          ? 'Best fit when you need AI-heavy workflows without full team administration.'
          : 'Best fit for lightweight solo exploration and early adoption.';

  const handlePlanAction = (plan: PricingPlan) => {
    if (plan.id === currentPlan) {
      setCtaMessage(`${plan.name} is already active on this workspace.`);
      return;
    }

    if (plan.id === 'enterprise') {
      setCtaMessage('Sales follow-up prepared. Use this plan as the anchor for a custom deployment conversation.');
      return;
    }

    setCtaMessage(`${plan.cta} flow is ready. ${plan.name} is now the active recommendation on this page.`);
    setSelectedPlanId(plan.id);
  };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} style={{ textAlign: 'center', marginBottom: 30 }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '3px 12px', background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2, marginBottom: 18 }}>
          <Sparkles size={10} color={TT.yolk} />
          <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.yolk }}>
            Simple Pricing
          </span>
        </div>

        <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 52, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase', marginBottom: 12 }}>
          <span style={{ color: TT.yolk }}>C</span>HOOSE YOUR PLAN
        </h1>
        <div style={{ width: 36, height: 3, background: TT.yolk, margin: '12px auto 16px' }} />
        <p style={{ fontFamily: TT.fontBody, fontSize: 14, color: TT.inkMuted, maxWidth: 560, margin: '0 auto 24px', lineHeight: 1.65 }}>
          Start free and scale as you grow. The structure stays familiar, but this view now lets you compare plans, estimate cost, and surface the right path faster.
        </p>

        <div style={{ display: 'flex', justifyContent: 'center', flexWrap: 'wrap', gap: 10, marginBottom: 20 }}>
          {(['all', 'individual', 'team', 'security'] as PricingFocus[]).map((option) => (
            <FilterButton key={option} active={focus === option} onClick={() => setFocus(option)}>
              {option}
            </FilterButton>
          ))}
        </div>

        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 12, padding: '8px 16px', background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3 }}>
          <span style={{ fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.07em', textTransform: 'uppercase', color: !isAnnual ? TT.snow : TT.inkMuted }}>Monthly</span>
          <button
            type="button"
            aria-label="Toggle billing cadence"
            onClick={() => setIsAnnual((value) => !value)}
            style={{
              width: 40,
              height: 22,
              borderRadius: 11,
              background: isAnnual ? TT.yolk : TT.inkMid,
              border: 'none',
              cursor: 'pointer',
              position: 'relative',
              transition: 'background 0.2s',
            }}
          >
            <span
              style={{
                position: 'absolute',
                top: 3,
                left: isAnnual ? 20 : 3,
                width: 16,
                height: 16,
                borderRadius: '50%',
                background: isAnnual ? TT.inkBlack : TT.inkMuted,
                transition: 'left 0.2s',
              }}
            />
          </button>
          <span style={{ fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.07em', textTransform: 'uppercase', color: isAnnual ? TT.snow : TT.inkMuted }}>
            Annual
          </span>
          {isAnnual ? (
            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.07em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(245,230,66,0.1)', color: TT.yolk, border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2 }}>
              Save 20%
            </span>
          ) : null}
        </div>
      </motion.div>

      {ctaMessage ? (
        <div
          role="status"
          style={{
            maxWidth: 980,
            margin: '0 auto 20px',
            background: 'rgba(245,230,66,0.08)',
            border: '1px solid rgba(245,230,66,0.25)',
            borderRadius: 3,
            padding: '12px 14px',
            color: TT.snow,
            fontSize: 11,
            letterSpacing: '0.04em',
          }}
        >
          {ctaMessage}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, maxWidth: 1100, margin: '0 auto 24px' }}>
        <div style={summaryCardStyle}>
          <div style={summaryLabelStyle}>Current plan</div>
          <div style={summaryValueStyle}>{currentPlan.toUpperCase()}</div>
          <p style={summaryHelperStyle}>This workspace is currently on the {currentPlan} plan.</p>
        </div>
        <div style={summaryCardStyle}>
          <div style={summaryLabelStyle}>Team size</div>
          <div style={summaryValueStyle}>{teamSize}</div>
          <input
            type="range"
            min={1}
            max={50}
            value={teamSize}
            onChange={(event) => setTeamSize(Number(event.target.value))}
            aria-label="Adjust seat count"
            style={{ width: '100%', accentColor: TT.yolk }}
          />
        </div>
        <div style={summaryCardStyle}>
          <div style={summaryLabelStyle}>Monthly estimate</div>
          <div style={summaryValueStyle}>
            {selectedPlanMonthlyCost === null ? 'Custom' : `$${selectedPlanMonthlyCost}`}
          </div>
          <p style={summaryHelperStyle}>
            {selectedPlan.id === 'enterprise'
              ? 'Pricing is tailored to deployment and support requirements.'
              : `Based on ${teamSize} seat${teamSize === 1 ? '' : 's'} on ${selectedPlan.name}.`}
          </p>
        </div>
        <div style={summaryCardStyle}>
          <div style={summaryLabelStyle}>Recommendation</div>
          <div style={summaryValueStyle}>{recommendedPlan.toUpperCase()}</div>
          <p style={summaryHelperStyle}>{recommendationReason}</p>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(280px, 340px)', gap: 18, maxWidth: 1100, margin: '0 auto 34px', alignItems: 'start' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 240px))',
            gap: 24,
            justifyContent: 'center',
          }}
        >
          {filteredPlans.map((plan, index) => {
            const isCurrent = currentPlan === plan.id;
            const highlighted = !!plan.highlighted;
            const selected = selectedPlan.id === plan.id;
            const Icon = planIcons[plan.id] ?? Zap;
            const monthlyPrice = getMonthlyPrice(plan, isAnnual);

            return (
              <motion.button
                key={plan.id}
                type="button"
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.06 }}
                onClick={() => setSelectedPlanId(plan.id)}
                aria-pressed={selected}
                style={{
                  position: 'relative',
                  marginTop: highlighted ? 0 : 16,
                  background: highlighted ? 'rgba(245,230,66,0.04)' : TT.inkDeep,
                  border: `1px solid ${selected ? 'rgba(245,230,66,0.35)' : highlighted ? 'rgba(245,230,66,0.3)' : isCurrent ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                  borderTop: `3px solid ${selected || highlighted || isCurrent ? TT.yolk : TT.inkBorder}`,
                  borderRadius: 3,
                  padding: '22px 20px',
                  height: '100%',
                  boxSizing: 'border-box',
                  display: 'flex',
                  flexDirection: 'column',
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                {highlighted ? (
                  <div style={{ position: 'absolute', top: -12, left: '50%', transform: 'translateX(-50%)', zIndex: 1 }}>
                    <span style={{ fontFamily: TT.fontMono, fontSize: 8, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 10px', background: TT.yolk, color: TT.inkBlack, borderRadius: 2 }}>
                      Most Popular
                    </span>
                  </div>
                ) : null}

                <div style={{ marginBottom: 18 }}>
                  <div style={{ width: 34, height: 34, borderRadius: 2, background: highlighted ? TT.yolk : TT.inkRaised, border: `1px solid ${highlighted ? TT.yolk : TT.inkBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12 }}>
                    <Icon size={15} color={highlighted ? TT.inkBlack : TT.inkSubtle} />
                  </div>
                  <div style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow }}>{plan.name.toUpperCase()}</div>
                  <div style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, marginTop: 3, lineHeight: 1.5 }}>{plan.description}</div>
                </div>

                <div style={{ marginBottom: 18 }}>
                  <span style={{ fontFamily: TT.fontDisplay, fontSize: 40, letterSpacing: '0.02em', color: highlighted ? TT.yolk : TT.snow, lineHeight: 1 }}>
                    {plan.id === 'enterprise' ? 'Custom' : plan.id === 'free' ? 'Free' : `$${monthlyPrice}`}
                  </span>
                  {plan.price > 0 && plan.id !== 'enterprise' ? (
                    <span style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.04em', marginLeft: 6 }}>
                      {isAnnual ? '/mo billed annually' : plan.priceUnit}
                    </span>
                  ) : null}
                </div>

                <button
                  type="button"
                  disabled={false}
                  onClick={(event) => {
                    event.stopPropagation();
                    handlePlanAction(plan);
                  }}
                  style={{
                    width: '100%',
                    height: 38,
                    marginBottom: 20,
                    background: isCurrent ? TT.inkRaised : highlighted ? TT.yolk : 'transparent',
                    border: `2px solid ${isCurrent ? TT.inkBorder : highlighted ? TT.yolk : TT.inkBorder}`,
                    borderRadius: 3,
                    color: isCurrent ? TT.inkMuted : highlighted ? TT.inkBlack : TT.inkMuted,
                    fontFamily: TT.fontDisplay,
                    fontSize: 15,
                    letterSpacing: '0.1em',
                    textTransform: 'uppercase',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                  }}
                >
                  {isCurrent ? (
                    <>
                      <Check size={12} />
                      Current Plan
                    </>
                  ) : (
                    <>
                      {plan.cta}
                      <ArrowRight size={12} />
                    </>
                  )}
                </button>

                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 10 }}>
                    Features
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                    {plan.features.slice(0, 4).map((feature) => (
                      <div key={feature} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, marginTop: 6, flexShrink: 0, boxShadow: '0 0 4px rgba(245,230,66,0.6)' }} />
                        <span style={{ fontFamily: TT.fontBody, fontSize: 12, color: TT.inkSubtle, lineHeight: 1.5 }}>{feature}</span>
                      </div>
                    ))}
                    {plan.limitations.slice(0, 2).map((limitation) => (
                      <div key={limitation} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, opacity: 0.4 }}>
                        <div style={{ width: 4, height: 4, borderRadius: 1, border: `1px solid ${TT.inkMid}`, marginTop: 6, flexShrink: 0 }} />
                        <span style={{ fontFamily: TT.fontBody, fontSize: 12, color: TT.inkMuted, lineHeight: 1.5, textDecoration: 'line-through' }}>{limitation}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </motion.button>
            );
          })}
        </div>

        <aside
          style={{
            background: TT.inkDeep,
            border: `1px solid ${TT.inkBorder}`,
            borderLeft: `4px solid ${TT.yolk}`,
            borderRadius: 3,
            padding: '20px 18px',
            position: 'sticky',
            top: 20,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <Users size={14} color={TT.yolk} />
            <span style={{ fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.yolk }}>
              Plan Detail
            </span>
          </div>
          <h2 style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.05em', color: TT.snow, marginBottom: 4 }}>
            {selectedPlan.name.toUpperCase()}
          </h2>
          <p style={{ fontFamily: TT.fontBody, fontSize: 13, color: TT.inkMuted, lineHeight: 1.6, marginBottom: 16 }}>
            {selectedPlan.description}
          </p>

          <div style={{ display: 'grid', gap: 10, marginBottom: 18 }}>
            <div style={detailMetricStyle}>
              <span style={detailMetricLabelStyle}>Seats modeled</span>
              <span style={detailMetricValueStyle}>{teamSize}</span>
            </div>
            <div style={detailMetricStyle}>
              <span style={detailMetricLabelStyle}>Monthly spend</span>
              <span style={detailMetricValueStyle}>{selectedPlanMonthlyCost === null ? 'Custom' : `$${selectedPlanMonthlyCost}`}</span>
            </div>
            <div style={detailMetricStyle}>
              <span style={detailMetricLabelStyle}>Annual savings</span>
              <span style={detailMetricValueStyle}>{yearlySavings > 0 ? `$${yearlySavings}` : '—'}</span>
            </div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <div style={detailSectionLabelStyle}>Why this plan</div>
            <p style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkSubtle, lineHeight: 1.7 }}>
              {selectedPlan.id === recommendedPlan
                ? `This plan matches the current team and focus settings. ${recommendationReason}`
                : `Right now this plan is selected for closer inspection. The current recommendation is ${recommendedPlan.toUpperCase()}.`}
            </p>
          </div>

          <div style={{ marginBottom: 16 }}>
            <div style={detailSectionLabelStyle}>Included</div>
            <div style={{ display: 'grid', gap: 8 }}>
              {selectedPlan.features.map((feature) => (
                <div key={feature} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                  <Check size={12} color={TT.green} style={{ marginTop: 2, flexShrink: 0 }} />
                  <span style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkSubtle, lineHeight: 1.5 }}>{feature}</span>
                </div>
              ))}
            </div>
          </div>

          {selectedPlan.limitations.length > 0 ? (
            <div>
              <div style={detailSectionLabelStyle}>Tradeoffs</div>
              <div style={{ display: 'grid', gap: 8 }}>
                {selectedPlan.limitations.map((limitation) => (
                  <div key={limitation} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <span style={{ width: 12, color: TT.inkMuted, lineHeight: 1 }}>—</span>
                    <span style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.5 }}>{limitation}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </aside>
      </div>

      <div
        style={{
          background: TT.inkDeep,
          border: `1px solid ${TT.inkBorder}`,
          borderLeft: `4px solid ${TT.yolk}`,
          borderRadius: 3,
          padding: '28px 32px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 20,
          marginBottom: 34,
          maxWidth: 1100,
          marginInline: 'auto',
        }}
      >
        <div>
          <div style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.05em', color: TT.snow, marginBottom: 6 }}>
            NEED A CUSTOM SOLUTION?
          </div>
          <p style={{ fontFamily: TT.fontBody, fontSize: 13, color: TT.inkMuted, lineHeight: 1.6, maxWidth: 440 }}>
            We offer custom deployments, dedicated infrastructure, and enterprise-grade security. Use the recommendation controls above to decide whether it is time to escalate from Team.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCtaMessage('Enterprise conversation staged. This is the right path for custom deployment, SSO hardening, and SLA-backed support.')}
          style={{
            height: 42,
            padding: '0 22px',
            background: TT.yolk,
            border: `2px solid ${TT.yolk}`,
            borderRadius: 3,
            color: TT.inkBlack,
            fontFamily: TT.fontDisplay,
            fontSize: 16,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <Building2 size={15} />
          Contact Sales
        </button>
      </div>

      <div style={{ maxWidth: 760, margin: '0 auto 48px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <h2 style={{ fontFamily: TT.fontDisplay, fontSize: 32, letterSpacing: '0.05em', color: TT.snow, margin: 0 }}>
            <span style={{ color: TT.yolk }}>F</span>AQ
          </h2>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: TT.inkDeep,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 3,
              padding: '0 10px',
              minWidth: 260,
              height: 38,
            }}
          >
            <Search size={13} color={TT.inkMuted} />
            <input
              value={faqQuery}
              onChange={(event) => setFaqQuery(event.target.value)}
              placeholder="Search pricing questions"
              aria-label="Search pricing FAQs"
              style={{
                flex: 1,
                height: '100%',
                background: 'transparent',
                border: 'none',
                color: TT.snow,
                fontFamily: TT.fontBody,
                fontSize: 13,
                outline: 'none',
              }}
            />
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {filteredFaqs.length === 0 ? (
            <div style={{ background: TT.inkDeep, border: `1px dashed ${TT.inkBorder}`, borderRadius: 3, padding: '18px 16px', color: TT.inkMuted, fontSize: 12 }}>
              No FAQ entries match that search. Try a broader term like `plan`, `trial`, or `data`.
            </div>
          ) : (
            filteredFaqs.map((faq, index) => {
              const realIndex = faqs.findIndex((entry) => entry.question === faq.question);
              const expanded = expandedFaq === realIndex;
              return (
                <div
                  key={faq.question}
                  style={{
                    background: TT.inkDeep,
                    border: `1px solid ${expanded ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                    borderLeft: `3px solid ${expanded ? TT.yolk : 'transparent'}`,
                    borderRadius: 3,
                    overflow: 'hidden',
                  }}
                >
                  <button
                    type="button"
                    onClick={() => setExpandedFaq(expanded ? null : realIndex)}
                    style={{
                      width: '100%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '14px 16px',
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <span style={{ fontFamily: TT.fontMono, fontSize: 12, letterSpacing: '0.03em', color: TT.snow }}>{faq.question}</span>
                    <HelpCircle size={13} color={expanded ? TT.yolk : TT.inkMuted} style={{ flexShrink: 0, marginLeft: 12 }} />
                  </button>
                  {expanded ? (
                    <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} style={{ padding: '0 16px 14px' }}>
                      <p style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.7 }}>{faq.answer}</p>
                    </motion.div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>

      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 14 }}>
          Trusted by teams at
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 32 }}>
          {['Google', 'Microsoft', 'Amazon', 'Meta', 'Netflix'].map((company) => (
            <span key={company} style={{ fontFamily: TT.fontDisplay, fontSize: 18, letterSpacing: '0.08em', color: TT.inkBorder }}>
              {company.toUpperCase()}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

const summaryCardStyle: React.CSSProperties = {
  background: TT.inkDeep,
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 3,
  padding: '16px 18px',
};

const summaryLabelStyle: React.CSSProperties = {
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: TT.inkMuted,
  marginBottom: 8,
};

const summaryValueStyle: React.CSSProperties = {
  fontFamily: TT.fontDisplay,
  fontSize: 28,
  letterSpacing: '0.06em',
  color: TT.snow,
  lineHeight: 1,
  marginBottom: 6,
};

const summaryHelperStyle: React.CSSProperties = {
  fontFamily: TT.fontBody,
  fontSize: 12.5,
  color: TT.inkSubtle,
  lineHeight: 1.6,
  margin: 0,
};

const detailMetricStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 10,
  background: TT.inkRaised,
  border: `1px solid ${TT.inkBorder}`,
  borderRadius: 3,
  padding: '10px 12px',
};

const detailMetricLabelStyle: React.CSSProperties = {
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: TT.inkMuted,
};

const detailMetricValueStyle: React.CSSProperties = {
  fontFamily: TT.fontDisplay,
  fontSize: 20,
  letterSpacing: '0.05em',
  color: TT.snow,
};

const detailSectionLabelStyle: React.CSSProperties = {
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: TT.yolk,
  marginBottom: 8,
};
