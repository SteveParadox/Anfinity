import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Sparkles, Link2, TrendingUp, Lightbulb, Check, X as XIcon } from 'lucide-react';
import type { AIInsight } from '@/types';

interface AIInsightsPanelProps {
  insights: AIInsight[];
  isOpen: boolean;
  onClose: () => void;
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
  error:     '#FF4545',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

// Each insight type maps to: icon, accent color, bg, border
const insightConfig = {
  connection: {
    Icon: Link2,
    color: '#60A5FA',
    bg: 'rgba(96,165,250,0.07)',
    border: 'rgba(96,165,250,0.2)',
    label: 'Connection',
  },
  trend: {
    Icon: TrendingUp,
    color: TT.yolk,           // trends use brand yellow
    bg: 'rgba(245,230,66,0.07)',
    border: 'rgba(245,230,66,0.2)',
    label: 'Trend',
  },
  suggestion: {
    Icon: Lightbulb,
    color: '#FB923C',
    bg: 'rgba(251,146,60,0.07)',
    border: 'rgba(251,146,60,0.2)',
    label: 'Suggestion',
  },
  summary: {
    Icon: Sparkles,
    color: TT.yolk,
    bg: 'rgba(245,230,66,0.07)',
    border: 'rgba(245,230,66,0.2)',
    label: 'Summary',
  },
} as const;

export function AIInsightsPanel({ insights, isOpen, onClose }: AIInsightsPanelProps) {
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [accepted,  setAccepted]  = useState<string[]>([]);

  const visible = insights.filter(
    (i) => !dismissed.includes(i.id) && !accepted.includes(i.id)
  );

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Overlay */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              position: 'fixed', inset: 0,
              background: 'rgba(0,0,0,0.6)',
              zIndex: 50,
            }}
            className="lg:hidden"
            onClick={onClose}
          />

          {/* Panel */}
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 220 }}
            style={{
              position: 'fixed', right: 0, top: 0, bottom: 0,
              width: '100%', maxWidth: 420,
              background: TT.inkDeep,
              borderLeft: `1px solid ${TT.inkBorder}`,
              // Signature: right-panel gets a right-side yellow stripe
              borderRight: `3px solid ${TT.yolk}`,
              zIndex: 51,
              display: 'flex', flexDirection: 'column',
              fontFamily: TT.fontMono,
            }}
          >
            {/* ── Header ──────────────────────────────────────────── */}
            <div
              style={{
                height: 56,
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '0 20px',
                borderBottom: `1px solid ${TT.inkBorder}`,
                flexShrink: 0,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div
                  style={{
                    width: 28, height: 28, borderRadius: 3,
                    background: TT.yolk,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  <Sparkles size={13} color={TT.inkBlack} />
                </div>
                <div>
                  <div
                    style={{
                      fontFamily: TT.fontDisplay,
                      fontSize: 20,
                      letterSpacing: '0.08em',
                      color: TT.snow,
                      lineHeight: 1,
                    }}
                  >
                    AI <span style={{ color: TT.yolk }}>INSIGHTS</span>
                  </div>
                  <div
                    style={{
                      fontFamily: TT.fontMono,
                      fontSize: 9,
                      letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                      color: TT.inkMuted,
                      marginTop: 2,
                    }}
                  >
                    {visible.length} pending
                  </div>
                </div>
              </div>

              <button
                onClick={onClose}
                style={{
                  background: 'none', border: `1px solid ${TT.inkBorder}`,
                  borderRadius: 3, cursor: 'pointer', padding: '4px 6px',
                  color: TT.inkMuted, transition: 'all 0.15s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.color = TT.yolk;
                  (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
                  (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                }}
              >
                <X size={14} />
              </button>
            </div>

            {/* ── Content ─────────────────────────────────────────── */}
            <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
              {visible.length === 0 ? (
                /* Empty state */
                <div
                  style={{
                    display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center',
                    height: '100%', gap: 12, paddingTop: 60,
                  }}
                >
                  <div
                    style={{
                      width: 56, height: 56, borderRadius: 3,
                      background: TT.inkRaised,
                      border: `1px solid ${TT.inkBorder}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}
                  >
                    <Sparkles size={22} color={TT.inkMid} />
                  </div>
                  <span
                    style={{
                      fontFamily: TT.fontDisplay,
                      fontSize: 22, letterSpacing: '0.06em',
                      color: TT.snow,
                    }}
                  >
                    ALL CLEAR
                  </span>
                  <span
                    style={{
                      fontFamily: TT.fontMono,
                      fontSize: 10, color: TT.inkMuted,
                      letterSpacing: '0.05em', textTransform: 'uppercase',
                      textAlign: 'center', maxWidth: 220, lineHeight: 1.6,
                    }}
                  >
                    No new insights right now. Check back later.
                  </span>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {visible.map((insight, index) => {
                    const cfg = insightConfig[insight.type] ?? insightConfig.summary;
                    const { Icon } = cfg;

                    return (
                      <motion.div
                        key={insight.id}
                        initial={{ opacity: 0, y: 14 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: index * 0.07 }}
                        style={{
                          background: TT.inkBlack,
                          border: `1px solid ${TT.inkBorder}`,
                          borderLeft: `3px solid ${cfg.color}`,
                          borderRadius: 3,
                          padding: '14px 14px',
                        }}
                      >
                        {/* Type row */}
                        <div
                          style={{
                            display: 'flex', alignItems: 'center',
                            justifyContent: 'space-between',
                            marginBottom: 10,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                            <div
                              style={{
                                width: 24, height: 24, borderRadius: 2,
                                background: cfg.bg,
                                border: `1px solid ${cfg.border}`,
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                flexShrink: 0,
                              }}
                            >
                              <Icon size={12} color={cfg.color} />
                            </div>
                            <span
                              style={{
                                fontFamily: TT.fontMono,
                                fontSize: 9, letterSpacing: '0.1em',
                                textTransform: 'uppercase',
                                color: cfg.color,
                              }}
                            >
                              {cfg.label}
                            </span>
                          </div>
                          <span
                            style={{
                              fontFamily: TT.fontMono,
                              fontSize: 9, color: TT.inkMuted,
                              letterSpacing: '0.05em',
                            }}
                          >
                            {Math.round(insight.confidence * 100)}% conf
                          </span>
                        </div>

                        {/* Content */}
                        <p
                          style={{
                            fontFamily: TT.fontBody,
                            fontSize: 12.5, lineHeight: 1.65,
                            color: TT.inkSubtle,
                          }}
                        >
                          {insight.content}
                        </p>

                        {/* Date + sources */}
                        <div style={{ marginTop: 10, marginBottom: 10 }}>
                          <span
                            style={{
                              fontFamily: TT.fontMono,
                              fontSize: 9, color: TT.inkMid,
                              letterSpacing: '0.04em',
                            }}
                          >
                            {new Date(insight.createdAt).toLocaleDateString()}
                          </span>
                          {insight.sources?.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 7 }}>
                              {insight.sources.map((src) => (
                                <span
                                  key={src}
                                  style={{
                                    fontFamily: TT.fontMono,
                                    fontSize: 8.5,
                                    letterSpacing: '0.04em',
                                    padding: '1px 6px',
                                    background: TT.inkRaised,
                                    border: `1px solid ${TT.inkBorder}`,
                                    borderRadius: 2,
                                    color: TT.inkMuted,
                                    textTransform: 'uppercase',
                                  }}
                                >
                                  {src}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>

                        {/* Actions */}
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button
                            onClick={() => setAccepted((p) => [...p, insight.id])}
                            style={{
                              flex: 1, height: 32,
                              background: TT.yolk,
                              border: `2px solid ${TT.yolk}`,
                              borderRadius: 3,
                              color: TT.inkBlack,
                              fontFamily: TT.fontDisplay,
                              fontSize: 13, letterSpacing: '0.1em',
                              textTransform: 'uppercase',
                              cursor: 'pointer',
                              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                              transition: 'all 0.15s',
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
                            <Check size={12} />
                            Apply
                          </button>
                          <button
                            onClick={() => setDismissed((p) => [...p, insight.id])}
                            style={{
                              flex: 1, height: 32,
                              background: 'transparent',
                              border: `1px solid ${TT.inkBorder}`,
                              borderRadius: 3,
                              color: TT.inkMuted,
                              fontFamily: TT.fontDisplay,
                              fontSize: 13, letterSpacing: '0.1em',
                              textTransform: 'uppercase',
                              cursor: 'pointer',
                              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                              transition: 'all 0.15s',
                            }}
                            onMouseEnter={(e) => {
                              (e.currentTarget as HTMLElement).style.borderColor = `rgba(255,69,69,0.4)`;
                              (e.currentTarget as HTMLElement).style.color = TT.error;
                            }}
                            onMouseLeave={(e) => {
                              (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                              (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
                            }}
                          >
                            <XIcon size={12} />
                            Dismiss
                          </button>
                        </div>
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* ── Footer ──────────────────────────────────────────── */}
            <div
              style={{
                padding: '10px 20px',
                borderTop: `1px solid ${TT.inkBorder}`,
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  fontFamily: TT.fontMono,
                  fontSize: 8.5, letterSpacing: '0.07em',
                  textTransform: 'uppercase', color: TT.inkMid,
                }}
              >
                Powered by <span style={{ color: TT.yolk }}>CogniFlow AI</span>
              </span>
              <span
                style={{
                  fontFamily: TT.fontMono,
                  fontSize: 8.5, color: TT.inkMid,
                  letterSpacing: '0.05em',
                }}
              >
                v2.1.0
              </span>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}