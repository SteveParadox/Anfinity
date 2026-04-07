/**
 * FEATURE 2: "ASK YOUR PAST SELF" - Frontend RAG Service Hook
 * Handles streaming chat requests to knowledge base with source attribution.
 */

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
}

/**
 * Stream RAG chat response from backend.
 * 
 * Yields chunks containing:
 * 1. Sources metadata first
 * 2. Streamed LLM tokens
 * 3. Follow-up questions last
 */
export async function* streamAskPastSelf(
  options: AskPastSelfOptions
): AsyncGenerator<ChatStreamMessage> {
  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8080';

  const payload = {
    workspace_id: options.workspaceId,
    query: options.query,
    history: options.history || [],
    top_k: options.topK ?? 6,
    similarity_threshold: options.similarityThreshold ?? 0.3,
  };

  try {
    const response = await fetch(`${API_BASE}/chat/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${api.getToken() || ''}`,
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    if (!response.body) {
      throw new Error('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // Parse Server-Sent Events format
      const text = decoder.decode(value);
      const lines = text.split('\n\n');

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;

        const data = line.slice('data: '.length).trim();
        if (!data || data === '[DONE]') continue;

        try {
          const message = JSON.parse(data) as ChatStreamMessage;
          yield message;
        } catch (e) {
          console.error('Failed to parse chat chunk:', data, e);
        }
      }
    }
  } catch (error) {
    yield {
      type: 'error',
      message: error instanceof Error ? error.message : 'Unknown error',
    };
  }
}

/**
 * Non-streaming variant for simpler use cases.
 * Returns complete response synchronously.
 */
export async function askPastSelfSync(
  options: AskPastSelfOptions
): Promise<{
  answer: string;
  sources: RAGSource[];
  confidence: 'high' | 'medium' | 'low' | 'not_found';
  followUpQuestions: string[];
}> {
  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8080';

  const payload = {
    workspace_id: options.workspaceId,
    query: options.query,
    history: options.history || [],
    top_k: options.topK ?? 6,
    similarity_threshold: options.similarityThreshold ?? 0.3,
  };

  const response = await fetch(`${API_BASE}/chat/ask/sync`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${api.getToken() || ''}`,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

/**
 * React hook for managing chat state and streaming.
 */
export function useAskPastSelf() {
  const [messages, setMessages] = React.useState<
    Array<{ role: 'user' | 'assistant'; content: string; sources?: RAGSource[] }>
  >([]);
  const [loading, setLoading] = React.useState(false);
  const [streamingSources, setStreamingSources] = React.useState<RAGSource[]>([]);

  const chat = React.useCallback(
    async (query: string, workspaceId: string) => {
      setLoading(true);
      setMessages((prev) => [...prev, { role: 'user', content: query }]);

      let assistantContent = '';
      let sources: RAGSource[] = [];

      try {
        for await (const chunk of streamAskPastSelf({
          query,
          workspaceId,
          history: messages.map((m) => ({ role: m.role, content: m.content })),
        })) {
          if (chunk.type === 'sources' && chunk.sources) {
            sources = chunk.sources;
            setStreamingSources(sources);
          } else if (chunk.type === 'token' && chunk.text) {
            assistantContent += chunk.text;
            setMessages((prev) => {
              const updated = [...prev];
              if (updated[updated.length - 1]?.role === 'assistant') {
                updated[updated.length - 1].content = assistantContent;
                updated[updated.length - 1].sources = sources;
              }
              return updated;
            });
          } else if (chunk.type === 'error') {
            throw new Error(chunk.message || 'Stream error');
          }
        }

        // Add assistant message with sources
        setMessages((prev) => [...prev, { role: 'assistant', content: assistantContent, sources }]);
      } catch (error) {
        console.error('Chat error:', error);
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}`,
          },
        ]);
      } finally {
        setLoading(false);
        setStreamingSources([]);
      }
    },
    [messages]
  );

  const clearChat = React.useCallback(() => {
    setMessages([]);
  }, []);

  return {
    messages,
    loading,
    streamingSources,
    chat,
    clearChat,
  };
}
