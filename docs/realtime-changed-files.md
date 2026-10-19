# Changed-file inventory

| Path | Purpose |
| --- | --- |
| `.github/workflows/realtime-deploy.yml` | Validation and manual environment-scoped deployment workflow. |
| `.github/workflows/realtime.yml` | Validation and manual environment-scoped deployment workflow. |
| `.gitignore` | Exclude local dependencies, credentials and generated runtime state. |
| `README.md` | Replace obsolete deployment guidance and link new runbooks. |
| `Server/README.md` | Replace obsolete deployment guidance and link new runbooks. |
| `Server/app/api/notes.py` | Accept idempotent identical collaboration projections before stale-base validation. |
| `Server/app/api/realtime.py` | Issue encrypted resource-scoped tickets and atomically redeem them. |
| `Server/app/api/thinking_sessions.py` | Observe durable synthesis progress independently of the producer. |
| `Server/app/celery_app.py` | Register synthesis task in existing Celery infrastructure. |
| `Server/app/config.py` | Backend-only Worker redemption secret setting. |
| `Server/app/main.py` | Register new realtime ticket router. |
| `Server/app/tasks/thinking_synthesis.py` | Durable Celery synthesis producer with atomic backend run claim. |
| `Server/tests/test_note_comments_service.py` | Backend ticket/synthesis security tests or regression fixture maintenance. |
| `Server/tests/test_realtime_tickets.py` | Backend ticket/synthesis security tests or regression fixture maintenance. |
| `Server/tests/test_thinking_synthesis_task.py` | Backend ticket/synthesis security tests or regression fixture maintenance. |
| `automation-registry.manifest.json` | Restore missing shared registry source after comparing both generated registries. |
| `docs/cloudflare-deployment.md` | Architecture, migration, validation, deployment and recovery documentation. |
| `docs/realtime-architecture.md` | Architecture, migration, validation, deployment and recovery documentation. |
| `docs/realtime-local-development.md` | Architecture, migration, validation, deployment and recovery documentation. |
| `docs/realtime-migration.md` | Architecture, migration, validation, deployment and recovery documentation. |
| `docs/realtime-troubleshooting.md` | Architecture, migration, validation, deployment and recovery documentation. |
| `docs/realtime-validation.md` | Architecture, migration, validation, deployment and recovery documentation. |
| `frontend/.dev.vars.example` | Safe local secret template. |
| `frontend/README.md` | Replace obsolete deployment guidance and link new runbooks. |
| `frontend/package-lock.json` | PartyServer/YServer/Wrangler dependencies and reproducible command scripts. |
| `frontend/package.json` | PartyServer/YServer/Wrangler dependencies and reproducible command scripts. |
| `frontend/partykit.json` | Remove obsolete managed hosting configuration. |
| `frontend/partykit/collaboration-server.test.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/collaboration-server.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/security.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/server.test.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/server.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/snapshot.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/test-runtime.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/test-types.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/thinking-session-server.test.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/partykit/thinking-session-server.ts` | Migrated server, security/routing/persistence, or retained application unit tests and fixtures. |
| `frontend/src/components/notes/CollaborativeNoteEditor.tsx` | Guard initial blank callbacks, synchronize editability and accessible status. |
| `frontend/src/hooks/useThinkingSessionRoom.ts` | Ticket-based PartySocket reconnection and access-error feedback. |
| `frontend/src/lib/api.ts` | Authenticated ticket issuance client. |
| `frontend/src/lib/collaboration/constants.ts` | Central host validation, environment aliases and local port migration. |
| `frontend/src/lib/collaboration/noteRoom.ts` | Central host validation, environment aliases and local port migration. |
| `frontend/src/lib/collaboration/provider.ts` | YServer provider/custom framing, tickets, retry bound and browser edit recovery. |
| `frontend/src/sections/NotesView.tsx` | Remove competing browser plaintext writer and misleading save state. |
| `frontend/tools/realtime-smoke.mjs` | Remote health/routing and optional authenticated WebSocket smoke checks. |
| `frontend/tsconfig.worker.json` | Separate Node unit and Cloudflare runtime tests and Worker type checking. |
| `frontend/vite.config.ts` | Chunk new collaboration library with existing editor dependencies. |
| `frontend/vitest.config.ts` | Separate Node unit and Cloudflare runtime tests and Worker type checking. |
| `frontend/vitest.worker.config.ts` | Separate Node unit and Cloudflare runtime tests and Worker type checking. |
| `frontend/worker-tests/realtime.test.ts` | Real workerd WebSocket, CRDT, authorization, restart and storage tests. |
| `frontend/wrangler.jsonc` | Separate local/staging/production namespaces, SQLite migration and Worker bindings. |
| `docs/realtime-changed-files.md` | This inventory. |
| `Server/app/services/note_invites.py` | Lock and refresh note records for collaboration compare-and-swap. |
| `Server/app/services/thinking_sessions.py` | Refresh locked ORM state and recheck stalled synthesis heartbeats atomically. |
| `Server/tests/test_realtime_consistency.py` | Real SQLAlchemy stale-identity regressions and stale-heartbeat race coverage. |
| `Server/tests/test_thinking_synthesis_observer.py` | Observer permission, database lifetime, and superseded-run regressions. |
| `frontend/src/lib/collaboration/initialConnection.ts` | Abortable bounded retry policy for first-ticket acquisition. |
| `frontend/src/lib/collaboration/initialConnection.test.ts` | Transient/permanent failure and cancellation regression tests. |
| `frontend/src/lib/collaboration/localPersistence.ts` | Bounded and failure-safe browser CRDT update storage with cross-tab-safe compaction. |
| `frontend/src/lib/collaboration/localPersistence.test.ts` | Recovery, corruption, blocked storage, and multi-tab compaction tests. |
| `frontend/src/lib/collaboration/provider.test.tsx` | Hook unmount, token rotation, and invalid configuration tests. |
| `frontend/src/lib/collaboration/content.ts` | Lossless plaintext whitespace round trips for backend projections. |
| `frontend/src/lib/collaboration/content.test.ts` | Whitespace-only and trailing blank-line regression tests. |
| `frontend/src/lib/collaboration/noteRoom.test.ts` | Explicit protocol and production-host validation regressions. |
| `Server/tests/test_notes_api_helpers.py` | Required content and locked CAS API regression tests. |
