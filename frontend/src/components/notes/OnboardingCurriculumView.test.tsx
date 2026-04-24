import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { OnboardingCurriculumView } from '@/components/notes/OnboardingCurriculumView';
import type { Note, OnboardingCurriculum } from '@/types';

describe('OnboardingCurriculumView', () => {
  it('renders grounded weeks, glossary terms, and note links', () => {
    const onOpenNote = vi.fn();
    const notes: Note[] = [
      {
        id: 'note-1',
        title: 'System Architecture',
        content: 'Architecture details',
        tags: ['architecture'],
        connections: [],
        userId: 'user-1',
        workspaceId: 'workspace-1',
        createdAt: new Date('2026-01-01T00:00:00Z'),
        updatedAt: new Date('2026-01-02T00:00:00Z'),
        type: 'document',
      },
    ];

    const curriculum: OnboardingCurriculum = {
      roleInput: 'engineer',
      role: 'Engineer',
      normalizedRole: 'engineer',
      summary: 'This plan is grounded in architecture and deployment notes.',
      weeks: [
        {
          weekNumber: 1,
          theme: 'Architecture foundations',
          objectives: ['Learn the service boundaries'],
          readingList: [
            {
              noteId: 'note-1',
              title: 'System Architecture',
              reason: 'Covers the core topology.',
            },
          ],
          conceptCheckpoints: ['Explain how requests flow through the platform.'],
          supportNoteIds: ['note-1'],
        },
        {
          weekNumber: 2,
          theme: 'Delivery flow',
          objectives: ['Understand how code reaches production'],
          readingList: [],
          conceptCheckpoints: ['Describe the deployment path.'],
          supportNoteIds: ['note-1'],
        },
        {
          weekNumber: 3,
          theme: 'Operational signals',
          objectives: ['Recognize core monitoring patterns'],
          readingList: [],
          conceptCheckpoints: ['Identify the main health indicators.'],
          supportNoteIds: ['note-1'],
        },
        {
          weekNumber: 4,
          theme: 'Role execution',
          objectives: ['Tie system knowledge back to day-to-day work'],
          readingList: [],
          conceptCheckpoints: ['Show how to use the workspace notes in practice.'],
          supportNoteIds: ['note-1'],
        },
      ],
      glossary: [
        {
          term: 'Shard',
          definition: 'A partition of the event stream.',
          supportNoteIds: ['note-1'],
        },
      ],
      grounding: {
        candidateNoteCount: 8,
        modelCandidateNoteCount: 8,
        selectedNoteCount: 1,
        groundingConfidence: 'high',
        insufficientContent: false,
        warnings: [],
        roleQueries: ['technical architecture', 'deployment process'],
        fallbackQueries: ['team onboarding'],
        usedNoteIds: ['note-1'],
      },
      candidateNotes: [
        {
          noteId: 'note-1',
          title: 'System Architecture',
          excerpt: 'Architecture summary',
          summary: 'Architecture summary',
          tags: ['architecture'],
          noteType: 'document',
          semanticScore: 0.91,
          popularityScore: 0.42,
          freshnessScore: 0.63,
          completenessScore: 0.88,
          rankingScore: 0.8,
          matchedQueries: ['technical architecture'],
          queryHits: 1,
          popularityCount: 9,
          groundingSources: ['semantic', 'popular'],
        },
      ],
    };

    const markup = renderToStaticMarkup(
      <OnboardingCurriculumView
        curriculum={curriculum}
        notes={notes}
        workspaceName="Platform"
        onOpenNote={onOpenNote}
      />
    );

    expect(markup).toContain('Architecture foundations');
    expect(markup).toContain('System Architecture');
    expect(markup).toContain('Shard');
    expect(markup).toContain('Grounded in 8 candidate notes');
    expect(markup).toContain('Open Note');
  });
});
