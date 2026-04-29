'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowUp, BookOpenText, RotateCcw, Search, Sparkles, Square, X } from 'lucide-react';
import { useAskPastSelf } from '../../hooks/useAskPastSelf';
import type { RAGSource } from '../../hooks/useAskPastSelf';
import { useProductSettings } from '../../hooks/useProductSettings';
import { DESIGN_TOKENS } from '../../lib/theme';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  sources?: RAGSource[];
}

interface AskPastSelfProps {
  workspaceId: string;
  onClose?: () => void;
}

const TT = {
  canvas: DESIGN_TOKENS.canvas,
  panel: DESIGN_TOKENS.panel,
  panelRaised: DESIGN_TOKENS.panelRaised,
  border: DESIGN_TOKENS.border,
  borderStrong: DESIGN_TOKENS.borderStrong,
  text: DESIGN_TOKENS.text,
  textMuted: DESIGN_TOKENS.textMuted,
  textSubtle: DESIGN_TOKENS.textSubtle,
  textInverse: DESIGN_TOKENS.textInverse,
  accent: DESIGN_TOKENS.accent,
  accentSoft: DESIGN_TOKENS.accentSoft,
  accentBorder: DESIGN_TOKENS.accentBorder,
  success: DESIGN_TOKENS.success,
  warning: DESIGN_TOKENS.warning,
  error: DESIGN_TOKENS.error,
  shadow: DESIGN_TOKENS.shadow,
  fontDisplay: DESIGN_TOKENS.fontDisplay,
  fontMono: DESIGN_TOKENS.fontMono,
  fontBody: DESIGN_TOKENS.fontBody,
} as const;

const EXAMPLE_QUESTIONS = [
  'What did I learn about AI architecture?',
  'Summarize my notes on semantic search',
  'What are my thoughts on RAG systems?',
];

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

function sourceSimilarityPercent(source: RAGSource): number {
  return Math.max(
    0,
    Math.min(95, source.similarityPercent ?? Math.round((source.similarity || 0) * 100))
  );
}

