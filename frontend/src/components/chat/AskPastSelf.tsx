'use client';

import { useEffect, useRef, useState } from 'react';
import { useAskPastSelf } from '../../hooks/useAskPastSelf';
import type { RAGSource } from '../../hooks/useAskPastSelf';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  sources?: RAGSource[];
}

interface AskPastSelfProps {
  workspaceId: string;
  onClose?: () => void;
}

function formatDate(dateStr: string | undefined | null): string {
  if (!dateStr) return 'Unknown Date';

  try {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return 'Unknown Date';

    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return 'Unknown Date';
  }
}

export function AskPastSelf({ workspaceId, onClose }: AskPastSelfProps) {
  const {
    messages,
    loading,
    streamingSources,
    followUpQuestions,
    chat,
    clearChat,
    cancelChat,
  } = useAskPastSelf();
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, followUpQuestions, streamingSources]);

  async function handleSendMessage() {
    if (!input.trim() || loading) return;

    const userMessage = input;
    setInput('');
    await chat(userMessage, workspaceId);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSendMessage();
    }
  }

  const visibleMessages = messages.filter(
    (message) => message.content.trim().length > 0 || (message.sources?.length ?? 0) > 0
  );

  return (
    <div className='flex h-full flex-col bg-gray-950'>
      <div className='bg-gradient-to-r from-indigo-600 to-purple-600 p-4 shadow-lg'>
        <div className='flex items-center justify-between'>
          <div>
            <h2 className='text-xl font-bold text-white'>Ask Your Past Self</h2>
            <p className='mt-1 text-sm text-indigo-100'>
              Search your knowledge base with grounded AI answers
            </p>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className='text-white transition hover:text-indigo-100'
              aria-label='Close chat'
            >
              <svg className='h-6 w-6' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
                <path
                  strokeLinecap='round'
                  strokeLinejoin='round'
                  strokeWidth={2}
                  d='M6 18L18 6M6 6l12 12'
                />
              </svg>
            </button>
          )}
        </div>
      </div>

      <div className='flex-1 space-y-4 overflow-y-auto bg-gray-950 p-4'>
        {visibleMessages.length === 0 && !loading && (
          <div className='flex h-full items-center justify-center'>
            <div className='max-w-md text-center'>
              <div className='mb-4 text-5xl'>Thoughts</div>
              <h3 className='mb-2 text-2xl font-bold text-gray-100'>
                Chat with Your Knowledge Base
              </h3>
              <p className='text-gray-400'>
                Ask questions about your notes and documents. Every answer is grounded in your
                own stored context.
              </p>
              <div className='mt-6 border-t border-gray-800 pt-6'>
                <p className='mb-3 text-sm text-gray-500'>Example questions:</p>
                <div className='space-y-2'>
                  {[
                    'What did I learn about AI architecture?',
                    'Summarize my notes on semantic search',
                    'What are my thoughts on RAG systems?',
                  ].map((example) => (
                    <button
                      key={example}
                      onClick={() => setInput(example)}
                      className='block w-full rounded-lg bg-gray-800 p-2 text-left text-sm text-gray-300 transition hover:bg-gray-700 hover:text-gray-100'
                    >
                      "{example}"
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {visibleMessages.map((message: Message, index: number) => (
          <div
            key={`${message.role}-${index}`}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-2xl rounded-2xl p-4 ${
                message.role === 'user'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-100'
              }`}
            >
              <p className='whitespace-pre-wrap text-sm leading-relaxed'>{message.content}</p>

              {message.sources && message.sources.length > 0 && (
                <div className='mt-4 space-y-3 border-t border-gray-700 pt-4'>
                  <p className='text-xs font-semibold uppercase tracking-wider text-gray-400'>
                    From your notes
                  </p>

                  {message.sources.map((source: RAGSource, sourceIndex: number) => (
                    <div key={sourceIndex} className='space-y-2 rounded-lg bg-gray-700 p-3'>
                      <div className='flex items-start justify-between gap-2'>
                        <a
                          href={`/app/notes/${source.noteId}`}
                          className='line-clamp-2 text-sm font-medium text-indigo-300 transition hover:text-indigo-200'
                          title={source.title}
                        >
                          {source.title}
                        </a>
                        <span className='ml-2 whitespace-nowrap text-xs text-gray-400'>
                          {formatDate(source.createdAt)}
                        </span>
                      </div>

                      <p className='line-clamp-3 text-xs text-gray-400'>{source.excerpt}</p>

                      <div className='space-y-1'>
                        <div className='h-2 overflow-hidden rounded-full bg-gray-600'>
                          <div
                            className='h-2 rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all'
                            style={{ width: `${Math.round(source.similarity * 100)}%` }}
                          />
                        </div>
                        <p className='text-xs text-gray-500'>
                          {Math.round(source.similarity * 100)}% relevance match
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && streamingSources.length > 0 && (
          <div className='flex justify-start'>
            <div className='max-w-2xl space-y-3 rounded-2xl bg-gray-800 p-4'>
              <div className='text-sm font-medium text-gray-400'>
                Searching your knowledge base...
              </div>

              {streamingSources.map((source: RAGSource, index: number) => (
                <div key={index} className='animate-pulse rounded-lg bg-gray-700 p-3 opacity-70'>
                  <div className='mb-2 flex items-start justify-between gap-2'>
                    <div className='h-4 w-2/3 rounded bg-gray-600' />
                    <div className='h-4 w-20 rounded bg-gray-600' />
                  </div>
                  <div className='mb-1 h-3 w-full rounded bg-gray-600' />
                  <div className='h-3 w-4/5 rounded bg-gray-600' />
                </div>
              ))}
            </div>
          </div>
        )}

        {loading && streamingSources.length === 0 && (
          <div className='flex justify-start'>
            <div className='rounded-2xl bg-gray-800 p-4 text-sm text-gray-400 animate-pulse'>
              Thinking about your knowledge base...
            </div>
          </div>
        )}

        {!loading && followUpQuestions.length > 0 && (
          <div className='flex justify-start'>
            <div className='max-w-2xl rounded-2xl bg-gray-800 p-4'>
              <p className='mb-3 text-xs font-semibold uppercase tracking-wider text-gray-400'>
                Suggested follow-ups
              </p>
              <div className='flex flex-wrap gap-2'>
                {followUpQuestions.map((question, index) => (
                  <button
                    key={`${question}-${index}`}
                    onClick={() => {
                      setInput('');
                      void chat(question, workspaceId);
                    }}
                    className='rounded-full bg-gray-700 px-3 py-2 text-sm text-gray-200 transition hover:bg-gray-600'
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className='border-t border-gray-800 bg-gray-900 p-4'>
        <div className='flex gap-2'>
          <textarea
            className='flex-1 resize-none rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-white outline-none transition hover:border-gray-600 focus:ring-2 focus:ring-indigo-500'
            placeholder='Ask anything about your past notes... (Shift+Enter for new line)'
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            disabled={loading}
          />
          <div className='flex flex-col gap-2'>
            <button
              onClick={loading ? cancelChat : handleSendMessage}
              disabled={!loading && !input.trim()}
              className='flex h-full items-center gap-2 whitespace-nowrap rounded-xl bg-indigo-600 px-6 py-3 font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50'
            >
              <span>{loading ? 'Stop' : 'Ask'}</span>
            </button>
            {messages.length > 0 && (
              <button
                onClick={clearChat}
                disabled={loading}
                className='rounded-xl bg-gray-700 px-4 py-2 text-sm text-gray-200 transition hover:bg-gray-600 disabled:opacity-50'
                title='Clear chat history'
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {messages.length === 0 && (
          <div className='mt-3 rounded-lg bg-gray-800 p-3 text-xs text-gray-400'>
            <p className='mb-1 font-semibold text-gray-300'>Tips:</p>
            <ul className='list-inside list-disc space-y-1'>
              <li>Ask complex questions spanning multiple notes</li>
              <li>Every answer is grounded in your personal knowledge</li>
              <li>Click on sources to view the original notes</li>
              <li>Use natural language without special syntax</li>
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default AskPastSelf;
