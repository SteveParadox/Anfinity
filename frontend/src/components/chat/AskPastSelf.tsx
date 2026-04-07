'use client';

import { useState, useRef, useEffect } from 'react';
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

/**
 * Safe date formatter - handles null, invalid dates, ISO strings
 */
function formatDate(dateStr: string | undefined | null): string {
  if (!dateStr) return 'Unknown Date';
  
  try {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return 'Unknown Date';
    
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return 'Unknown Date';
  }
}

/**
 * FEATURE 2: "ASK YOUR PAST SELF" - Chat UI Component
 * 
 * Conversational interface that queries the user's personal knowledge base.
 * Every answer is grounded in their own notes with exact source attribution.
 */
export function AskPastSelf({ workspaceId, onClose }: AskPastSelfProps) {
  const { messages, loading, streamingSources, chat, clearChat } = useAskPastSelf();
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function handleSendMessage() {
    if (!input.trim() || loading) return;

    const userMessage = input;
    setInput('');

    await chat(userMessage, workspaceId);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  }

  return (
    <div className='flex flex-col h-full bg-gray-950'>
      {/* Header */}
      <div className='bg-gradient-to-r from-indigo-600 to-purple-600 p-4 shadow-lg'>
        <div className='flex justify-between items-center'>
          <div>
            <h2 className='text-xl font-bold text-white'>Ask Your Past Self</h2>
            <p className='text-indigo-100 text-sm mt-1'>
              Search your knowledge base with AI-powered answers
            </p>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className='text-white hover:text-indigo-100 transition'
              aria-label='Close chat'
            >
              <svg
                className='w-6 h-6'
                fill='none'
                stroke='currentColor'
                viewBox='0 0 24 24'
              >
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

      {/* Messages Container */}
      <div className='flex-1 overflow-y-auto p-4 space-y-4 bg-gray-950'>
        {messages.length === 0 && !loading && (
          <div className='h-full flex items-center justify-center'>
            <div className='text-center max-w-md'>
              <div className='text-5xl mb-4'>💭</div>
              <h3 className='text-2xl font-bold text-gray-100 mb-2'>
                Chat with Your Knowledge Base
              </h3>
              <p className='text-gray-400'>
                Ask questions about your notes and documents. Every answer is grounded in your
                personal knowledge with exact source attribution.
              </p>
              <div className='mt-6 pt-6 border-t border-gray-800'>
                <p className='text-gray-500 text-sm mb-3'>Example questions:</p>
                <div className='space-y-2'>
                  {[
                    'What did I learn about AI architecture?',
                    'Summarize my notes on semantic search',
                    'What are my thoughts on RAG systems?',
                  ].map((example, i) => (
                    <button
                      key={i}
                      onClick={() => setInput(example)}
                      className='block w-full text-left p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-gray-100 text-sm transition'
                    >
                      "{example}"
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {messages.map((message: Message, index: number) => (
          <div
            key={index}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-2xl p-4 rounded-2xl ${
                message.role === 'user'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-100'
              }`}
            >
              {/* Message Content */}
              <p className='whitespace-pre-wrap text-sm leading-relaxed'>{message.content}</p>

              {/* Source Citations */}
              {message.sources && message.sources.length > 0 && (
                <div className='mt-4 pt-4 border-t border-gray-700 space-y-3'>
                  <p className='text-xs font-semibold uppercase tracking-wider text-gray-400'>
                    📚 From your notes
                  </p>

                  {message.sources.map((source: RAGSource, sourceIndex: number) => (
                    <div
                      key={sourceIndex}
                      className='bg-gray-700 rounded-lg p-3 space-y-2'
                    >
                      {/* Source Header */}
                      <div className='flex justify-between items-start gap-2'>
                        <a
                          href={`/app/notes/${source.noteId}`}
                          className='text-sm font-medium text-indigo-300 hover:text-indigo-200 transition line-clamp-2'
                          title={source.title}
                        >
                          {source.title}
                        </a>
                        <span className='text-xs text-gray-400 whitespace-nowrap ml-2'>
                          {formatDate(source.createdAt)}
                        </span>
                      </div>

                      {/* Excerpt */}
                      <p className='text-xs text-gray-400 line-clamp-3'>
                        {source.excerpt}
                      </p>

                      {/* Similarity Score */}
                      <div className='space-y-1'>
                        <div className='h-2 bg-gray-600 rounded-full overflow-hidden'>
                          <div
                            className='h-2 bg-gradient-to-r from-indigo-500 to-purple-500 rounded-full transition-all'
                            style={{
                              width: `${Math.round(source.similarity * 100)}%`,
                            }}
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
            <div className='bg-gray-800 rounded-2xl p-4 space-y-3 max-w-2xl'>
              <div className='text-gray-400 text-sm font-medium'>
                📖 Searching your knowledge base...
              </div>

              {streamingSources.map((source: RAGSource, i: number) => (
                <div key={i} className='bg-gray-700 rounded-lg p-3 opacity-70 animate-pulse'>
                  <div className='flex justify-between items-start gap-2 mb-2'>
                    <div className='h-4 bg-gray-600 rounded w-2/3' />
                    <div className='h-4 bg-gray-600 rounded w-20' />
                  </div>
                  <div className='h-3 bg-gray-600 rounded w-full mb-1' />
                  <div className='h-3 bg-gray-600 rounded w-4/5' />
                </div>
              ))}
            </div>
          </div>
        )}

        {loading && streamingSources.length === 0 && (
          <div className='flex justify-start'>
            <div className='bg-gray-800 rounded-2xl p-4 text-gray-400 text-sm animate-pulse'>
              ✨ Thinking about your knowledge base...
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input Area */}
      <div className='p-4 border-t border-gray-800 bg-gray-900'>
        <div className='flex gap-2'>
          <textarea
            className='flex-1 bg-gray-800 text-white rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-indigo-500 resize-none border border-gray-700 hover:border-gray-600 transition text-sm'
            placeholder='Ask anything about your past notes... (Shift+Enter for new line)'
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            disabled={loading}
          />
          <div className='flex flex-col gap-2'>
            <button
              onClick={handleSendMessage}
              disabled={loading || !input.trim()}
              className='bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-3 rounded-xl font-medium transition h-full flex items-center gap-2 whitespace-nowrap'
            >
              {loading ? (
                <>
                  <span className='animate-spin'>⏳</span>
                  <span className='hidden sm:inline'>Answering</span>
                </>
              ) : (
                <>
                  <span>Ask</span>
                  <span>→</span>
                </>
              )}
            </button>
            {messages.length > 0 && (
              <button
                onClick={clearChat}
                disabled={loading}
                className='bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-200 px-4 py-2 rounded-xl text-sm transition'
                title='Clear chat history'
              >
                🗑️
              </button>
            )}
          </div>
        </div>

        {/* Helpful Tips */}
        {messages.length === 0 && (
          <div className='mt-3 p-3 bg-gray-800 rounded-lg text-xs text-gray-400'>
            <p className='font-semibold mb-1 text-gray-300'>💡 Tips:</p>
            <ul className='space-y-1 list-disc list-inside'>
              <li>Ask complex questions spanning multiple notes</li>
              <li>Every answer is grounded in your personal knowledge</li>
              <li>Click on sources to view the original notes</li>
              <li>Use natural language - no special syntax needed</li>
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default AskPastSelf;
