import { useMemo, useState } from 'react';
import { Brain, Sparkles } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { OnboardingCurriculumView } from '@/components/notes/OnboardingCurriculumView';
import type { Note, OnboardingCurriculum } from '@/types';

const OA = {
  inkBlack: '#0A0A0A',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMuted: '#5A5A5A',
  inkSubtle: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

const ROLE_PRESETS = [
  { value: 'engineer', label: 'Engineer' },
  { value: 'product manager', label: 'Product Manager' },
  { value: 'designer', label: 'Designer' },
  { value: 'customer success', label: 'Customer Success' },
  { value: 'sales', label: 'Sales' },
  { value: 'manager', label: 'Manager' },
] as const;

interface OnboardingAcceleratorPanelProps {
  workspaceId?: string | null;
  workspaceName?: string | null;
  notes: Note[];
  canGenerate: boolean;
  onOpenNote: (noteId: string) => void;
}

export function OnboardingAcceleratorPanel({
  workspaceId,
  workspaceName,
  notes,
  canGenerate,
  onOpenNote,
}: OnboardingAcceleratorPanelProps) {
  const [roleInput, setRoleInput] = useState('engineer');
  const [curriculum, setCurriculum] = useState<OnboardingCurriculum | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const roleLabel = useMemo(() => {
    const preset = ROLE_PRESETS.find((item) => item.value === roleInput.toLowerCase().trim());
    return preset?.label || roleInput.trim() || 'Custom Role';
  }, [roleInput]);

  const handleGenerate = async () => {
    if (!workspaceId || !roleInput.trim() || !canGenerate) return;

    setLoading(true);
    setError(null);
    try {
      const result = await api.generateOnboardingCurriculum(workspaceId, roleInput.trim());
      setCurriculum(result);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('The onboarding plan could not be generated right now.');
      }
    } finally {
      setLoading(false);
    }
  };

  const helperMessage = !workspaceId
    ? 'Choose a workspace to generate a curriculum from real workspace notes.'
    : !canGenerate
      ? 'Your current role can view notes here, but it cannot generate onboarding plans for this workspace.'
      : 'Build a role-specific 4-week curriculum grounded in this workspace’s actual notes.';

  return (
    <div
      style={{
        background: OA.inkDeep,
        border: `1px solid ${OA.inkBorder}`,
        borderRadius: 3,
        padding: 18,
        marginBottom: 24,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Sparkles size={12} color={OA.yolk} />
            <span style={{ fontFamily: OA.fontMono, fontSize: 9, color: OA.yolk, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              Onboarding Accelerator
            </span>
          </div>
          <h2 style={{ fontFamily: OA.fontDisplay, fontSize: 30, color: OA.snow, letterSpacing: '0.06em', lineHeight: 0.95 }}>
            Grounded By Workspace Notes
          </h2>
          <p style={{ fontFamily: OA.fontBody, fontSize: 12, color: OA.inkSubtle, lineHeight: 1.65, marginTop: 10, maxWidth: 720 }}>
            {helperMessage}
          </p>
        </div>
        <div
          style={{
            minWidth: 220,
            background: OA.inkBlack,
            border: `1px solid ${OA.inkBorder}`,
            borderRadius: 3,
            padding: '12px 14px',
            alignSelf: 'flex-start',
          }}
        >
          <p style={{ fontFamily: OA.fontMono, fontSize: 9, color: OA.yolk, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
            Active Scope
          </p>
          <p style={{ fontFamily: OA.fontMono, fontSize: 10.5, color: OA.snow, marginBottom: 6 }}>
            {workspaceName || 'No workspace selected'}
          </p>
          <p style={{ fontFamily: OA.fontMono, fontSize: 10, color: OA.inkMuted, lineHeight: 1.55 }}>
            {notes.length} notes currently loaded in this view.
          </p>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        {ROLE_PRESETS.map((preset) => {
          const active = roleInput.toLowerCase().trim() === preset.value;
          return (
            <button
              key={preset.value}
              onClick={() => setRoleInput(preset.value)}
              style={{
                background: active ? 'rgba(245,230,66,0.08)' : OA.inkRaised,
                border: `1px solid ${active ? 'rgba(245,230,66,0.32)' : OA.inkBorder}`,
                borderRadius: 2,
                color: active ? OA.yolk : OA.inkSubtle,
                cursor: 'pointer',
                padding: '7px 10px',
                fontFamily: OA.fontMono,
                fontSize: 9.5,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
              }}
            >
              {preset.label}
            </button>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'stretch', marginBottom: 14 }}>
        <input
          value={roleInput}
          onChange={(event) => setRoleInput(event.target.value)}
          placeholder="Enter a target role"
          style={{
            flex: '1 1 260px',
            minWidth: 220,
            height: 42,
            background: OA.inkRaised,
            border: `1px solid ${OA.inkBorder}`,
            borderRadius: 3,
            color: OA.snow,
            fontFamily: OA.fontMono,
            fontSize: 12,
            letterSpacing: '0.03em',
            padding: '0 12px',
            outline: 'none',
          }}
        />
        <button
          onClick={handleGenerate}
          disabled={!workspaceId || !canGenerate || !roleInput.trim() || loading}
          style={{
            height: 42,
            padding: '0 18px',
            background: !workspaceId || !canGenerate || !roleInput.trim() || loading ? OA.inkRaised : OA.yolk,
            border: `2px solid ${!workspaceId || !canGenerate || !roleInput.trim() || loading ? OA.inkBorder : OA.yolk}`,
            borderRadius: 3,
            color: !workspaceId || !canGenerate || !roleInput.trim() || loading ? OA.inkMuted : OA.inkBlack,
            fontFamily: OA.fontDisplay,
            fontSize: 15,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            cursor: !workspaceId || !canGenerate || !roleInput.trim() || loading ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 7,
          }}
        >
          <Brain size={13} />
          {loading ? 'Generating' : curriculum ? 'Regenerate Plan' : 'Generate Plan'}
        </button>
      </div>

      <div style={{ marginBottom: 14 }}>
        <p style={{ fontFamily: OA.fontMono, fontSize: 9, color: OA.inkMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
          Target Role
        </p>
        <p style={{ fontFamily: OA.fontBody, fontSize: 11.5, color: OA.inkSubtle, lineHeight: 1.55 }}>
          The current curriculum request is tuned for <span style={{ color: OA.snow }}>{roleLabel}</span>. Retrieval blends semantic role queries with the workspace’s most accessed notes before generation.
        </p>
      </div>

      {error && (
        <div
          style={{
            marginBottom: 14,
            padding: '12px 14px',
            background: 'rgba(255,69,69,0.08)',
            border: '1px solid rgba(255,69,69,0.22)',
            borderRadius: 3,
            color: '#FFB4B4',
            fontFamily: OA.fontMono,
            fontSize: 10.5,
            lineHeight: 1.55,
          }}
        >
          {error}
        </div>
      )}

      {loading && (
        <div
          style={{
            marginBottom: 14,
            padding: '12px 14px',
            background: OA.inkBlack,
            border: `1px solid ${OA.inkBorder}`,
            borderRadius: 3,
            color: OA.inkSubtle,
            fontFamily: OA.fontMono,
            fontSize: 10.5,
            lineHeight: 1.55,
          }}
        >
          Ranking workspace notes, preparing grounded excerpts, and generating a 4-week plan.
        </div>
      )}

      {curriculum && !loading && (
        <OnboardingCurriculumView
          curriculum={curriculum}
          notes={notes}
          workspaceName={workspaceName}
          onOpenNote={onOpenNote}
        />
      )}
    </div>
  );
}
