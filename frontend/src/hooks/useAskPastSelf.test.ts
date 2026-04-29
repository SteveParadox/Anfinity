import { afterEach, describe, expect, it, vi } from 'vitest';

async function loadParser() {
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    },
    configurable: true,
  });

  return import('./useAskPastSelf');
}

describe('parseSseEvents', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('parses named SSE events with structural source metadata', async () => {
    const { parseSseEvents } = await loadParser();
    const payload = [
      'event: start',
      'data: {"correlationId":"abc","status":"retrieving"}',
      '',
      'event: sources',
      'data: {"sources":[{"sourceId":"S1","noteId":"n1","title":"Ranking Notes","excerpt":"BM25","createdAt":"2026-04-01T00:00:00Z","similarity":0.84,"similarityPercent":85}],"answerStatus":"supported","confidence":"medium"}',
      '',
      '',
    ].join('\n');

    const parsed = parseSseEvents(payload);

    expect(parsed.remainder).toBe('');
    expect(parsed.events[0]).toMatchObject({
      type: 'start',
      correlationId: 'abc',
      status: 'retrieving',
    });
    expect(parsed.events[1]).toMatchObject({
      type: 'sources',
      answerStatus: 'supported',
      confidence: 'medium',
    });
    expect(parsed.events[1].sources?.[0]).toMatchObject({
      sourceId: 'S1',
      noteId: 'n1',
      similarityPercent: 85,
    });
  });

  it('keeps incomplete events in the remainder', async () => {
    const { parseSseEvents } = await loadParser();
    const parsed = parseSseEvents('event: token\ndata: {"text":"hello"');

    expect(parsed.events).toEqual([]);
    expect(parsed.remainder).toBe('event: token\ndata: {"text":"hello"');
  });

  it('sends settings-derived retrieval options in the stream payload', async () => {
    const { streamAskPastSelf } = await loadParser();
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: done\ndata: {"followUpQuestions":[]}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      body,
    } as Response);

    const events = [];
    for await (const event of streamAskPastSelf({
      workspaceId: 'workspace-1',
      query: 'What did I write?',
      topK: 9,
      similarityThreshold: 0.62,
    })) {
      events.push(event);
    }

    const payload = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(payload.top_k).toBe(9);
    expect(payload.similarity_threshold).toBe(0.62);
    expect(events[0]).toMatchObject({ type: 'done' });
  });
});
