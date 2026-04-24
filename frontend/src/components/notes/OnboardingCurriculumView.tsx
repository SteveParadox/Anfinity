import type { Note, OnboardingCurriculum } from '@/types';

const OC = {
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

const confidenceTone: Record<'low' | 'medium' | 'high', { label: string; color: string; border: string }> = {
  low: {
    label: 'Low grounding confidence',
    color: '#FCA5A5',
    border: 'rgba(252,165,165,0.25)',
  },
  medium: {
    label: 'Medium grounding confidence',
    color: '#FCD34D',
    border: 'rgba(252,211,77,0.25)',
  },
  high: {
    label: 'High grounding confidence',
    color: '#86EFAC',
    border: 'rgba(134,239,172,0.25)',
  },
};

interface OnboardingCurriculumViewProps {
  curriculum: OnboardingCurriculum;
  notes: Note[];
  workspaceName?: string | null;
  onOpenNote?: (noteId: string) => void;
}

function dedupe(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean)));
}

export function OnboardingCurriculumView({
  curriculum,
  notes,
  workspaceName,
  onOpenNote,
}: OnboardingCurriculumViewProps) {
  const noteMap = new Map(notes.map((note) => [note.id, note]));
  const candidateMap = new Map(curriculum.candidateNotes.map((note) => [note.noteId, note]));
  const confidence = confidenceTone[curriculum.grounding.groundingConfidence] || confidenceTone.low;
  const sourceNoteIds = dedupe([
    ...curriculum.grounding.usedNoteIds,
    ...curriculum.weeks.flatMap((week) => week.supportNoteIds),
  ]).filter((noteId) => noteMap.has(noteId) || candidateMap.has(noteId)).slice(0, 12);

  const resolveNoteTitle = (noteId: string, fallbackTitle?: string) =>
    noteMap.get(noteId)?.title || fallbackTitle || candidateMap.get(noteId)?.title || 'Workspace note';

  return (
    <div
      style={{
        background: OC.inkDeep,
        border: `1px solid ${OC.inkBorder}`,
        borderRadius: 3,
        padding: 18,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.yolk, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
            Onboarding Accelerator
          </p>
          <h3 style={{ fontFamily: OC.fontDisplay, fontSize: 28, letterSpacing: '0.06em', color: OC.snow, lineHeight: 0.95 }}>
            {curriculum.role}
          </h3>
          <p style={{ fontFamily: OC.fontBody, fontSize: 12, color: OC.inkSubtle, lineHeight: 1.65, marginTop: 10, maxWidth: 760 }}>
            {curriculum.summary}
          </p>
        </div>
        <div
          style={{
            minWidth: 220,
            background: OC.inkRaised,
            border: `1px solid ${confidence.border}`,
            borderRadius: 3,
            padding: '12px 14px',
            alignSelf: 'flex-start',
          }}
        >
          <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: confidence.color, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
            {confidence.label}
          </p>
          <p style={{ fontFamily: OC.fontMono, fontSize: 10, color: OC.inkSubtle, lineHeight: 1.6 }}>
            Grounded in {curriculum.grounding.modelCandidateNoteCount} candidate notes
            {workspaceName ? ` from ${workspaceName}` : ''}.
          </p>
          <p style={{ fontFamily: OC.fontMono, fontSize: 10, color: OC.inkMuted, marginTop: 8, lineHeight: 1.6 }}>
            Role queries: {curriculum.grounding.roleQueries.slice(0, 3).join(' • ') || 'None'}
          </p>
        </div>
      </div>

      {curriculum.grounding.warnings.length > 0 && (
        <div
          style={{
            marginBottom: 14,
            padding: '12px 14px',
            background: 'rgba(245,230,66,0.04)',
            border: '1px solid rgba(245,230,66,0.16)',
            borderLeft: `3px solid ${OC.yolk}`,
            borderRadius: 3,
          }}
        >
          {curriculum.grounding.warnings.map((warning) => (
            <p key={warning} style={{ fontFamily: OC.fontBody, fontSize: 11.5, color: OC.inkSubtle, lineHeight: 1.55 }}>
              {warning}
            </p>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
        {curriculum.weeks.map((week) => (
          <div
            key={week.weekNumber}
            style={{
              background: OC.inkBlack,
              border: `1px solid ${OC.inkBorder}`,
              borderTop: `2px solid ${OC.yolk}`,
              borderRadius: 3,
              padding: '14px 14px 16px',
            }}
          >
            <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.yolk, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
              Week {week.weekNumber}
            </p>
            <h4 style={{ fontFamily: OC.fontMono, fontSize: 13, color: OC.snow, letterSpacing: '0.02em', marginBottom: 10 }}>
              {week.theme}
            </h4>

            <div style={{ marginBottom: 12 }}>
              <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.inkMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
                Objectives
              </p>
              {week.objectives.map((objective) => (
                <p key={objective} style={{ fontFamily: OC.fontBody, fontSize: 11.5, color: OC.inkSubtle, lineHeight: 1.55, marginBottom: 5 }}>
                  {objective}
                </p>
              ))}
            </div>

            <div style={{ marginBottom: 12 }}>
              <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.inkMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
                Reading List
              </p>
              {week.readingList.length === 0 ? (
                <p style={{ fontFamily: OC.fontBody, fontSize: 11, color: OC.inkMuted, lineHeight: 1.5 }}>
                  No strongly grounded reading list was available for this week.
                </p>
              ) : (
                week.readingList.map((item) => (
                  <div
                    key={`${week.weekNumber}-${item.noteId}`}
                    style={{
                      background: OC.inkRaised,
                      border: `1px solid ${OC.inkBorder}`,
                      borderRadius: 3,
                      padding: '10px 10px 9px',
                      marginBottom: 8,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
                      <div>
                        <p style={{ fontFamily: OC.fontMono, fontSize: 10.5, color: OC.snow, marginBottom: 5 }}>
                          {resolveNoteTitle(item.noteId, item.title)}
                        </p>
                        <p style={{ fontFamily: OC.fontBody, fontSize: 11, color: OC.inkSubtle, lineHeight: 1.5 }}>
                          {item.reason}
                        </p>
                      </div>
                      {onOpenNote && (
                        <button
                          onClick={() => onOpenNote(item.noteId)}
                          style={{
                            background: 'transparent',
                            border: `1px solid ${OC.inkBorder}`,
                            borderRadius: 2,
                            color: OC.yolk,
                            cursor: 'pointer',
                            padding: '6px 8px',
                            fontFamily: OC.fontMono,
                            fontSize: 9,
                            letterSpacing: '0.06em',
                            textTransform: 'uppercase',
                            flexShrink: 0,
                          }}
                        >
                          Open Note
                        </button>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>

            <div>
              <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.inkMuted, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
                Concept Checkpoints
              </p>
              {week.conceptCheckpoints.map((checkpoint) => (
                <p key={checkpoint} style={{ fontFamily: OC.fontBody, fontSize: 11.5, color: OC.inkSubtle, lineHeight: 1.55, marginBottom: 5 }}>
                  {checkpoint}
                </p>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.35fr) minmax(260px, 0.95fr)', gap: 12, marginTop: 14 }}>
        <div
          style={{
            background: OC.inkBlack,
            border: `1px solid ${OC.inkBorder}`,
            borderRadius: 3,
            padding: '14px 14px 12px',
          }}
        >
          <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.yolk, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
            Glossary
          </p>
          {curriculum.glossary.length === 0 ? (
            <p style={{ fontFamily: OC.fontBody, fontSize: 11.5, color: OC.inkMuted, lineHeight: 1.55 }}>
              The workspace notes did not surface strong repeated terms for a glossary yet.
            </p>
          ) : (
            curriculum.glossary.map((entry) => (
              <div key={entry.term} style={{ marginBottom: 10 }}>
                <p style={{ fontFamily: OC.fontMono, fontSize: 10.5, color: OC.snow, marginBottom: 4 }}>{entry.term}</p>
                <p style={{ fontFamily: OC.fontBody, fontSize: 11.5, color: OC.inkSubtle, lineHeight: 1.55 }}>
                  {entry.definition}
                </p>
                {entry.supportNoteIds.length > 0 && (
                  <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.inkMuted, marginTop: 4 }}>
                    From: {entry.supportNoteIds.map((noteId) => resolveNoteTitle(noteId)).join(' • ')}
                  </p>
                )}
              </div>
            ))
          )}
        </div>

        <div
          style={{
            background: OC.inkBlack,
            border: `1px solid ${OC.inkBorder}`,
            borderRadius: 3,
            padding: '14px 14px 12px',
          }}
        >
          <p style={{ fontFamily: OC.fontMono, fontSize: 9, color: OC.yolk, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
            Grounding Notes
          </p>
          <p style={{ fontFamily: OC.fontBody, fontSize: 11.5, color: OC.inkSubtle, lineHeight: 1.55, marginBottom: 10 }}>
            This curriculum is backed by real workspace notes instead of a generic template.
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {sourceNoteIds.map((noteId) => (
              <button
                key={noteId}
                onClick={() => onOpenNote?.(noteId)}
                style={{
                  background: OC.inkRaised,
                  border: `1px solid ${OC.inkBorder}`,
                  borderRadius: 2,
                  color: OC.snow,
                  cursor: onOpenNote ? 'pointer' : 'default',
                  padding: '6px 8px',
                  fontFamily: OC.fontMono,
                  fontSize: 9,
                  letterSpacing: '0.03em',
                }}
              >
                {resolveNoteTitle(noteId)}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
