import React from 'react';
import { api } from '../lib/api';

export interface RAGSource {
  sourceId?: string;
  noteId: string;
  title: string;
  excerpt: string;
  createdAt: string;
  similarity: number;
  similarityPercent?: number;
  citation?: string;
  chunkId?: string;
  chunkIndex?: number;
  supportLevel?: 'supported' | 'partial';
}

export interface ChatStreamMessage {
  type: 'start' | 'sources' | 'token' | 'done' | 'error';
  sources?: RAGSource[];
  text?: string;
  followUpQuestions?: string[];
  message?: string;
  confidence?: 'high' | 'medium' | 'low' | 'not_found';
  answerStatus?: 'supported' | 'partial' | 'refusal';
  refusalReason?: string;
  diagnostics?: Record<string, unknown>;
  correlationId?: string;
  status?: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AskPastSelfOptions {
  workspaceId: string;
  query: string;
  history?: ChatMessage[];
  topK?: number;
  similarityThreshold?: number;
  signal?: AbortSignal;
}

type ChatStateMessage = {
  role: 'user' | 'assistant';
  content: string;
  sources?: RAGSource[];
  answerStatus?: 'supported' | 'partial' | 'refusal';
  refusalReason?: string;
  diagnostics?: Record<string, unknown>;
};

const STREAM_READ_TIMEOUT_MS = 180_000;
const STREAM_CONNECT_TIMEOUT_MS = 30_000;

async function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(message)), ms);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
    }
  }
}

async function readStreamChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => {
          void reader.cancel();
          reject(new Error('Ask Your Past Self timed out while waiting for the answer.'));
        }, STREAM_READ_TIMEOUT_MS);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
    }
  }
}

export function parseSseEvents(buffer: string): {
  events: ChatStreamMessage[];
  remainder: string;
} {
  const events: ChatStreamMessage[] = [];
  const rawEvents = buffer.split(/\r?\n\r?\n/);
  const remainder = rawEvents.pop() ?? '';

  for (const rawEvent of rawEvents) {
    let eventType = '';
    const dataLines: string[] = [];

    for (const line of rawEvent.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim();
      }
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    const data = dataLines.join('\n').trim();

    if (!data || data === '[DONE]') {
      continue;
    }

    try {
      const parsed = JSON.parse(data) as ChatStreamMessage;
      events.push({
        ...parsed,
        type: parsed.type || (eventType as ChatStreamMessage['type']),
      });
    } catch (error) {
      console.error('Failed to parse chat stream event:', data, error);
    }
  }

  return { events, remainder };
}

export async function* streamAskPastSelf(
  options: AskPastSelfOptions
): AsyncGenerator<ChatStreamMessage> {
  const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8080';
  const payload = {
    workspace_id: options.workspaceId,
    query: options.query,
    history: options.history || [],
    top_k: options.topK ?? 6,
    similarity_threshold: options.similarityThreshold ?? 0.3,
  };

  try {
    const response = await withTimeout(
      api.stream('/chat/ask', {
        method: 'POST',
        body: JSON.stringify(payload),
        signal: options.signal,
      }),
      STREAM_CONNECT_TIMEOUT_MS,
      'Ask Your Past Self timed out while connecting to the server.'
    );

    if (!response.ok) {
      throw await api.errorFromResponse(response);
    }

    if (!response.body) {
      throw new Error('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await readStreamChunk(reader);
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

      const parsed = parseSseEvents(buffer);
      buffer = parsed.remainder;

      for (const event of parsed.events) {
        yield event;
      }

      if (done) {
        break;
      }
    }

    if (buffer.trim()) {
      const parsed = parseSseEvents(`${buffer}\n\n`);
      for (const event of parsed.events) {
        yield event;
      }
    }
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') {
      throw error;
    }

    yield {
      type: 'error',
      message: error instanceof Error ? error.message : 'Unknown error',
    };
  }
}

export async function askPastSelfSync(
  options: AskPastSelfOptions
): Promise<{
  answer: string;
  sources: RAGSource[];
  confidence: 'high' | 'medium' | 'low' | 'not_found';
  followUpQuestions: string[];
  answerStatus: 'supported' | 'partial' | 'refusal';
}> {
  const payload = {
    workspace_id: options.workspaceId,
    query: options.query,
    history: options.history || [],
    top_k: options.topK ?? 6,
    similarity_threshold: options.similarityThreshold ?? 0.3,
  };

  const response = await withTimeout(
    api.stream('/chat/ask/sync', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: options.signal,
    }),
    STREAM_CONNECT_TIMEOUT_MS,
    'Ask Your Past Self timed out while connecting to the server.'
  );

  if (!response.ok) {
    throw await api.errorFromResponse(response);
  }

  return response.json();
}

