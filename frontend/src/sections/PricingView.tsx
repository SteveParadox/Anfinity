import { useState } from 'react';
import { motion } from 'framer-motion';
import { Check, Sparkles, Zap, Crown, Building2, ArrowRight, HelpCircle } from 'lucide-react';
import type { PricingPlan } from '@/types';

interface PricingViewProps {
  currentPlan: 'free' | 'pro' | 'team' | 'enterprise';
}

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
  yolkMuted: '#C4B830',
  error:     '#FF4545',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

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
  { question: 'Can I switch plans anytime?',       answer: "Yes, you can upgrade or downgrade your plan at any time. Changes take effect immediately, and we'll prorate any differences." },
  { question: 'What happens to my data if I cancel?', answer: 'Your data remains accessible in read-only mode for 30 days. After that, you can export everything or reactivate your account.' },
  { question: 'Is there a free trial for paid plans?', answer: 'Yes! Pro and Team plans come with a 14-day free trial. No credit card required to start.' },
  { question: 'How does AI search work?',           answer: 'Our AI understands natural language queries and finds semantically related content, not just keyword matches. It learns from your usage patterns to improve over time.' },
];

const planIcons: Record<string, React.ElementType> = {
  free: Zap, pro: Sparkles, team: Crown, enterprise: Building2,
};

