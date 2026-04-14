import React from 'react';
import { api } from '../lib/api';

export interface RAGSource {
  noteId: string;
  title: string;
  excerpt: string;
  createdAt: string;
  similarity: number;
}

export interface ChatStreamMessage {
  type: 'sources' | 'token' | 'done' | 'error';
  sources?: RAGSource[];
  text?: string;
  followUpQuestions?: string[];
  message?: string;
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
};

function parseSseEvents(buffer: string): {
  events: ChatStreamMessage[];
  remainder: string;
} {
  const events: ChatStreamMessage[] = [];
  const rawEvents = buffer.split('\n\n');
  const remainder = rawEvents.pop() ?? '';

  for (const rawEvent of rawEvents) {
    const data = rawEvent
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
      .trim();

    if (!data || data === '[DONE]') {
      continue;
    }

    try {
      events.push(JSON.parse(data) as ChatStreamMessage);
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
    const response = await fetch(`${apiBase}/chat/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${api.getToken() || ''}`,
      },
      body: JSON.stringify(payload),
      signal: options.signal,
    });

    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const errorBody = await response.json();
        detail = errorBody.detail || detail;
      } catch {
        const errorText = await response.text().catch(() => '');
        if (errorText) {
          detail = errorText;
        }
      }
      throw new Error(detail);
    }

    if (!response.body) {
      throw new Error('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
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
}> {
  const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8080';
  const payload = {
    workspace_id: options.workspaceId,
    query: options.query,
    history: options.history || [],
    top_k: options.topK ?? 6,
    similarity_threshold: options.similarityThreshold ?? 0.3,
  };

  const response = await fetch(`${apiBase}/chat/ask/sync`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${api.getToken() || ''}`,
    },
    body: JSON.stringify(payload),
    signal: options.signal,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
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
    (content: string, sources: RAGSource[]) => {
      setMessages((prev) => {
        const updated = [...prev];
        for (let index = updated.length - 1; index >= 0; index -= 1) {
          if (updated[index].role === 'assistant') {
            updated[index] = {
              ...updated[index],
              content,
              sources,
            };
            return updated;
          }
        }
        return [...updated, { role: 'assistant', content, sources }];
      });
    },
    []
  );

  const chat = React.useCallback(
    async (query: string, workspaceId: string) => {
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
          signal: controller.signal,
        })) {
          if (chunk.type === 'sources' && chunk.sources) {
            sources = chunk.sources;
            setStreamingSources(sources);
            replaceLastAssistant(assistantContent, sources);
            continue;
          }

          if (chunk.type === 'token' && chunk.text) {
            assistantContent += chunk.text;
            replaceLastAssistant(assistantContent, sources);
            continue;
          }

          if (chunk.type === 'done') {
            setFollowUpQuestions(chunk.followUpQuestions || []);
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
