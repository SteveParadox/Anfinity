# Real-time troubleshooting

Start with `GET /health`, browser Network → WS, FastAPI logs, Celery worker logs and `npx wrangler tail --env staging --format json`. Health only proves the Worker responds; it does not prove backend/Redis/AI readiness. Do not copy ticket query strings, authorization headers, note contents or synthesis text into issues.

| Symptom | Investigation / recovery |
| --- | --- |
| Legacy deployment opens a retired page / 404 | Use this branch's `npm run realtime:deploy -- --env staging`; do not use the managed PartyKit deploy workflow. |
| Wrangler login/account/token failure | Run `wrangler whoami`, select the real account; in CI check environment-scoped account ID and scoped API token. |
| Missing binding/class or migration | Match `NOTES` and `THINKING` bindings to exported classes and the versioned new-class migration. Do not delete/recreate production namespaces. |
| `/health` works, WS path 404 | Exact route is `/parties/main/note:<UUID>` or `/parties/main/thinking-session:<UUID>`; verify hostname and encoded room. Other namespaces are rejected. |
| HTTP 426 | A normal HTTP room GET needs a WebSocket upgrade. Use the provider/PartySocket. |
| HTTP 401 / repeated handshake failure | Check POST `/realtime/ticket`, original session expiration, resource access, Redis GETDEL support and stable encryption keys across API replicas. A consumed ticket cannot be replayed; reconnect must request a new one. |
| HTTP 403 | Compare exact browser Origin with `REALTIME_ALLOWED_ORIGINS`; confirm backend view/edit/control permissions. Do not use `*`. |
| HTTP 503 | Check public HTTPS backend URL, Worker/backend shared secret, backend availability and Redis. Blank production configuration deliberately fails closed. |
| Socket closes 4403 | Token expired, permissions revoked, or backend authorization unavailable. Restore legitimate access and reopen; no automatic edit fallback. |
| Connection loops | Inspect ticket endpoint first, then origin/host/path. Never restore long-lived bearer query strings. Permanent access errors stop ticket retries; transient handshake failures remain bounded by provider retry behavior. |
| Frontend uses localhost in production | Set `VITE_REALTIME_HOST` and redeploy Vercel (Vite variables are build-time). Runtime validation rejects localhost/insecure/public-path configuration. |
| Missing initial note / blank editor | Check backend note load. It fails closed instead of seeding empty state. Inspect snapshot manifest/chunks and retain a backup before repair. Do not erase browser IndexedDB during unsent-edit recovery. |
| `note_projection_failed` / `persistence_failed` | Durable snapshot remains. Compare current backend plaintext with the room's last projected text. Resolve external edits intentionally; never blindly retry a stale plaintext overwrite. Verify editor permissions and credentials. |
| Cursors/typing stale | Verify custom-message framing (`sendMessage` / `custom-message`) and onClose; multiple tabs share user presence until the last connection leaves. |
| Thinking run pending | Confirm Celery listens on `celery`, task `anfinity.thinking_synthesis` is registered and Redis broker is healthy. Reconnect host to redispatch pending work. |
| Thinking stream disconnects | Producer continues separately. Reconnect a controlling user; absolute full-text snapshots avoid duplicate text. Backend permissions apply to observers. |
| Worker killed during synthesis | PostgreSQL partial output survives. A stale streaming run is marked failed by a later observer after the configured timeout; review it before starting another run. Model-provider side effects are not automatically retried. |
| Search/Ask Your Past Self has stale text | Verify successful note projection first, then the existing backend search-index job. CRDT synchronization does not prove asynchronous indexing completed. |
| Note disappears after commenting | Comment callbacks update comment state only. Verify GET note and list filters; inspect any stale content callback before initial editor synchronization. Preserve data and add a focused regression reproduction. Live browser regression is still required. |

Worker events use structured names (`connection_attempt`, `connection_authorized`, `realtime_handshake_failed`, `note_auth_failed`, `thinking_auth_failed`, `note_projection_failed`, `thinking_request_failed`). Use Cloudflare dashboard request/error rates and log filters to establish alerts for handshake failures, authorization failures and persistence errors. Application room/user/content identifiers are deliberately omitted from routine logs. Cloudflare platform request IDs provide request correlation. Never configure log exports to retain full WS URLs or secret headers.