function isNotEnoughEvidenceMessage(content: string): boolean {
  const normalized = content.toLowerCase();
  return normalized.includes("couldn't find enough")
    || normalized.includes('not enough reliable information')
    || normalized.includes('not enough in your notes');
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
  } = useAskPastSelf(workspaceId);
  const { user: userSettings, workspace: workspaceSettings } = useProductSettings(workspaceId);
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const showSourceCards = Boolean(
    userSettings?.settings.ai_search.show_source_cards ?? true
  ) && Boolean(workspaceSettings?.settings.ai_search.source_cards_default ?? true);
  const showSimilarity = Boolean(userSettings?.settings.ai_search.show_similarity_scores ?? true);
  const defaultTopK = userSettings?.settings.ai_search.default_top_k ?? 6;
  const similarityThreshold = workspaceSettings?.settings.ai_search.min_note_similarity ?? 0.46;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, followUpQuestions, streamingSources]);

  async function handleSendMessage() {
    if (!workspaceId || !input.trim() || loading) return;

    const userMessage = input;
    setInput('');
    await chat(userMessage, workspaceId, {
      topK: defaultTopK,
      similarityThreshold,
    });
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
    <div
      style={{
        display: 'flex',
        height: '100%',
        flexDirection: 'column',
        background: TT.panel,
        color: TT.text,
      }}
    >
      <div
        style={{
          borderBottom: `1px solid ${TT.border}`,
          background: `linear-gradient(135deg, ${TT.accentSoft} 0%, transparent 65%), ${TT.panel}`,
          padding: '18px 18px 16px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 7,
                marginBottom: 10,
                border: `1px solid ${TT.accentBorder}`,
                background: TT.accentSoft,
                borderRadius: 999,
                padding: '4px 9px',
                fontFamily: TT.fontMono,
                fontSize: 9,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: TT.accent,
              }}
            >
              <Sparkles size={11} />
              Grounded chat
            </div>
            <h2
              id="chat-title"
              style={{
                margin: 0,
                fontFamily: TT.fontDisplay,
                fontSize: 30,
                letterSpacing: '0.06em',
                lineHeight: 1,
                color: TT.text,
              }}
            >
              Ask Your Past Self
            </h2>
            <p
              style={{
                margin: '8px 0 0',
                fontFamily: TT.fontBody,
                fontSize: 12.5,
                lineHeight: 1.6,
                color: TT.textMuted,
                maxWidth: 360,
              }}
            >
              Search your notes with grounded answers, visible evidence, and follow-up prompts that stay anchored to your workspace.
            </p>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close chat"
              style={{
                width: 34,
                height: 34,
                borderRadius: 3,
                border: `1px solid ${TT.border}`,
                background: TT.panelRaised,
                color: TT.textMuted,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'border-color 0.15s, color 0.15s, background 0.15s',
                flexShrink: 0,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = TT.accent;
                e.currentTarget.style.color = TT.accent;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = TT.border;
                e.currentTarget.style.color = TT.textMuted;
              }}
            >
              <X size={15} />
            </button>
          )}
        </div>
      </div>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          background: `linear-gradient(180deg, transparent 0%, ${TT.canvas} 100%)`,
          padding: 18,
        }}
      >
        {visibleMessages.length === 0 && !loading && (
          <div
            style={{
              display: 'flex',
              minHeight: '100%',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <div
              style={{
                width: '100%',
                maxWidth: 420,
                border: `1px solid ${TT.border}`,
                background: TT.panelRaised,
                borderRadius: 6,
                padding: 22,
                boxShadow: TT.shadow,
              }}
            >
              <div
                style={{
                  display: 'inline-flex',
                  width: 46,
                  height: 46,
                  marginBottom: 14,
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 4,
                  background: TT.accentSoft,
                  border: `1px solid ${TT.accentBorder}`,
                  color: TT.accent,
                }}
              >
                <BookOpenText size={20} />
              </div>
              <h3
                style={{
                  margin: 0,
                  fontFamily: TT.fontDisplay,
                  fontSize: 24,
                  letterSpacing: '0.06em',
                  color: TT.text,
                }}
              >
                Search Your Notes
              </h3>
              <p
                style={{
                  margin: '8px 0 0',
                  fontFamily: TT.fontBody,
                  fontSize: 12.5,
                  lineHeight: 1.7,
                  color: TT.textMuted,
                }}
              >
                Ask questions your notes can actually support. Replies stay grounded in retrieved note evidence instead of drifting into generic chat.
              </p>
              <div
                style={{
                  display: 'grid',
                  gap: 8,
                  marginTop: 18,
                  paddingTop: 16,
                  borderTop: `1px solid ${TT.border}`,
                }}
              >
                <div
                  style={{
                    fontFamily: TT.fontMono,
                    fontSize: 9.5,
                    letterSpacing: '0.07em',
                    textTransform: 'uppercase',
                    color: TT.textSubtle,
                  }}
                >
                  Example prompts
                </div>
                {EXAMPLE_QUESTIONS.map((example) => (
                  <button
                    key={example}
                    onClick={() => setInput(example)}
                    style={{
                      width: '100%',
                      borderRadius: 4,
                      border: `1px solid ${TT.border}`,
                      background: TT.panel,
                      padding: '10px 12px',
                      textAlign: 'left',
                      fontFamily: TT.fontBody,
                      fontSize: 12,
                      color: TT.textMuted,
                      cursor: 'pointer',
                      transition: 'border-color 0.15s, color 0.15s, transform 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = TT.accent;
                      e.currentTarget.style.color = TT.text;
                      e.currentTarget.style.transform = 'translateY(-1px)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = TT.border;
                      e.currentTarget.style.color = TT.textMuted;
                      e.currentTarget.style.transform = 'translateY(0)';
                    }}
                  >
                    “{example}”
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {visibleMessages.map((message: Message, index: number) => {
          const isUser = message.role === 'user';
          const needsWarningTone = !isUser && isNotEnoughEvidenceMessage(message.content);

          return (
            <div
              key={`${message.role}-${index}`}
              style={{
                display: 'flex',
                justifyContent: isUser ? 'flex-end' : 'flex-start',
                marginBottom: 14,
              }}
            >
              <div
                style={{
                  width: '100%',
                  maxWidth: 430,
                  borderRadius: 6,
                  border: `1px solid ${isUser ? TT.accentBorder : needsWarningTone ? TT.accentBorder : TT.border}`,
                  background: isUser ? TT.accentSoft : TT.panelRaised,
                  padding: 14,
                  boxShadow: isUser ? 'none' : TT.shadow,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    marginBottom: 8,
                    fontFamily: TT.fontMono,
                    fontSize: 9,
                    letterSpacing: '0.07em',
                    textTransform: 'uppercase',
                    color: isUser ? TT.accent : TT.textSubtle,
                  }}
                >
                  {isUser ? <ArrowUp size={11} /> : <Search size={11} />}
                  {isUser ? 'You asked' : 'Retrieved answer'}
                </div>
                <p
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    fontFamily: TT.fontBody,
                    fontSize: 13,
                    lineHeight: 1.7,
                    color: needsWarningTone ? TT.warning : TT.text,
                  }}
                >
                  {message.content}
                </p>

                {showSourceCards && message.sources && message.sources.length > 0 && (
                  <div
                    style={{
                      display: 'grid',
                      gap: 10,
                      marginTop: 14,
                      paddingTop: 14,
                      borderTop: `1px solid ${TT.border}`,
                    }}
                  >
                    <div
                      style={{
                        fontFamily: TT.fontMono,
                        fontSize: 9,
                        letterSpacing: '0.07em',
                        textTransform: 'uppercase',
                        color: TT.textSubtle,
                      }}
                    >
                      Source evidence
                    </div>

                    {message.sources.map((source: RAGSource, sourceIndex: number) => {
                      const percent = sourceSimilarityPercent(source);
                      return (
                        <div
                          key={source.sourceId || source.chunkId || sourceIndex}
                          style={{
                            borderRadius: 4,
                            border: `1px solid ${TT.border}`,
                            background: TT.panel,
                            padding: 12,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 6 }}>
                            <a
                              href={`/app/notes/${source.noteId}`}
                              title={source.title}
                              style={{
                                color: TT.text,
                                textDecoration: 'none',
                                fontFamily: TT.fontMono,
                                fontSize: 11,
                                lineHeight: 1.5,
                                flex: 1,
                              }}
                            >
                              {source.title}
                            </a>
                            <span
                              style={{
                                whiteSpace: 'nowrap',
                                fontFamily: TT.fontMono,
                                fontSize: 9,
                                color: TT.textSubtle,
                              }}
                            >
                              {formatDate(source.createdAt)}
                            </span>
                          </div>

                          <p
                            style={{
                              margin: 0,
                              fontFamily: TT.fontBody,
                              fontSize: 11.5,
                              lineHeight: 1.6,
                              color: TT.textMuted,
                            }}
                          >
                            {source.excerpt}
                          </p>

                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                            {source.citation && (
                              <span
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: 5,
                                  borderRadius: 999,
                                  border: `1px solid ${TT.border}`,
                                  background: TT.panelRaised,
                                  padding: '3px 8px',
                                  fontFamily: TT.fontMono,
                                  fontSize: 8.5,
                                  letterSpacing: '0.05em',
                                  textTransform: 'uppercase',
                                  color: TT.textMuted,
                                }}
                              >
                                <BookOpenText size={10} />
                                {source.citation}
                              </span>
                            )}
                            {typeof source.chunkIndex === 'number' && (
                              <span
                                style={{
                                  borderRadius: 999,
                                  border: `1px solid ${TT.border}`,
                                  background: TT.panelRaised,
                                  padding: '3px 8px',
                                  fontFamily: TT.fontMono,
                                  fontSize: 8.5,
                                  letterSpacing: '0.05em',
                                  textTransform: 'uppercase',
                                  color: TT.textMuted,
                                }}
                              >
                                Chunk {source.chunkIndex + 1}
                              </span>
                            )}
                            {source.supportLevel && (
                              <span
                                style={{
                                  borderRadius: 999,
                                  border: `1px solid ${source.supportLevel === 'supported' ? TT.accentBorder : TT.borderStrong}`,
                                  background: source.supportLevel === 'supported' ? TT.accentSoft : TT.panelRaised,
                                  padding: '3px 8px',
                                  fontFamily: TT.fontMono,
                                  fontSize: 8.5,
                                  letterSpacing: '0.05em',
                                  textTransform: 'uppercase',
                                  color: source.supportLevel === 'supported' ? TT.accent : TT.textMuted,
                                }}
                              >
                                {source.supportLevel}
                              </span>
                            )}
                          </div>

                          {showSimilarity && (
                            <div style={{ marginTop: 10 }}>
                              <div
                                style={{
                                  height: 6,
                                  overflow: 'hidden',
                                  borderRadius: 999,
                                  background: TT.panelRaised,
                                  border: `1px solid ${TT.border}`,
                                }}
                              >
                                <div
                                  style={{
                                    height: '100%',
                                    width: `${percent}%`,
                                    background: `linear-gradient(90deg, ${TT.accent} 0%, ${TT.success} 100%)`,
                                    borderRadius: 999,
                                    transition: 'width 0.25s ease',
                                  }}
                                />
                              </div>
                              <div
                                style={{
                                  marginTop: 6,
                                  fontFamily: TT.fontMono,
                                  fontSize: 9,
                                  color: TT.textSubtle,
                                }}
                              >
                                {percent}% note evidence match
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {loading && streamingSources.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 14 }}>
            <div
              style={{
                width: '100%',
                maxWidth: 430,
                borderRadius: 6,
                border: `1px solid ${TT.border}`,
                background: TT.panelRaised,
                padding: 14,
              }}
            >
              <div
                style={{
                  marginBottom: 10,
                  fontFamily: TT.fontMono,
                  fontSize: 10,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: TT.textSubtle,
                }}
              >
                Searching your notes...
              </div>

              {streamingSources.map((source: RAGSource, index: number) => (
                <div
                  key={source.sourceId || source.chunkId || index}
                  style={{
                    borderRadius: 4,
                    border: `1px solid ${TT.border}`,
                    background: TT.panel,
                    padding: 12,
                    opacity: 0.78,
                    marginTop: index === 0 ? 0 : 10,
                  }}
                >
                  <div style={{ width: '68%', height: 12, borderRadius: 999, background: TT.borderStrong, marginBottom: 8 }} />
                  <div style={{ width: '100%', height: 10, borderRadius: 999, background: TT.border, marginBottom: 6 }} />
                  <div style={{ width: '82%', height: 10, borderRadius: 999, background: TT.border }} />
                </div>
              ))}
            </div>
          </div>
        )}

        {loading && streamingSources.length === 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 14 }}>
            <div
              style={{
                borderRadius: 6,
                border: `1px solid ${TT.border}`,
                background: TT.panelRaised,
                padding: '12px 14px',
                fontFamily: TT.fontMono,
                fontSize: 10,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                color: TT.textSubtle,
              }}
            >
              Checking your notes...
            </div>
          </div>
        )}

        {!loading && followUpQuestions.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 14 }}>
            <div
              style={{
                width: '100%',
                maxWidth: 430,
                borderRadius: 6,
                border: `1px solid ${TT.border}`,
                background: TT.panelRaised,
                padding: 14,
              }}
            >
              <div
                style={{
                  marginBottom: 10,
                  fontFamily: TT.fontMono,
                  fontSize: 9,
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                  color: TT.textSubtle,
                }}
              >
                Suggested follow-ups
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {followUpQuestions.map((question, index) => (
                  <button
                    key={`${question}-${index}`}
                    onClick={() => {
                      if (!workspaceId) return;
                      setInput('');
                      void chat(question, workspaceId, {
                        topK: defaultTopK,
                        similarityThreshold,
                      });
                    }}
                    style={{
                      borderRadius: 999,
                      border: `1px solid ${TT.border}`,
                      background: TT.panel,
                      padding: '8px 12px',
                      fontFamily: TT.fontBody,
                      fontSize: 11.5,
                      color: TT.textMuted,
                      cursor: 'pointer',
                      transition: 'border-color 0.15s, color 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = TT.accent;
                      e.currentTarget.style.color = TT.text;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = TT.border;
                      e.currentTarget.style.color = TT.textMuted;
                    }}
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

      <div
        style={{
          borderTop: `1px solid ${TT.border}`,
          background: TT.panel,
          padding: 16,
        }}
      >
        <div style={{ display: 'flex', gap: 10, alignItems: 'stretch' }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            disabled={loading}
            placeholder="Ask about your notes, decisions, research, or past ideas..."
            style={{
              flex: 1,
              resize: 'none',
              borderRadius: 6,
              border: `1px solid ${TT.border}`,
              background: TT.panelRaised,
              padding: '12px 14px',
              color: TT.text,
              outline: 'none',
              fontFamily: TT.fontBody,
              fontSize: 13,
              lineHeight: 1.6,
              boxSizing: 'border-box',
            }}
          />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 112 }}>
            <button
              onClick={loading ? cancelChat : handleSendMessage}
              disabled={!workspaceId || (!loading && !input.trim())}
              style={{
                flex: 1,
                minHeight: 46,
                borderRadius: 6,
                border: `1px solid ${loading ? TT.error : TT.accent}`,
                background: loading ? 'rgba(217,45,32,0.08)' : TT.accent,
                color: loading ? TT.error : TT.textInverse,
                cursor: !workspaceId || (!loading && !input.trim()) ? 'not-allowed' : 'pointer',
                opacity: !workspaceId || (!loading && !input.trim()) ? 0.5 : 1,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
                fontFamily: TT.fontMono,
                fontSize: 10,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                transition: 'transform 0.15s ease, filter 0.15s ease',
              }}
              onMouseEnter={(e) => {
                if (!e.currentTarget.disabled) {
                  e.currentTarget.style.transform = 'translateY(-1px)';
                  e.currentTarget.style.filter = 'brightness(1.03)';
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.filter = 'none';
              }}
            >
              {loading ? <Square size={12} /> : <ArrowUp size={12} />}
              {loading ? 'Stop' : 'Ask'}
            </button>
            {messages.length > 0 && (
              <button
                onClick={clearChat}
                disabled={loading}
                title="Clear chat history"
                style={{
                  minHeight: 38,
                  borderRadius: 6,
                  border: `1px solid ${TT.border}`,
                  background: TT.panelRaised,
                  color: TT.textMuted,
                  cursor: loading ? 'not-allowed' : 'pointer',
                  opacity: loading ? 0.5 : 1,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 7,
                  fontFamily: TT.fontMono,
                  fontSize: 9.5,
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                }}
              >
                <RotateCcw size={11} />
                Clear
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default AskPastSelf;
