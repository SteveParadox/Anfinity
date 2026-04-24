import { useDeferredValue, useEffect, useMemo, useState, type CSSProperties, type FormEvent } from "react";
import { Brain, Copy, Loader2, RefreshCw, Search, Users, Vote, Wand2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { useThinkingSessionRoom } from "@/hooks/useThinkingSessionRoom";
import type {
  ThinkingSession,
  ThinkingSessionAccess,
  ThinkingSessionPhase,
  ThinkingSessionSummary,
} from "@/types";

const TT = {
  inkBlack: "#0A0A0A",
  inkDeep: "#111111",
  inkRaised: "#1A1A1A",
  inkBorder: "#252525",
  inkMuted: "#5A5A5A",
  inkSubtle: "#888888",
  snow: "#F5F5F5",
  yolk: "#F5E642",
  yolkSoft: "rgba(245,230,66,0.12)",
  red: "#FF4545",
  green: "#5BE37A",
  blue: "#7DD3FC",
  fontMono: "'IBM Plex Mono', monospace",
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
};

function phaseLabel(phase: ThinkingSessionPhase): string {
  return phase.charAt(0).toUpperCase() + phase.slice(1);
}

function formatTime(value?: Date | null): string {
  if (!value) {
    return "Just now";
  }

  return value.toLocaleString();
}

export function ThinkingSessionsView() {
  const { currentWorkspaceId, hasPermission, user } = useAuth();
  const canView = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, "chat", "view"));
  const canCreate = Boolean(currentWorkspaceId && hasPermission(currentWorkspaceId, "chat", "create"));

  const [sessions, setSessions] = useState<ThinkingSessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [sessionDetails, setSessionDetails] = useState<ThinkingSession | null>(null);
  const [sessionAccess, setSessionAccess] = useState<ThinkingSessionAccess | null>(null);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [submittingContribution, setSubmittingContribution] = useState(false);
  const [savingRefinement, setSavingRefinement] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [titleInput, setTitleInput] = useState("");
  const [contextInput, setContextInput] = useState("");
  const [contributionInput, setContributionInput] = useState("");
  const [refinementDraft, setRefinementDraft] = useState("");
  const [refinementDirty, setRefinementDirty] = useState(false);
  const [sessionSearch, setSessionSearch] = useState("");
  const [phaseFilter, setPhaseFilter] = useState<"all" | ThinkingSessionPhase>("all");
  const [contributionSearch, setContributionSearch] = useState("");

  const deferredSessionSearch = useDeferredValue(sessionSearch.trim().toLowerCase());
  const deferredContributionSearch = useDeferredValue(contributionSearch.trim().toLowerCase());

  const room = useThinkingSessionRoom({
    sessionId: selectedSessionId,
    token: api.getToken(),
    enabled: canView,
  });

  useEffect(() => {
    if (!currentWorkspaceId || !canView) {
      setSessions([]);
      setSelectedSessionId(null);
      setSessionDetails(null);
      return;
    }

    let disposed = false;
    setLoadingSessions(true);
    setError(null);

    void api.listThinkingSessions(currentWorkspaceId)
      .then((response) => {
        if (disposed) {
          return;
        }

        setSessions(response);
        setSelectedSessionId((current) => (
          current && response.some((session) => session.id === current)
            ? current
            : response[0]?.id ?? null
        ));
      })
      .catch((err) => {
        if (!disposed) {
          setError(err instanceof Error ? err.message : "Unable to load thinking sessions");
        }
      })
      .finally(() => {
        if (!disposed) {
          setLoadingSessions(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, [canView, currentWorkspaceId]);

  useEffect(() => {
    if (!selectedSessionId) {
      setSessionDetails(null);
      setSessionAccess(null);
      return;
    }

    let disposed = false;
    setLoadingDetails(true);

    void Promise.all([
      api.getThinkingSession(selectedSessionId),
      api.getThinkingSessionAccess(selectedSessionId),
    ])
      .then(([response, access]) => {
        if (!disposed) {
          setSessionDetails(response);
          setSessionAccess(access);
        }
      })
      .catch((err) => {
        if (!disposed) {
          setError(err instanceof Error ? err.message : "Unable to load the selected session");
        }
      })
      .finally(() => {
        if (!disposed) {
          setLoadingDetails(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, [selectedSessionId]);

  useEffect(() => {
    if (!room.session) {
      return;
    }

    const nextSession = room.session;
    setSessionDetails(nextSession);
    setSessionAccess((current) => (
      current && current.sessionId === nextSession.id
        ? { ...current, phase: nextSession.phase }
        : current
    ));
    setSessions((current) => {
      const summary: ThinkingSessionSummary = {
        id: nextSession.id,
        workspaceId: nextSession.workspaceId,
        noteId: nextSession.noteId ?? null,
        roomId: nextSession.roomId,
        title: nextSession.title,
        phase: nextSession.phase,
        hostUserId: nextSession.hostUserId,
        activeSynthesisRunId: nextSession.activeSynthesisRunId ?? null,
        createdAt: nextSession.createdAt,
        updatedAt: nextSession.updatedAt,
      };

      const withoutCurrent = current.filter((entry) => entry.id !== summary.id);
      return [summary, ...withoutCurrent].sort((left, right) => {
        const leftTime = left.updatedAt?.getTime() ?? 0;
        const rightTime = right.updatedAt?.getTime() ?? 0;
        return rightTime - leftTime;
      });
    });
  }, [room.session]);

  useEffect(() => {
    if (!sessionDetails) {
      setRefinementDraft("");
      setRefinementDirty(false);
      return;
    }

    if (!refinementDirty) {
      setRefinementDraft(sessionDetails.refinedOutput || sessionDetails.synthesisOutput || "");
    }
  }, [refinementDirty, sessionDetails?.id, sessionDetails?.phase, sessionDetails?.refinedOutput, sessionDetails?.synthesisOutput]);

  const activeSession = room.session ?? sessionDetails;
  const currentUserId = user?.id ?? "";
  const isHost = Boolean(sessionAccess?.isHost || (activeSession ? (activeSession.hostUserId === currentUserId || activeSession.createdByUserId === currentUserId) : false));
  const canControl = Boolean(sessionAccess?.canControl);
  const canParticipate = Boolean(sessionAccess?.canParticipate);

  const displayedParticipants = room.status === "connected" ? room.participants : [];
  const sessionCounts = useMemo(() => ({
    total: sessions.length,
    waiting: sessions.filter((session) => session.phase === "waiting").length,
    gathering: sessions.filter((session) => session.phase === "gathering").length,
    refining: sessions.filter((session) => session.phase === "refining").length,
    completed: sessions.filter((session) => session.phase === "completed").length,
  }), [sessions]);
  const filteredSessions = useMemo(() => {
    return sessions.filter((session) => {
      const matchesPhase = phaseFilter === "all" || session.phase === phaseFilter;
      const matchesQuery =
        !deferredSessionSearch ||
        [session.title, session.phase, session.roomId].join(" ").toLowerCase().includes(deferredSessionSearch);
      return matchesPhase && matchesQuery;
    });
  }, [deferredSessionSearch, phaseFilter, sessions]);

  const handleCreateSession = async (event: FormEvent) => {
    event.preventDefault();

    if (!currentWorkspaceId || !titleInput.trim()) {
      return;
    }

    setCreatingSession(true);
    setError(null);

    try {
      const created = await api.createThinkingSession({
        workspaceId: currentWorkspaceId,
        title: titleInput.trim(),
        promptContext: contextInput.trim() || undefined,
      });

      setSessions((current) => [{
        id: created.id,
        workspaceId: created.workspaceId,
        noteId: created.noteId ?? null,
        roomId: created.roomId,
        title: created.title,
        phase: created.phase,
        hostUserId: created.hostUserId,
        activeSynthesisRunId: created.activeSynthesisRunId ?? null,
        createdAt: created.createdAt,
        updatedAt: created.updatedAt,
      }, ...current.filter((entry) => entry.id !== created.id)]);
      setSelectedSessionId(created.id);
      setSessionDetails(created);
      setSessionAccess({
        sessionId: created.id,
        workspaceId: created.workspaceId,
        roomId: created.roomId,
        canView: true,
        canParticipate: true,
        canControl: true,
        isHost: true,
        phase: created.phase,
      });
      setTitleInput("");
      setContextInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create session");
    } finally {
      setCreatingSession(false);
    }
  };

  const handleSubmitContribution = async (event: FormEvent) => {
    event.preventDefault();
    if (!contributionInput.trim()) {
      return;
    }

    setSubmittingContribution(true);
    setError(null);
    try {
      if (!room.submitContribution(contributionInput.trim())) {
        return;
      }
      setContributionInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to submit contribution");
    } finally {
      setSubmittingContribution(false);
    }
  };

  const handleSaveRefinement = async () => {
    if (!activeSession) {
      return;
    }

    setSavingRefinement(true);
    setError(null);
    try {
      if (room.updateRefinement(refinementDraft)) {
        setRefinementDirty(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save refinement");
    } finally {
      setSavingRefinement(false);
    }
  };

  const synthesisText = activeSession?.phase === "completed"
    ? activeSession.finalOutput
    : activeSession?.phase === "refining"
      ? activeSession.refinedOutput || activeSession.synthesisOutput
      : activeSession?.synthesisOutput || "";
  const filteredContributions = (activeSession?.contributions || []).filter((contribution) => {
    if (!deferredContributionSearch) {
      return true;
    }

    return [contribution.content, contribution.author?.name || ""].join(" ").toLowerCase().includes(deferredContributionSearch);
  });

  if (!currentWorkspaceId) {
    return (
      <div style={{ padding: 32, color: TT.inkSubtle, fontFamily: TT.fontMono }}>
        Select a workspace to use Live Thinking Sessions.
      </div>
    );
  }

  if (!canView) {
    return (
      <div style={{ padding: 32, color: TT.inkSubtle, fontFamily: TT.fontMono }}>
        You do not have permission to view live thinking sessions in this workspace.
      </div>
    );
  }

  return (
    <div style={{ padding: 24, color: TT.snow, fontFamily: TT.fontMono }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24, gap: 16 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <Brain size={18} color={TT.yolk} />
            <h1 style={{ margin: 0, fontFamily: TT.fontDisplay, letterSpacing: "0.08em", fontSize: 28 }}>
              Live Thinking Sessions
            </h1>
          </div>
          <p style={{ margin: 0, color: TT.inkSubtle, fontSize: 12 }}>
            Gather ideas together, vote, synthesize live, refine, and finish with a durable shared result.
          </p>
        </div>

        <div style={{ fontSize: 11, color: room.status === "connected" ? TT.green : TT.inkSubtle }}>
          Room status: {room.status}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginBottom: 16 }}>
        {[
          { label: "Total", value: sessionCounts.total, helper: "all sessions" },
          { label: "Gathering", value: sessionCounts.gathering, helper: "ideas in progress" },
          { label: "Refining", value: sessionCounts.refining, helper: "editing output" },
          { label: "Completed", value: sessionCounts.completed, helper: "ready to reuse" },
        ].map(({ label, value, helper }) => (
          <div key={label} style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 10, padding: 14 }}>
            <div style={{ fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase", color: TT.inkSubtle, marginBottom: 8 }}>
              {label}
            </div>
            <div style={{ fontFamily: TT.fontDisplay, fontSize: 26, color: TT.snow, letterSpacing: "0.05em", lineHeight: 1 }}>
              {value}
            </div>
            <div style={{ fontSize: 11, color: TT.inkMuted, marginTop: 6 }}>{helper}</div>
          </div>
        ))}
      </div>

      {error || room.lastError ? (
        <div style={{
          marginBottom: 16,
          padding: "10px 12px",
          border: `1px solid rgba(255,69,69,0.3)`,
          background: "rgba(255,69,69,0.08)",
          color: "#FFC8C8",
          borderRadius: 6,
          fontSize: 12,
        }}>
          {room.lastError || error}
        </div>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "340px minmax(0, 1fr)", gap: 20, alignItems: "start" }}>
        <aside style={{
          display: "flex",
          flexDirection: "column",
          gap: 16,
          position: "sticky",
          top: 16,
        }}>
          <form
            onSubmit={handleCreateSession}
            style={{
              background: TT.inkDeep,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 10,
              padding: 16,
            }}
          >
            <h2 style={{ margin: "0 0 12px 0", fontSize: 13, color: TT.yolk, textTransform: "uppercase", letterSpacing: "0.08em" }}>
              New Session
            </h2>
            <input
              value={titleInput}
              onChange={(event) => setTitleInput(event.target.value)}
              placeholder="Session title"
              disabled={!canCreate || creatingSession}
              style={{
                width: "100%",
                padding: "10px 12px",
                marginBottom: 10,
                borderRadius: 6,
                border: `1px solid ${TT.inkBorder}`,
                background: TT.inkRaised,
                color: TT.snow,
                fontFamily: TT.fontMono,
              }}
            />
            <textarea
              value={contextInput}
              onChange={(event) => setContextInput(event.target.value)}
              placeholder="Optional prompt context"
              disabled={!canCreate || creatingSession}
              rows={4}
              style={{
                width: "100%",
                padding: "10px 12px",
                marginBottom: 12,
                borderRadius: 6,
                border: `1px solid ${TT.inkBorder}`,
                background: TT.inkRaised,
                color: TT.snow,
                fontFamily: TT.fontMono,
                resize: "vertical",
              }}
            />
            <button
              type="submit"
              disabled={!canCreate || creatingSession || !titleInput.trim()}
              style={{
                width: "100%",
                height: 38,
                borderRadius: 6,
                border: `1px solid ${TT.yolk}`,
                background: canCreate ? TT.yolk : TT.inkRaised,
                color: TT.inkBlack,
                fontFamily: TT.fontMono,
                cursor: canCreate ? "pointer" : "not-allowed",
                opacity: canCreate ? 1 : 0.55,
              }}
            >
              {creatingSession ? "Creating..." : "Create Session"}
            </button>
          </form>

          <div style={{
            background: TT.inkDeep,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: 10,
            padding: 16,
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <h2 style={{ margin: 0, fontSize: 13, color: TT.yolk, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Sessions
              </h2>
              <button
                type="button"
                onClick={() => {
                  if (!currentWorkspaceId || !canView) return;
                  setLoadingSessions(true);
                  setError(null);
                  void api.listThinkingSessions(currentWorkspaceId)
                    .then((response) => {
                      setSessions(response);
                      setSelectedSessionId((current) => (
                        current && response.some((session) => session.id === current)
                          ? current
                          : response[0]?.id ?? null
                      ));
                    })
                    .catch((err) => {
                      setError(err instanceof Error ? err.message : "Unable to load thinking sessions");
                    })
                    .finally(() => setLoadingSessions(false));
                }}
                disabled={loadingSessions}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: `1px solid ${TT.inkBorder}`,
                  background: TT.inkRaised,
                  color: TT.snow,
                  fontFamily: TT.fontMono,
                  fontSize: 10,
                  cursor: "pointer",
                }}
              >
                <RefreshCw size={12} className={loadingSessions ? "animate-spin" : undefined} />
                Refresh
              </button>
            </div>

            <div style={{ display: "grid", gap: 10, marginBottom: 12 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  background: TT.inkBlack,
                  border: `1px solid ${TT.inkBorder}`,
                  borderRadius: 8,
                  padding: "0 10px",
                  height: 36,
                }}
              >
                <Search size={12} color={TT.inkMuted} />
                <input
                  value={sessionSearch}
                  onChange={(event) => setSessionSearch(event.target.value)}
                  placeholder="Search sessions"
                  aria-label="Search sessions"
                  style={{ flex: 1, height: "100%", background: "transparent", border: "none", color: TT.snow, outline: "none", fontFamily: TT.fontMono, fontSize: 12 }}
                />
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {(["all", "waiting", "gathering", "synthesizing", "refining", "completed"] as const).map((phase) => (
                  <button
                    key={phase}
                    type="button"
                    onClick={() => setPhaseFilter(phase)}
                    aria-pressed={phaseFilter === phase}
                    style={{
                      borderRadius: 999,
                      border: `1px solid ${phaseFilter === phase ? "rgba(245,230,66,0.28)" : TT.inkBorder}`,
                      background: phaseFilter === phase ? TT.yolkSoft : TT.inkBlack,
                      color: phaseFilter === phase ? TT.yolk : TT.inkMuted,
                      padding: "6px 10px",
                      fontSize: 10,
                      letterSpacing: "0.08em",
                      textTransform: "uppercase",
                      cursor: "pointer",
                    }}
                  >
                    {phase}
                  </button>
                ))}
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {filteredSessions.length === 0 && !loadingSessions ? (
                <div style={{ color: TT.inkSubtle, fontSize: 12 }}>
                  No sessions match the current filters.
                </div>
              ) : null}

              {filteredSessions.map((session) => {
                const selected = session.id === selectedSessionId;
                return (
                  <button
                    key={session.id}
                    type="button"
                    onClick={() => setSelectedSessionId(session.id)}
                    style={{
                      textAlign: "left",
                      padding: 12,
                      borderRadius: 8,
                      border: `1px solid ${selected ? "rgba(245,230,66,0.4)" : TT.inkBorder}`,
                      background: selected ? TT.yolkSoft : TT.inkRaised,
                      color: TT.snow,
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ fontSize: 12, marginBottom: 6 }}>{session.title}</div>
                    <div style={{ fontSize: 10, color: TT.inkSubtle, display: "flex", justifyContent: "space-between" }}>
                      <span>{phaseLabel(session.phase)}</span>
                      <span>{formatTime(session.updatedAt)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </aside>

        <section style={{ minWidth: 0 }}>
          {!activeSession ? (
            <div style={{
              background: TT.inkDeep,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 10,
              padding: 24,
              color: TT.inkSubtle,
            }}>
              {loadingDetails ? "Loading session..." : "Select a session to begin."}
            </div>
          ) : (
            <div style={{ display: "grid", gap: 16 }}>
              <div style={{
                background: TT.inkDeep,
                border: `1px solid ${TT.inkBorder}`,
                borderRadius: 10,
                padding: 18,
              }}>
                <div style={{ display: "flex", alignItems: "start", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
                  <div>
                    <h2 style={{ margin: "0 0 6px 0", fontSize: 24, fontFamily: TT.fontDisplay, letterSpacing: "0.05em" }}>
                      {activeSession.title}
                    </h2>
                    <div style={{ fontSize: 11, color: TT.inkSubtle }}>
                      Phase: <span style={{ color: TT.yolk }}>{phaseLabel(activeSession.phase)}</span>
                      {" · "}
                      Updated {formatTime(activeSession.updatedAt)}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
                    {activeSession.phase === "waiting" && canControl ? (
                      <button
                        type="button"
                        onClick={() => room.transitionPhase("gathering")}
                        style={actionButtonStyle(TT.blue)}
                      >
                        Start Gathering
                      </button>
                    ) : null}
                    {activeSession.phase === "gathering" && canControl ? (
                      <button
                        type="button"
                        onClick={() => room.transitionPhase("synthesizing")}
                        style={actionButtonStyle(TT.yolk)}
                      >
                        Start Synthesis
                      </button>
                    ) : null}
                    {activeSession.phase === "refining" && canControl ? (
                      <button
                        type="button"
                        onClick={() => room.transitionPhase("completed")}
                        style={actionButtonStyle(TT.green)}
                      >
                        Complete Session
                      </button>
                    ) : null}
                  </div>
                </div>

                {activeSession.promptContext ? (
                  <div style={{
                    padding: 12,
                    borderRadius: 8,
                    background: TT.inkRaised,
                    border: `1px solid ${TT.inkBorder}`,
                    fontSize: 12,
                    color: TT.inkSubtle,
                    whiteSpace: "pre-wrap",
                  }}>
                    {activeSession.promptContext}
                  </div>
                ) : null}

                <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 14 }}>
                  <span style={sessionInfoPillStyle}>{activeSession.contributions.length} contributions</span>
                  <span style={sessionInfoPillStyle}>{displayedParticipants.length} live participants</span>
                  {activeSession.activeSynthesisRunId ? <span style={{ ...sessionInfoPillStyle, color: TT.yolk }}>Synthesis running</span> : null}
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "260px minmax(0, 1fr)", gap: 16 }}>
                <div style={{
                  background: TT.inkDeep,
                  border: `1px solid ${TT.inkBorder}`,
                  borderRadius: 10,
                  padding: 16,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <Users size={14} color={TT.yolk} />
                    <h3 style={{ margin: 0, fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                      Participants
                    </h3>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {displayedParticipants.map((participant) => (
                      <div
                        key={participant.userId}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: 10,
                          borderRadius: 8,
                          background: TT.inkRaised,
                          border: `1px solid ${TT.inkBorder}`,
                        }}
                      >
                        <div style={{
                          width: 12,
                          height: 12,
                          borderRadius: 999,
                          background: participant.color,
                          flexShrink: 0,
                        }} />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {participant.name}
                            {participant.isHost ? " · Host" : ""}
                          </div>
                          <div style={{ fontSize: 10, color: TT.inkSubtle }}>
                            {participant.connectionCount} connection{participant.connectionCount === 1 ? "" : "s"}
                          </div>
                        </div>
                      </div>
                    ))}
                    {displayedParticipants.length === 0 ? (
                      <div style={{ color: TT.inkSubtle, fontSize: 12 }}>
                        {room.status === "connected"
                          ? "No active participants yet."
                          : "Live participant presence is unavailable while the room is disconnected."}
                      </div>
                    ) : null}
                  </div>
                </div>

                <div style={{ display: "grid", gap: 16 }}>
                  <div style={{
                    background: TT.inkDeep,
                    border: `1px solid ${TT.inkBorder}`,
                    borderRadius: 10,
                    padding: 16,
                  }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                      <h3 style={{ margin: 0, fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                        Contributions
                      </h3>
                      <span style={{ color: TT.inkSubtle, fontSize: 10 }}>
                        {filteredContributions.length} visible
                      </span>
                    </div>

                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        background: TT.inkBlack,
                        border: `1px solid ${TT.inkBorder}`,
                        borderRadius: 8,
                        padding: "0 10px",
                        height: 36,
                        marginBottom: 14,
                      }}
                    >
                      <Search size={12} color={TT.inkMuted} />
                      <input
                        value={contributionSearch}
                        onChange={(event) => setContributionSearch(event.target.value)}
                        placeholder="Search contributions"
                        aria-label="Search contributions"
                        style={{ flex: 1, height: "100%", background: "transparent", border: "none", color: TT.snow, outline: "none", fontFamily: TT.fontMono, fontSize: 12 }}
                      />
                    </div>

                    {activeSession.phase === "gathering" && canParticipate ? (
                      <form onSubmit={handleSubmitContribution} style={{ marginBottom: 16 }}>
                        <textarea
                          value={contributionInput}
                          onChange={(event) => setContributionInput(event.target.value)}
                          placeholder="Add a contribution for the group to vote on"
                          rows={4}
                          style={textareaStyle}
                        />
                        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                          <button
                            type="submit"
                            disabled={submittingContribution || !contributionInput.trim()}
                            style={actionButtonStyle(TT.yolk)}
                          >
                            {submittingContribution ? "Submitting..." : "Submit Idea"}
                          </button>
                        </div>
                      </form>
                    ) : null}

                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      {filteredContributions.map((contribution) => {
                        const hasVoted = contribution.voterUserIds.includes(currentUserId);
                        return (
                          <div
                            key={contribution.id}
                            style={{
                              padding: 14,
                              borderRadius: 10,
                              background: TT.inkRaised,
                              border: `1px solid ${TT.inkBorder}`,
                            }}
                          >
                            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
                              <div style={{ fontSize: 11, color: TT.inkSubtle }}>
                                Rank #{contribution.rank} · {contribution.author?.name || "Collaborator"}
                              </div>
                              <div style={{ fontSize: 11, color: TT.inkSubtle }}>
                                {contribution.voteCount} vote{contribution.voteCount === 1 ? "" : "s"}
                              </div>
                            </div>
                            <div style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6, marginBottom: 12 }}>
                              {contribution.content}
                            </div>
                            {activeSession.phase === "gathering" && canParticipate ? (
                              <button
                                type="button"
                                onClick={() => room.toggleVote(contribution.id)}
                                style={actionButtonStyle(hasVoted ? TT.green : TT.blue)}
                              >
                                <Vote size={13} />
                                {hasVoted ? "Remove Vote" : "Vote"}
                              </button>
                            ) : null}
                          </div>
                        );
                      })}
                      {filteredContributions.length === 0 ? (
                        <div style={{ color: TT.inkSubtle, fontSize: 12 }}>
                          {activeSession.contributions.length === 0
                            ? "No contributions yet. Start gathering ideas to move this session forward."
                            : "No contributions match that search."}
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div style={{
                    background: TT.inkDeep,
                    border: `1px solid ${TT.inkBorder}`,
                    borderRadius: 10,
                    padding: 16,
                  }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
                      <h3 style={{ margin: 0, fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                        Synthesis
                      </h3>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {room.activeRunId ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 6, color: TT.yolk, fontSize: 10 }}>
                            <Wand2 size={12} />
                            Streaming live
                          </div>
                        ) : null}
                        {synthesisText ? (
                          <button
                            type="button"
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(synthesisText);
                              } catch (err) {
                                setError(err instanceof Error ? err.message : "Unable to copy synthesis");
                              }
                            }}
                            style={actionButtonStyle(TT.blue)}
                          >
                            <Copy size={12} />
                            Copy
                          </button>
                        ) : null}
                      </div>
                    </div>

                    <div style={{
                      minHeight: 160,
                      padding: 14,
                      borderRadius: 10,
                      background: TT.inkRaised,
                      border: `1px solid ${TT.inkBorder}`,
                      whiteSpace: "pre-wrap",
                      fontSize: 13,
                      lineHeight: 1.7,
                    }}>
                      {synthesisText || (
                        activeSession.phase === "synthesizing"
                          ? "Waiting for live synthesis output..."
                          : "No synthesis output yet."
                      )}
                    </div>

                    {activeSession.phase === "refining" ? (
                      <div style={{ marginTop: 16 }}>
                        <div style={{ fontSize: 11, color: TT.inkSubtle, marginBottom: 8 }}>
                          Refine the synthesis before completing the session.
                        </div>
                        <textarea
                          value={refinementDraft}
                          onChange={(event) => {
                            setRefinementDraft(event.target.value);
                            setRefinementDirty(true);
                          }}
                          rows={10}
                          style={textareaStyle}
                        />
                        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                          <button
                            type="button"
                            onClick={handleSaveRefinement}
                            disabled={savingRefinement || !refinementDirty}
                            style={actionButtonStyle(TT.green)}
                          >
                            {savingRefinement ? "Saving..." : "Save Refinement"}
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

const textareaStyle: CSSProperties = {
  width: "100%",
  padding: "12px 14px",
  borderRadius: 8,
  border: `1px solid ${TT.inkBorder}`,
  background: TT.inkRaised,
  color: TT.snow,
  fontFamily: TT.fontMono,
  resize: "vertical",
};

const sessionInfoPillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "5px 8px",
  borderRadius: 999,
  border: `1px solid ${TT.inkBorder}`,
  background: TT.inkRaised,
  color: TT.inkSubtle,
  fontFamily: TT.fontMono,
  fontSize: 10,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
};

function actionButtonStyle(color: string): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    padding: "9px 12px",
    borderRadius: 8,
    border: `1px solid ${color}`,
    background: color === TT.yolk ? TT.yolk : "transparent",
    color: color === TT.yolk ? TT.inkBlack : color,
    fontFamily: TT.fontMono,
    fontSize: 11,
    cursor: "pointer",
  };
}

export default ThinkingSessionsView;
