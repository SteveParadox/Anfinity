# Real-time migration dependency map

Baseline: main b891ab1. Vite editor → y-partykit provider → /parties/main/note:UUID → root delegate → backend /auth/me and /notes/UUID/access → Yjs snapshot in PartyKit room storage. Editor plaintext → NotesView debounce → PATCH /notes/UUID/collaboration with base_content → PostgreSQL/search indexing. Presence/cursors/typing use raw JSON beside binary Yjs.

Thinking UI → PartySocket → /parties/main/thinking-session:UUID → access and identity checks → backend participant/contribution/vote/transition/refinement endpoints. PostgreSQL owns session state and synthesis run claim; Worker relays SSE and progress. Existing synthesis execution is coupled to streaming and needs explicit verification.

Migration: separate NoteCollaborationServer and ThinkingSessionServer Durable Objects; strict main-path routing; resource-scoped one-use Redis tickets; FastAPI remains permission authority. YServer handles CRDT frames and custom-message framing; SQLite Durable Object storage holds binary snapshots. No legacy account bindings or recoverable exports are present in the repository. Remote deployment/data existence remains UNVERIFIED; do not redirect production until inventory and backups are completed.

Baseline validation: npm ci passed; frontend build passed; unit tests 100 passed / 1 failed (missing root automation-registry.manifest.json); lint 340 errors / 16 warnings.

## Data cutover decision and operator record

No authenticated Cloudflare/PartyKit inventory, production database dump, room export or existing namespace map was available during implementation. Whether production rooms exist is **UNVERIFIED**. The implementation therefore neither claims recovery nor installs speculative export endpoints on the retired platform.

Before cutover, record: deployment mode (managed vs own-account), account/Worker/class names, room IDs from authorized note/session records, snapshot locations, export timestamps, byte sizes and SHA-256 checksums, note/session counts, operator and validation results. Freeze old editing before the final export. Back up PostgreSQL and any recoverable legacy room storage. Existing Yjs binaries must be exported as binary or explicitly base64-encoded bytes, decoded with Yjs `applyUpdate`, and compared against the backend projection and known document examples. A JSON serialization of a Uint8Array object is not an interchangeable export format.

For own-account storage, inspect class identity and actual storage backend before planning a transfer; never register the old class as a new SQLite class. This branch creates isolated new namespaces only. For managed hosting, use existing accessible read/export capabilities or prior backups; do not assume a new export endpoint can be deployed. If no remote state is recoverable, compare PostgreSQL and prior exports, document any lost CRDT-only edits, and obtain a recovery decision before redirecting users. Backend seeding preserves recoverable plaintext but cannot recreate missing historical CRDT identities, formatting or unsaved updates.

If no previous successful deployment existed, new rooms initialize from authorized backend note/session records. No remote migration job is necessary. Validate representative notes, nonempty documents, view-only users and completed session history before opening writes.

## Compatibility and rollback

The room IDs and default `main` path remain stable. Runtime APIs changed to `Server`/`YServer`, `onMessage(connection, message)`, `this.name`, `this.env`, `this.ctx.storage`; Yjs custom strings use the new provider framing. Old raw custom-message clients must be redeployed. Environment aliases are temporary; old bearer query authentication is not accepted publicly. Staging and production namespaces are separate. Keep an immutable pre-cutover backup and a compatible PartyServer build; a code rollback cannot undo storage migrations. See the deployment/recovery guides.

## Independent review limitations

Exactly-once application operations across ambiguous backend timeouts are not guaranteed. Non-idempotent mutations are not retried automatically; current backend locking and synthesis claims remain in force. Model jobs execute independently of WebSockets but require the existing broker/worker infrastructure; PostgreSQL commit and broker publication are not an atomic outbox transaction. Live account configuration, full browser workflows for comments/search/Ask Your Past Self, production legacy recovery and provider execution require staging verification. Those acceptance items remain UNVERIFIED.