export function PricingView({ currentPlan }: PricingViewProps) {
  const [isAnnual,    setIsAnnual]    = useState(true);
  const [expandedFaq, setExpandedFaq] = useState<number | null>(null);

  const getPrice = (plan: PricingPlan): string => {
    if (plan.id === 'enterprise') return 'Custom';
    if (plan.id === 'free')       return 'Free';
    const price = isAnnual ? plan.price * 2.5 : plan.price;
    return `$${price}`;
  };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>

      {/* ── Header ──────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} style={{ textAlign: 'center', marginBottom: 36 }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '3px 12px', background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2, marginBottom: 18 }}>
          <Sparkles size={10} color={TT.yolk} />
          <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.yolk }}>Simple Pricing</span>
        </div>

        <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 52, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase', marginBottom: 12 }}>
          <span style={{ color: TT.yolk }}>C</span>HOOSE YOUR PLAN
        </h1>
        <div style={{ width: 36, height: 3, background: TT.yolk, margin: '12px auto 16px' }} />
        <p style={{ fontFamily: TT.fontBody, fontSize: 14, color: TT.inkMuted, maxWidth: 480, margin: '0 auto 24px', lineHeight: 1.65 }}>
          Start free and scale as you grow. All plans include core knowledge management features.
        </p>

        {/* Billing toggle */}
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 12, padding: '8px 16px', background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3 }}>
          <span style={{ fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.07em', textTransform: 'uppercase', color: !isAnnual ? TT.snow : TT.inkMuted }}>Monthly</span>
          <button
            onClick={() => setIsAnnual((a) => !a)}
            style={{
              width: 40, height: 22, borderRadius: 11,
              background: isAnnual ? TT.yolk : TT.inkMid,
              border: 'none', cursor: 'pointer', position: 'relative', transition: 'background 0.2s',
            }}
          >
            <span style={{ position: 'absolute', top: 3, left: isAnnual ? 20 : 3, width: 16, height: 16, borderRadius: '50%', background: isAnnual ? TT.inkBlack : TT.inkMuted, transition: 'left 0.2s' }} />
          </button>
          <span style={{ fontFamily: TT.fontMono, fontSize: 10, letterSpacing: '0.07em', textTransform: 'uppercase', color: isAnnual ? TT.snow : TT.inkMuted }}>
            Annual
          </span>
          {isAnnual && (
            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.07em', textTransform: 'uppercase', padding: '2px 7px', background: 'rgba(245,230,66,0.1)', color: TT.yolk, border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2 }}>
              Save 20%
            </span>
          )}
        </div>
      </motion.div>

      {/* ── Plan cards ──────────────────────────────────────────── */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 240px))',
            gap: 24,
            justifyContent: 'center',
            margin: '0 auto 32px',
            maxWidth: 1100,
          }}
        >     
           {plans.map((plan, index) => {
          const isCurrent   = currentPlan === plan.id;
          const highlighted = !!plan.highlighted;
          const Icon        = planIcons[plan.id] ?? Zap;

          return (
            <motion.div
              key={plan.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.08 }}
              style={{ position: 'relative', marginTop: highlighted ? 0 : 16 }}
            >
              {/* Most popular label */}
              {highlighted && (
                <div style={{ position: 'absolute', top: -12, left: '50%', transform: 'translateX(-50%)', zIndex: 1 }}>
                  <span style={{ fontFamily: TT.fontMono, fontSize: 8, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 10px', background: TT.yolk, color: TT.inkBlack, borderRadius: 2 }}>
                    Most Popular
                  </span>
                </div>
              )}

              <div
                style={{
                  background: highlighted ? 'rgba(245,230,66,0.04)' : TT.inkDeep,
                  border: `1px solid ${highlighted ? 'rgba(245,230,66,0.3)' : isCurrent ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                  borderTop: `3px solid ${highlighted ? TT.yolk : isCurrent ? TT.yolk : TT.inkBorder}`,
                  borderRadius: 3,
                  padding: '22px 20px',
                  height: '100%',
                  boxSizing: 'border-box',
                  display: 'flex', flexDirection: 'column',
                }}
              >
                {/* Plan header */}
                <div style={{ marginBottom: 18 }}>
                  <div style={{ width: 34, height: 34, borderRadius: 2, background: highlighted ? TT.yolk : TT.inkRaised, border: `1px solid ${highlighted ? TT.yolk : TT.inkBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12 }}>
                    <Icon size={15} color={highlighted ? TT.inkBlack : TT.inkSubtle} />
                  </div>
                  <div style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow }}>{plan.name.toUpperCase()}</div>
                  <div style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, marginTop: 3, lineHeight: 1.5 }}>{plan.description}</div>
                </div>

                {/* Price */}
                <div style={{ marginBottom: 18 }}>
                  <span style={{ fontFamily: TT.fontDisplay, fontSize: 40, letterSpacing: '0.02em', color: highlighted ? TT.yolk : TT.snow, lineHeight: 1 }}>
                    {getPrice(plan)}
                  </span>
                  {plan.price > 0 && plan.id !== 'enterprise' && (
                    <span style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.04em', marginLeft: 6 }}>
                      {isAnnual ? '/mo billed annually' : plan.priceUnit}
                    </span>
                  )}
                </div>

                {/* CTA */}
                <button
                  disabled={isCurrent}
                  style={{
                    width: '100%', height: 38, marginBottom: 20,
                    background: isCurrent ? TT.inkRaised : highlighted ? TT.yolk : 'transparent',
                    border: `2px solid ${isCurrent ? TT.inkBorder : highlighted ? TT.yolk : TT.inkBorder}`,
                    borderRadius: 3,
                    color: isCurrent ? TT.inkMuted : highlighted ? TT.inkBlack : TT.inkMuted,
                    fontFamily: TT.fontDisplay,
                    fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase',
                    cursor: isCurrent ? 'not-allowed' : 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    if (!isCurrent) {
                      if (highlighted) {
                        (e.currentTarget as HTMLElement).style.background = TT.yolkBright;
                        (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright;
                      } else {
                        (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
                        (e.currentTarget as HTMLElement).style.color = TT.yolk;
                      }
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isCurrent) {
                      (e.currentTarget as HTMLElement).style.background = highlighted ? TT.yolk : 'transparent';
                      (e.currentTarget as HTMLElement).style.borderColor = highlighted ? TT.yolk : TT.inkBorder;
                      (e.currentTarget as HTMLElement).style.color = highlighted ? TT.inkBlack : TT.inkMuted;
                    }
                  }}
                >
                  {isCurrent ? (
                    <><Check size={12} /> Current Plan</>
                  ) : (
                    <>{plan.cta} <ArrowRight size={12} /></>
                  )}
                </button>

                {/* Features */}
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 10 }}>Features</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                    {plan.features.map((f, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, marginTop: 6, flexShrink: 0, boxShadow: '0 0 4px rgba(245,230,66,0.6)' }} />
                        <span style={{ fontFamily: TT.fontBody, fontSize: 12, color: TT.inkSubtle, lineHeight: 1.5 }}>{f}</span>
                      </div>
                    ))}
                    {plan.limitations.map((l, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, opacity: 0.35 }}>
                        <div style={{ width: 4, height: 4, borderRadius: 1, border: `1px solid ${TT.inkMid}`, marginTop: 6, flexShrink: 0 }} />
                        <span style={{ fontFamily: TT.fontBody, fontSize: 12, color: TT.inkMuted, lineHeight: 1.5, textDecoration: 'line-through' }}>{l}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>

      {/* ── Enterprise CTA ──────────────────────────────────────── */}
      <div
        style={{
          background: TT.inkDeep,
          border: `1px solid ${TT.inkBorder}`,
          borderLeft: `4px solid ${TT.yolk}`,
          borderRadius: 3,
          padding: '28px 32px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexWrap: 'wrap', gap: 20,
          marginBottom: 40,
        }}
      >
        <div>
          <div style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.05em', color: TT.snow, marginBottom: 6 }}>
            NEED A CUSTOM SOLUTION?
          </div>
          <p style={{ fontFamily: TT.fontBody, fontSize: 13, color: TT.inkMuted, lineHeight: 1.6, maxWidth: 440 }}>
            We offer custom deployments, dedicated infrastructure, and enterprise-grade security.
          </p>
        </div>
        <button
          style={{
            height: 42, padding: '0 22px',
            background: TT.yolk, border: `2px solid ${TT.yolk}`, borderRadius: 3,
            color: TT.inkBlack, fontFamily: TT.fontDisplay,
            fontSize: 16, letterSpacing: '0.1em', textTransform: 'uppercase',
            cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
            transition: 'all 0.15s',
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = TT.yolkBright; (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = TT.yolk; }}
        >
          <Building2 size={15} /> Contact Sales
        </button>
      </div>

      {/* ── FAQ ─────────────────────────────────────────────────── */}
      <div style={{ maxWidth: 680, margin: '0 auto', marginBottom: 48 }}>
        <h2 style={{ fontFamily: TT.fontDisplay, fontSize: 32, letterSpacing: '0.05em', color: TT.snow, textAlign: 'center', marginBottom: 20 }}>
          <span style={{ color: TT.yolk }}>F</span>AQ
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {faqs.map((faq, index) => (
            <div
              key={index}
              style={{
                background: TT.inkDeep,
                border: `1px solid ${expandedFaq === index ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                borderLeft: `3px solid ${expandedFaq === index ? TT.yolk : 'transparent'}`,
                borderRadius: 3,
                overflow: 'hidden',
                transition: 'border-color 0.15s',
              }}
            >
              <button
                onClick={() => setExpandedFaq(expandedFaq === index ? null : index)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '14px 16px', background: 'none', border: 'none', cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <span style={{ fontFamily: TT.fontMono, fontSize: 12, letterSpacing: '0.03em', color: TT.snow }}>{faq.question}</span>
                <HelpCircle size={13} color={expandedFaq === index ? TT.yolk : TT.inkMuted} style={{ flexShrink: 0, marginLeft: 12 }} />
              </button>
              {expandedFaq === index && (
                <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} style={{ padding: '0 16px 14px' }}>
                  <p style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.7 }}>{faq.answer}</p>
                </motion.div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── Trust bar ──────────────────────────────────────────────── */}
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMid, marginBottom: 14 }}>Trusted by teams at</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 32 }}>
          {['Google', 'Microsoft', 'Amazon', 'Meta', 'Netflix'].map((co) => (
            <span key={co} style={{ fontFamily: TT.fontDisplay, fontSize: 18, letterSpacing: '0.08em', color: TT.inkBorder }}>{co.toUpperCase()}</span>
          ))}
        </div>
      </div>
    </div>
  );
}