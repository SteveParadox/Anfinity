import { describe, expect, it } from 'vitest';
import { transformOnboardingCurriculumFromAPI } from '@/lib/transformers';

describe('onboarding curriculum transformer', () => {
  it('normalizes snake_case onboarding curriculum payloads', () => {
    const transformed = transformOnboardingCurriculumFromAPI({
      role_input: 'software engineer',
      role: 'Engineer',
      normalized_role: 'engineer',
      summary: 'Grounded summary',
      weeks: [
        {
          week_number: 1,
          theme: 'Architecture foundations',
          objectives: ['Understand the system shape'],
          reading_list: [
            {
              note_id: 'note-1',
              title: 'System Architecture',
              reason: 'Defines the platform topology',
            },
          ],
          concept_checkpoints: ['Explain the service boundaries'],
          support_note_ids: ['note-1'],
        },
      ],
      glossary: [
        {
          term: 'Shard',
          definition: 'A partition of the event stream.',
          support_note_ids: ['note-1'],
        },
      ],
      grounding: {
        candidate_note_count: 12,
        model_candidate_note_count: 10,
        selected_note_count: 6,
        grounding_confidence: 'high',
        insufficient_content: false,
        warnings: ['Keep the plan tied to current deployment notes.'],
        role_queries: ['technical architecture'],
        fallback_queries: ['team onboarding'],
        used_note_ids: ['note-1'],
      },
      candidate_notes: [
        {
          note_id: 'note-1',
          title: 'System Architecture',
          excerpt: 'A concise architecture overview',
          note_type: 'document',
          tags: ['architecture'],
          semantic_score: 0.92,
          popularity_score: 0.4,
          freshness_score: 0.7,
          completeness_score: 0.8,
          ranking_score: 0.77,
          matched_queries: ['technical architecture'],
          query_hits: 1,
          popularity_count: 9,
          grounding_sources: ['semantic', 'popular'],
        },
      ],
    });

    expect(transformed.normalizedRole).toBe('engineer');
    expect(transformed.weeks[0].weekNumber).toBe(1);
    expect(transformed.weeks[0].readingList[0].noteId).toBe('note-1');
    expect(transformed.glossary[0].supportNoteIds).toEqual(['note-1']);
    expect(transformed.grounding.groundingConfidence).toBe('high');
    expect(transformed.candidateNotes[0].matchedQueries).toEqual(['technical architecture']);
    expect(transformed.weeks).toHaveLength(4);
    expect(transformed.weeks[1].theme).toContain('Week 2');
  });

  it('stabilizes malformed onboarding schema for the UI', () => {
    const transformed = transformOnboardingCurriculumFromAPI({
      role: 'Engineer',
      normalized_role: 'engineer',
      summary: 'Grounded summary',
      weeks: [
        {
          week_number: 3,
          theme: 'Delivery',
          objectives: ['Understand release flow'],
          reading_list: [{ note_id: 'note-3', title: 'Release Notes', reason: 'Deployment context' }],
          concept_checkpoints: ['Explain rollback expectations'],
          support_note_ids: ['note-3', 'note-3'],
        },
      ],
      grounding: {
        grounding_confidence: 'unknown-value',
        used_note_ids: ['note-3', 'note-3'],
      },
      candidate_notes: [
        { note_id: '', title: 'Broken note' },
        { note_id: 'note-3', title: 'Release Notes', excerpt: 'Deployments', matched_queries: [] },
      ],
    });

    expect(transformed.grounding.groundingConfidence).toBe('low');
    expect(transformed.weeks).toHaveLength(4);
    expect(transformed.weeks[2].supportNoteIds).toEqual(['note-3']);
    expect(transformed.candidateNotes).toHaveLength(1);
  });
});
