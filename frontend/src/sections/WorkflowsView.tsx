import { useState } from 'react';
import { motion } from 'framer-motion';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  Zap, Plus, Edit2, Trash2, Clock, FileText, Bell,
  Webhook, Brain, ArrowRight, CheckCircle2,
} from 'lucide-react';
import type { Workflow } from '@/types';

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

const mockWorkflows: Workflow[] = [
  {
    id: 'wf-1',
    name: 'Auto-summarize Daily Notes',
    trigger: { type: 'scheduled', config: { cron: '0 18 * * *' } },
    actions: [
      { type: 'ai-summarize', config: {} },
      { type: 'send-notification', config: { channel: 'email' } },
    ],
    isActive: true,
    createdAt: new Date('2025-02-10'),
  },
  {
    id: 'wf-2',
    name: 'New Note to Slack',
    trigger: { type: 'note-created', config: { workspace: 'ws-1' } },
    actions: [{ type: 'webhook', config: { url: 'https://hooks.slack.com/...' } }],
    isActive: true,
    createdAt: new Date('2025-02-12'),
  },
  {
    id: 'wf-3',
    name: 'Weekly Knowledge Report',
    trigger: { type: 'scheduled', config: { cron: '0 9 * * 1' } },
    actions: [
      { type: 'export', config: { format: 'pdf' } },
      { type: 'send-notification', config: { channel: 'email' } },
    ],
    isActive: false,
    createdAt: new Date('2025-02-14'),
  },
];

const triggerIcons: Record<string, React.ElementType> = {
  'note-created': FileText,
  'tag-added':    CheckCircle2,
  'scheduled':    Clock,
  'webhook':      Webhook,
};

const actionIcons: Record<string, React.ElementType> = {
  'send-notification': Bell,
  'create-task':       CheckCircle2,
  'export':            FileText,
  'webhook':           Webhook,
  'ai-summarize':      Brain,
};

const templates = [
  { name: 'Daily Summary', description: 'Get AI summaries of your daily notes', Icon: Brain    },
  { name: 'Team Sync',     description: 'Share new notes with your team on Slack', Icon: Bell  },
  { name: 'Weekly Export', description: 'Export your knowledge base weekly',    Icon: FileText },
];

/* Toggle switch — pure CSS, no Switch component */
function TTToggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      onClick={onChange}
      style={{
        width: 36, height: 20,
        borderRadius: 10,
        background: checked ? TT.yolk : TT.inkMid,
        border: 'none',
        cursor: 'pointer',
        position: 'relative',
        transition: 'background 0.2s',
        flexShrink: 0,
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 2, left: checked ? 18 : 2,
          width: 16, height: 16,
          borderRadius: '50%',
          background: checked ? TT.inkBlack : TT.inkMuted,
          transition: 'left 0.2s',
        }}
      />
    </button>
  );
}

