import { describe, expect, it } from 'vitest';

import {
  ratingForSemanticFeedback,
  requiresReasonForSemanticFeedback,
  semanticFeedbackResultIds,
  semanticFeedbackResultSnapshot,
} from './semanticFeedback';

describe('semantic feedback helpers', () => {
  it('maps typed feedback to directional rating values', () => {
    expect(ratingForSemanticFeedback('correct')).toBe(1);
    expect(ratingForSemanticFeedback('partially_correct')).toBe(0);
    expect(ratingForSemanticFeedback('other')).toBe(0);
    expect(ratingForSemanticFeedback('wrong_result')).toBe(-1);
    expect(ratingForSemanticFeedback('hallucinated_or_unsupported')).toBe(-1);
  });

  it('requires structured reason for partial/negative feedback', () => {
    expect(requiresReasonForSemanticFeedback('correct')).toBe(false);
    expect(requiresReasonForSemanticFeedback('partially_correct')).toBe(true);
    expect(requiresReasonForSemanticFeedback('irrelevant')).toBe(true);
    expect(requiresReasonForSemanticFeedback('missing_expected_result')).toBe(true);
  });

  it('builds deduped bounded result ids for feedback context', () => {
    const ids = semanticFeedbackResultIds([
      { chunk_id: ' chunk-1 ' },
      { chunk_id: 'chunk-1' },
      { chunk_id: '' },
      { chunk_id: 'chunk-2' },
    ]);

    expect(ids).toEqual(['chunk-1', 'chunk-2']);
  });

  it('builds compact result snapshots without result content', () => {
    const snapshot = semanticFeedbackResultSnapshot([
      {
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        source_kind: 'document',
        source_type: 'upload',
        chunk_index: 2,
        similarity_score: 1.5,
        final_score: 0.75,
        confidence: 'high',
        confidence_score: 0.8,
        highlights: [{ text: 'match' }, { text: 'second' }, { text: 'third' }, { text: 'ignored' }],
        matched_chunks: [{ chunk_id: 'a' }, { chunk_id: 'b' }, { chunk_id: 'c' }, { chunk_id: 'ignored' }],
        content: 'not persisted',
      },
    ] as any);

    expect(snapshot).toEqual([
      {
        rank: 0,
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        source_kind: 'document',
        source_type: 'upload',
        chunk_index: 2,
        similarity_score: 1,
        final_score: 0.75,
        confidence: 'high',
        confidence_score: 0.8,
        highlights: [{ text: 'match' }, { text: 'second' }, { text: 'third' }],
        matched_chunks: [{ chunk_id: 'a' }, { chunk_id: 'b' }, { chunk_id: 'c' }],
      },
    ]);
    expect(snapshot[0]).not.toHaveProperty('content');
  });
});
