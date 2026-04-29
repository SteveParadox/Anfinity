export const SEMANTIC_FEEDBACK_TYPES = [
  'correct',
  'partially_correct',
  'irrelevant',
  'missing_expected_result',
  'wrong_result',
  'bad_highlight',
  'low_quality_answer',
  'hallucinated_or_unsupported',
  'other',
] as const;

export type SemanticFeedbackType = (typeof SEMANTIC_FEEDBACK_TYPES)[number];

export const SEMANTIC_FEEDBACK_REASON_OPTIONS = [
  { value: 'result_unrelated', label: 'Result was unrelated' },
  { value: 'expected_note_missing', label: 'Expected note was missing' },
  { value: 'highlight_wrong', label: 'Highlight was wrong' },
  { value: 'wrong_source_used', label: 'Answer used wrong source' },
  { value: 'answer_unsupported', label: 'Answer unsupported by notes' },
  { value: 'similarity_misleading', label: 'Similarity score was misleading' },
  { value: 'result_outdated', label: 'Result was outdated' },
  { value: 'duplicate_result', label: 'Result was duplicate' },
  { value: 'other', label: 'Other' },
] as const;

export type SemanticFeedbackReasonCode = (typeof SEMANTIC_FEEDBACK_REASON_OPTIONS)[number]['value'];

export const SEMANTIC_FEEDBACK_TYPE_LABELS: Record<SemanticFeedbackType, string> = {
  correct: 'Correct / Useful',
  partially_correct: 'Partially Correct',
  irrelevant: 'Irrelevant',
  missing_expected_result: 'Missing Expected Result',
  wrong_result: 'Wrong Result',
  bad_highlight: 'Bad Highlight',
  low_quality_answer: 'Low Quality Answer',
  hallucinated_or_unsupported: 'Hallucinated / Unsupported',
  other: 'Other',
};

const NEGATIVE_OR_PARTIAL = new Set<SemanticFeedbackType>([
  'partially_correct',
  'irrelevant',
  'missing_expected_result',
  'wrong_result',
  'bad_highlight',
  'low_quality_answer',
  'hallucinated_or_unsupported',
]);

export function ratingForSemanticFeedback(type: SemanticFeedbackType): -1 | 0 | 1 {
  if (type === 'correct') return 1;
  if (type === 'partially_correct' || type === 'other') return 0;
  return -1;
}

export function requiresReasonForSemanticFeedback(type: SemanticFeedbackType): boolean {
  return NEGATIVE_OR_PARTIAL.has(type);
}

type SemanticFeedbackResultLike = {
  chunk_id?: unknown;
  document_id?: unknown;
  source_kind?: unknown;
  source_type?: unknown;
  chunk_index?: unknown;
  similarity_score?: unknown;
  final_score?: unknown;
  confidence?: unknown;
  confidence_score?: unknown;
  highlights?: unknown;
  matched_chunks?: unknown;
};

function asFiniteNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function normalizeSemanticFeedbackResultId(value: unknown): string {
  return String(value ?? '').trim().slice(0, 255);
}

export function semanticFeedbackResultIds(results: SemanticFeedbackResultLike[]): string[] {
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const result of results) {
    const id = normalizeSemanticFeedbackResultId(result.chunk_id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids.slice(0, 100);
}

export function semanticFeedbackResultSnapshot(results: SemanticFeedbackResultLike[]): Array<Record<string, unknown>> {
  return results.slice(0, 100).map((result, index) => ({
    rank: index,
    chunk_id: normalizeSemanticFeedbackResultId(result.chunk_id),
    document_id: normalizeSemanticFeedbackResultId(result.document_id),
    source_kind: String(result.source_kind ?? '').trim(),
    source_type: String(result.source_type ?? '').trim(),
    chunk_index: Math.max(0, Math.trunc(asFiniteNumber(result.chunk_index))),
    similarity_score: Math.max(0, Math.min(asFiniteNumber(result.similarity_score), 1)),
    final_score: Math.max(0, Math.min(asFiniteNumber(result.final_score), 1)),
    confidence: String(result.confidence || 'low'),
    confidence_score: Math.max(0, Math.min(asFiniteNumber(result.confidence_score, result.final_score as number), 1)),
    highlights: Array.isArray(result.highlights) ? result.highlights.slice(0, 3) : [],
    matched_chunks: Array.isArray(result.matched_chunks) ? result.matched_chunks.slice(0, 3) : [],
  })).filter((item) => item.chunk_id);
}