export function useAskPastSelf(workspaceId?: string) {
  const [messages, setMessages] = React.useState<ChatStateMessage[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [streamingSources, setStreamingSources] = React.useState<RAGSource[]>([]);
  const [followUpQuestions, setFollowUpQuestions] = React.useState<string[]>([]);

  const messagesRef = React.useRef<ChatStateMessage[]>([]);
  const abortRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  React.useEffect(() => {
    abortRef.current?.abort();
    setMessages([]);
    setStreamingSources([]);
    setFollowUpQuestions([]);
    setLoading(false);
  }, [workspaceId]);

  const replaceLastAssistant = React.useCallback(
    (
      content: string,
      sources: RAGSource[],
      metadata: Pick<ChatStateMessage, 'answerStatus' | 'refusalReason' | 'diagnostics'> = {},
    ) => {
      setMessages((prev) => {
        const updated = [...prev];
        for (let index = updated.length - 1; index >= 0; index -= 1) {
          if (updated[index].role === 'assistant') {
            updated[index] = {
              ...updated[index],
              content,
              sources,
              ...metadata,
            };
            return updated;
          }
        }
        return [...updated, { role: 'assistant', content, sources, ...metadata }];
      });
    },
    []
  );

  const chat = React.useCallback(
    async (query: string, workspaceId: string, overrides?: Partial<Pick<AskPastSelfOptions, 'topK' | 'similarityThreshold'>>) => {
      if (loading) {
        return;
      }

      const history = messagesRef.current.map((message) => ({
        role: message.role,
        content: message.content,
      }));

      const controller = new AbortController();
      abortRef.current = controller;

      let assistantContent = '';
      let sources: RAGSource[] = [];

      setLoading(true);
      setStreamingSources([]);
      setFollowUpQuestions([]);
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: query },
        { role: 'assistant', content: '', sources: [] },
      ]);

      try {
        for await (const chunk of streamAskPastSelf({
          query,
          workspaceId,
          history,
          topK: overrides?.topK,
          similarityThreshold: overrides?.similarityThreshold,
          signal: controller.signal,
        })) {
          if (chunk.type === 'sources' && chunk.sources) {
            sources = chunk.sources;
            setStreamingSources(sources);
            replaceLastAssistant(assistantContent, sources, {
              answerStatus: chunk.answerStatus,
              refusalReason: chunk.refusalReason,
              diagnostics: chunk.diagnostics,
            });
            continue;
          }

          if (chunk.type === 'start') {
            continue;
          }

          if (chunk.type === 'token' && chunk.text) {
            assistantContent += chunk.text;
            replaceLastAssistant(assistantContent, sources);
            continue;
          }

          if (chunk.type === 'done') {
            setFollowUpQuestions(chunk.followUpQuestions || []);
            replaceLastAssistant(assistantContent, sources, {
              answerStatus: chunk.answerStatus,
              refusalReason: chunk.refusalReason,
              diagnostics: chunk.diagnostics,
            });
            continue;
          }

          if (chunk.type === 'error') {
            throw new Error(chunk.message || 'Stream error');
          }
        }

        replaceLastAssistant(assistantContent, sources);
      } catch (error) {
        if ((error as Error)?.name === 'AbortError') {
          setMessages((prev) => prev.filter((message, index) => {
            const isLast = index === prev.length - 1;
            return !(
              isLast &&
              message.role === 'assistant' &&
              !message.content &&
              (!message.sources || message.sources.length === 0)
            );
          }));
          return;
        }

        const message = error instanceof Error ? error.message : 'Unknown error';
        replaceLastAssistant(`Error: ${message}`, []);
      } finally {
        abortRef.current = null;
        setLoading(false);
        setStreamingSources([]);
      }
    },
    [loading, replaceLastAssistant]
  );

  const clearChat = React.useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setStreamingSources([]);
    setFollowUpQuestions([]);
  }, []);

  const cancelChat = React.useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return {
    messages,
    loading,
    streamingSources,
    followUpQuestions,
    chat,
    clearChat,
    cancelChat,
  };
}