function SmallBtn({ onClick, danger, children }: { onClick?: () => void; danger?: boolean; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="wf-action"
      style={{
        background: 'none', border: `1px solid ${TT.inkBorder}`,
        borderRadius: 2, cursor: 'pointer', padding: '4px 6px',
        color: TT.inkMuted, display: 'flex', alignItems: 'center',
        transition: 'all 0.15s', opacity: 0,
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.color = danger ? TT.error : TT.yolk;
        (e.currentTarget as HTMLElement).style.borderColor = danger ? 'rgba(255,69,69,0.3)' : 'rgba(245,230,66,0.3)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
        (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
      }}
    >
      {children}
    </button>
  );
}

export function WorkflowsView() {
  const [workflows, setWorkflows] = useState<Workflow[]>(mockWorkflows);
  const [isCreating, setIsCreating] = useState(false);

  const toggleWorkflow = (id: string) =>
    setWorkflows((prev) => prev.map((wf) => (wf.id === id ? { ...wf, isActive: !wf.isActive } : wf)));

  const deleteWorkflow = (id: string) =>
    setWorkflows((prev) => prev.filter((wf) => wf.id !== id));

  const stats = [
    { label: 'Total',       value: workflows.length                            },
    { label: 'Active',      value: workflows.filter((w) => w.isActive).length  },
    { label: 'Runs / mo',   value: '1,247'                                     },
    { label: 'Success',     value: '98.5%'                                     },
  ];

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>

      {/* ── CSS to reveal action buttons on card hover ─────────── */}
      <style>{`
        .wf-card:hover .wf-action { opacity: 1 !important; }
      `}</style>

      {/* ── Header ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 28, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Automation</span>
          </div>
          <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 44, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase' }}>
            <span style={{ color: TT.yolk }}>W</span>ORKFLOWS
          </h1>
          <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
        </div>
        <button
          onClick={() => setIsCreating(true)}
          style={{
            height: 38, padding: '0 18px',
            background: TT.yolk, border: `2px solid ${TT.yolk}`, borderRadius: 3,
            color: TT.inkBlack, fontFamily: TT.fontDisplay, fontSize: 15,
            letterSpacing: '0.1em', textTransform: 'uppercase',
            cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, transition: 'all 0.15s',
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = TT.yolkBright; (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = TT.yolk; }}
        >
          <Plus size={13} /> Create Workflow
        </button>
      </div>

      {/* ── Stats ───────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 24 }}>
        {stats.map(({ label, value }, i) => (
          <div
            key={label}
            style={{
              background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`,
              borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '12px 16px',
            }}
          >
            <div style={{ fontFamily: TT.fontDisplay, fontSize: 32, color: i === 1 ? TT.yolk : TT.snow, letterSpacing: '0.02em', lineHeight: 1 }}>{value}</div>
            <div style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginTop: 3 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* ── Workflow list ────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 32 }}>
        {workflows.map((wf, index) => {
          const TriggerIcon = triggerIcons[wf.trigger.type] ?? Zap;
          return (
            <motion.div
              key={wf.id}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.07 }}
            >
              <div
                className="wf-card"
                style={{
                  background: TT.inkDeep,
                  border: `1px solid ${TT.inkBorder}`,
                  borderLeft: `3px solid ${wf.isActive ? TT.yolk : TT.inkBorder}`,
                  borderRadius: 3,
                  padding: '14px 16px',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  gap: 16, transition: 'border-color 0.15s',
                }}
              >
                {/* Left: icon + info */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                  <div
                    style={{
                      width: 38, height: 38, borderRadius: 3,
                      background: wf.isActive ? 'rgba(245,230,66,0.08)' : TT.inkRaised,
                      border: `1px solid ${wf.isActive ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0,
                    }}
                  >
                    <Zap size={16} color={wf.isActive ? TT.yolk : TT.inkMuted} />
                  </div>

                  <div>
                    {/* Name + status chip */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                      <span style={{ fontFamily: TT.fontMono, fontSize: 13, fontWeight: 500, color: TT.snow, letterSpacing: '0.02em' }}>
                        {wf.name}
                      </span>
                      <span
                        style={{
                          fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase',
                          padding: '1px 6px',
                          background: wf.isActive ? 'rgba(245,230,66,0.08)' : TT.inkRaised,
                          color: wf.isActive ? TT.yolk : TT.inkMuted,
                          border: `1px solid ${wf.isActive ? 'rgba(245,230,66,0.2)' : TT.inkBorder}`,
                          borderRadius: 2,
                        }}
                      >
                        {wf.isActive ? 'Active' : 'Paused'}
                      </span>
                    </div>

                    {/* Trigger → Actions pipeline */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: TT.inkMuted }}>
                        <TriggerIcon size={11} />
                        <span style={{ fontSize: 10, letterSpacing: '0.05em', textTransform: 'capitalize' }}>
                          {wf.trigger.type.replace('-', ' ')}
                        </span>
                      </div>
                      <ArrowRight size={10} color={TT.inkMid} />
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {wf.actions.map((action, i) => {
                          const ActionIcon = actionIcons[action.type] ?? Zap;
                          return (
                            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, color: TT.inkMuted }}>
                              <ActionIcon size={11} />
                              <span style={{ fontSize: 10, letterSpacing: '0.04em', textTransform: 'capitalize' }}>
                                {action.type.replace('-', ' ')}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Right: toggle + actions */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  <TTToggle checked={wf.isActive} onChange={() => toggleWorkflow(wf.id)} />
                  <SmallBtn onClick={() => {}}><Edit2 size={11} /></SmallBtn>
                  <SmallBtn danger onClick={() => deleteWorkflow(wf.id)}><Trash2 size={11} /></SmallBtn>
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>

      {/* ── Empty state ──────────────────────────────────────────── */}
      {workflows.length === 0 && (
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <div style={{ width: 56, height: 56, borderRadius: 3, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
            <Zap size={22} color={TT.inkMid} />
          </div>
          <div style={{ fontFamily: TT.fontDisplay, fontSize: 26, letterSpacing: '0.06em', color: TT.snow, marginBottom: 8 }}>
            NO WORKFLOWS YET
          </div>
          <p style={{ fontSize: 10.5, letterSpacing: '0.05em', color: TT.inkMuted, textTransform: 'uppercase', marginBottom: 20 }}>
            Automate your knowledge management
          </p>
          <button
            onClick={() => setIsCreating(true)}
            style={{ height: 38, padding: '0 18px', background: TT.yolk, border: `2px solid ${TT.yolk}`, borderRadius: 3, color: TT.inkBlack, fontFamily: TT.fontDisplay, fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <Plus size={13} /> Create Workflow
          </button>
        </div>
      )}

      {/* ── Templates ───────────────────────────────────────────── */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block' }} />
          <span style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>Templates</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
          {templates.map(({ name, description, Icon }) => (
            <div
              key={name}
              onClick={() => setIsCreating(true)}
              style={{
                background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3,
                padding: '16px', cursor: 'pointer', transition: 'border-color 0.15s, background 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.22)';
                (e.currentTarget as HTMLElement).style.background = 'rgba(245,230,66,0.03)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                (e.currentTarget as HTMLElement).style.background = TT.inkDeep;
              }}
            >
              <div style={{ width: 30, height: 30, borderRadius: 2, background: 'rgba(245,230,66,0.08)', border: '1px solid rgba(245,230,66,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 10 }}>
                <Icon size={14} color={TT.yolk} />
              </div>
              <div style={{ fontFamily: TT.fontMono, fontSize: 12, color: TT.snow, marginBottom: 5, letterSpacing: '0.02em' }}>{name}</div>
              <div style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, lineHeight: 1.5 }}>{description}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Create dialog ────────────────────────────────────────── */}
      <Dialog open={isCreating} onOpenChange={setIsCreating}>
        <DialogContent
          style={{
            background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`,
            borderTop: `3px solid ${TT.yolk}`, borderRadius: 4,
            maxWidth: 480, fontFamily: TT.fontMono, color: TT.snow,
          }}
        >
          <DialogHeader>
            <DialogTitle style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.06em', color: TT.snow }}>
              <span style={{ color: TT.yolk }}>N</span>EW WORKFLOW
            </DialogTitle>
          </DialogHeader>
          <div style={{ padding: '24px 0 8px', textAlign: 'center' }}>
            <div style={{ width: 52, height: 52, borderRadius: 3, background: 'rgba(245,230,66,0.08)', border: '1px solid rgba(245,230,66,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
              <Zap size={22} color={TT.yolk} />
            </div>
            <div style={{ fontFamily: TT.fontDisplay, fontSize: 22, letterSpacing: '0.06em', color: TT.snow, marginBottom: 10 }}>COMING SOON</div>
            <p style={{ fontFamily: TT.fontBody, fontSize: 13, color: TT.inkMuted, lineHeight: 1.65, maxWidth: 320, margin: '0 auto 24px' }}>
              The workflow builder is being enhanced with AI capabilities. Stay tuned for the full release.
            </p>
            <button
              onClick={() => setIsCreating(false)}
              style={{ height: 38, padding: '0 24px', background: 'transparent', border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.inkMuted, fontFamily: TT.fontDisplay, fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase', cursor: 'pointer', transition: 'all 0.15s' }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; (e.currentTarget as HTMLElement).style.color = TT.yolk; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; (e.currentTarget as HTMLElement).style.color = TT.inkMuted; }}
            >
              Got It
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}