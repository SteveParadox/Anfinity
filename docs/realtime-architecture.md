# Real-time architecture

Vercel serves React/Vite. Browsers call FastAPI for identity, resource permissions, notes, workspaces, search and AI. A separate Cloudflare Worker coordinates WebSockets through PartyServer and SQLite-backed Durable Objects. PostgreSQL remains authoritative for application records. No WebSocket proxy is needed in Vercel.

| Resource | Public WebSocket path | Binding / class |
| --- | --- | --- |
| Note | `/parties/main/note:<UUID>` | `NOTES` / `NoteCollaborationServer` |
| Thinking session | `/parties/main/thinking-session:<UUID>` | `THINKING` / `ThinkingSessionServer` |

The Worker accepts only these paths, checks exact Origin membership, redeems a resource-scoped ticket, removes browser identity headers, and obtains current identity/access from FastAPI. Only trusted server headers reach the Durable Object. Bearer credentials are removed from the connection URI. There is no direct public binding route that bypasses this middleware.

## Authentication

An authenticated POST `/realtime/ticket` checks existing backend resource permissions. Its random ticket expires after 60 seconds. Redis >=6.2 stores the encrypted bearer credential under a SHA-256 ticket key. Atomic GETDEL at `/realtime/redeem` prevents replay across API replicas. Redemption additionally requires `REALTIME_SERVICE_SECRET`, shared only by FastAPI and the Worker. Existing `ENCRYPTION_KEY` must match across API replicas. Redis failure fails closed. Ticket and redemption responses use `Cache-Control: no-store`.

Every incoming room message checks current backend view access. Note access updates the YServer read-only decision before processing binary sync/update frames. Thinking mutations also retain their backend permission checks. Idle connections are revalidated every 30 seconds; revoked readers may receive events within that documented interval. Backend unavailability closes connections rather than preserving stale permissions. Client-supplied awareness names are presentation data, never authorization claims.

Messages are limited to 1 MiB and 120 frames/second/connection. Application JSON limits remain smaller. These limits do not replace account-level abuse controls: use Cloudflare WAF/rate limits for connection floods. Presence, typing and cursor state are transient and cleaned on disconnect; multiple tabs aggregate by authenticated user.

## Note persistence and consistency

YServer handles binary CRDT synchronization and `__YPS:` custom-message framing. The client uses `sendMessage` and `custom-message`. Both Yjs update frames and sync step 2 honor server-side read-only access. Cross-tab Yjs broadcast channels are disabled to prevent bypassing Worker authorization.

On first room use, a successful backend note fetch seeds the CRDT exactly once. Failed loading does not initialize an empty authoritative document. Thereafter the Durable Object snapshot is the authority for concurrent editing history. Full encoded Yjs updates and the last projected backend text are stored atomically in 64 KiB binary chunks; a manifest identifies complete snapshots. Missing chunks fail closed. Each accepted frame checkpoints state, and Cloudflare output gates storage-dependent responses. `onSave` serializes backend projections using `base_content` compare-and-swap. Identical projections are idempotent. The browser no longer runs a competing plaintext writer.

Plaintext projection keeps PostgreSQL/search usable; rich CRDT history stays in the Durable Object. A backend CAS conflict is visible as `persistence_failed` and retains the room snapshot instead of overwriting external edits. Recover by comparing both copies and applying an intentional CRDT edit. There is no automatic destructive reset. Failed projection retries on later saves/room close; a reopened editor also supplies fresh credentials. Snapshot persistence and backend projection are distinct guarantees: “Live sync active” means CRDT synchronization, not a verified search-index update.

Browser IndexedDB retains Yjs updates under user-and-note-scoped keys, including across reloads. Credential refresh reuses the same in-memory document for that user/note. Local content is still sensitive on shared devices; sign-out does not currently erase IndexedDB. A reader with revoked access cannot upload cached updates. Editing waits for synchronization, preventing initial blank editor callbacks from replacing backend content.

## Thinking sessions and synthesis

PostgreSQL remains authoritative for lifecycle, contributions, votes, refinement, and run status. The room serializes incoming operations, obtains fresh snapshots on join/ping, and avoids retrying non-idempotent HTTP mutations. Database transition/run-claim logic remains in FastAPI. A request without an application operation identifier can still be submitted twice by a client; full exactly-once contribution/vote semantics are not claimed.

Synthesis runs in the existing Celery/Redis infrastructure under `anfinity.thinking_synthesis`, with task-local database/provider lifetimes. SSE requests observe committed progress using separate short-lived database sessions. Disconnecting a Worker does not cancel the producer. Reconnection observes pending or streaming runs and uses absolute `full_text`, preventing replayed chunks from duplicating output. A database PENDING→STREAMING claim prevents duplicate provider runs. Partial output commits at bounded intervals. Interrupted STREAMING runs are marked failed when an observer sees stale progress after twice the configured timeout (minimum 120 seconds); operators can then start a new run. There is no claim of uninterrupted execution across a killed backend worker or guaranteed queue publication after a database commit. Pending work can be dispatched again by reconnecting the host.

## Capacity and cost

One object coordinates each note/session; there is no global room registry. Hibernation remains disabled to support current YServer state, timers and SSE relays. Active connections incur Durable Object duration charges; frequent snapshots incur storage writes and permission checks incur backend load. There is no fixed dollar-cost promise. Measure connection duration, messages, snapshot sizes and backend latency under representative load before enabling broad usage. Evaluate hibernation only with attachment/presence restoration and persistence tests. Check current Cloudflare limits/pricing for the chosen account plan.
