# Cloudflare deployment

The legacy managed `partykit deploy` workflow has been replaced by Wrangler. Install this migration's backend and restart Celery before switching frontend traffic. The Worker and frontend must be rolled out together because tickets and YServer custom-message framing changed.

## Operator configuration

| Value | Where | Purpose |
| --- | --- | --- |
| Cloudflare account ID | local Wrangler selection / CI secret `CLOUDFLARE_ACCOUNT_ID` | Actual operator account |
| `CLOUDFLARE_API_TOKEN` | GitHub environment secret | Scoped Worker deployment permissions and required account read access |
| `REALTIME_SERVICE_SECRET` | Backend environment and Cloudflare secret | Worker-only ticket redemption, random >=32 chars |
| `ENCRYPTION_KEY` | Backend environment | Existing stable Fernet key, shared by API replicas |
| `REDIS_URL` | Backend environment | Existing Redis >=6.2, ticket TTL and Celery broker |
| `REALTIME_BACKEND_URL` | Wrangler environment vars / CI environment variable | Public HTTPS FastAPI base URL, including any API prefix |
| `REALTIME_ALLOWED_ORIGINS` | Wrangler vars / CI variable | Comma-separated exact frontend origins, no wildcard |
| `REALTIME_ENVIRONMENT` | Wrangler config | development / staging / production |
| `VITE_API_URL` | Vercel environment | Same FastAPI application base URL |
| `VITE_REALTIME_HOST` | Vercel environment | Actual generated Worker hostname, without paths |
| `REALTIME_PUBLIC_URL` | CI variable | Deployed Worker's HTTPS base URL |
| `REALTIME_ORIGIN` | CI variable | One allowed frontend origin for smoke checks |
| `REALTIME_SMOKE_BEARER` | CI secret | Dedicated limited test account access token |
| `REALTIME_SMOKE_NOTE_ID` | CI variable | A disposable note accessible to that account |

Never put service secrets or Cloudflare tokens in `VITE_` variables. Keep staged/prod backend, origins, secrets, smoke accounts and object namespaces separate. `PARTYKIT_BACKEND_URL` and `VITE_PARTYKIT_HOST` remain explicit temporary aliases for one release; new variables take precedence. They do not enable legacy authentication or hosting. Browser `?token=` authentication is not supported by the public Worker.

## Initial account inventory

Confirm whether an earlier PartyKit deployment existed and whether it used hosted storage or your own account. The repository does not contain verified account IDs or namespace mappings. The configured `v1-partyserver` migration creates **new** SQLite classes under new Worker names. It is not an in-place migration of an unknown legacy namespace. Do not change class names, bindings, migration tag, or Worker names after first deploy without a reviewed storage plan. Do not apply this new-class migration to an existing legacy class identity. Export/compare data before importing or switching traffic (see migration guide).

## Local staging / production commands

Set actual HTTPS backend/origins in the selected `env.staging.vars` or `env.production.vars` in `frontend/wrangler.jsonc`; blank defaults intentionally fail protected traffic. Do not invent account IDs or domains.

```bash
cd frontend
npm ci
npx wrangler login
npx wrangler whoami
npm run realtime:typecheck
npm run realtime:lint
npm run test:unit
npm run test:realtime
npm run build
npm run realtime:check -- --env staging
npx wrangler secret put REALTIME_SERVICE_SECRET --env staging
npm run realtime:deploy -- --env staging
```

Record the returned hostname. Configure staging Vercel variables (`VITE_REALTIME_HOST`, `VITE_API_URL`), redeploy Vercel, and verify authenticated smoke checks plus actual two-user note/session journeys. No Vercel WebSocket rewrite is required. Existing `vercel.json` remains unchanged.

After review and explicit production release authorization:

```bash
cd frontend
npm run realtime:check -- --env production
npx wrangler secret put REALTIME_SERVICE_SECRET --env production
npm run realtime:deploy -- --env production
npx wrangler tail --env production --format json
```

Use the actual production hostname in Vercel, redeploy, and run `npm run realtime:smoke` with the table's environment variables set. Supplying smoke credentials enables an authenticated handshake; it does not edit the note or invoke AI.

## CI/CD and rollback

`realtime.yml` validates PRs/main with clean installs, Worker types/lint, frontend unit tests, Worker runtime tests, frontend build and staging dry run. Existing whole-frontend lint debt is reported separately; it is not hidden by disabling rules. `realtime-deploy.yml` is manual: select staging or production. Production requires the main branch and the GitHub environment's configured protection rules. Configure required reviewers on production. Deployments serialize per environment and require authenticated smoke configuration. Secrets must already be provisioned with Wrangler; CI never prints them. Cloudflare API tokens must be limited to the intended account/Worker permissions.

For a code rollback, inspect `npx wrangler deployments list --env production`, then use `npx wrangler rollback <version-id> --env production` for a compatible PartyServer release. Roll back Vercel to a frontend that speaks the same protocol. Do not roll back to PartyKit-hosted clients. Reverting Worker code does **not** reverse Durable Object storage migrations; never deploy delete-class migrations as a rollback. Restore corrupted state through Cloudflare SQLite point-in-time recovery and/or verified backups, compare PostgreSQL projections, and reopen traffic only after integrity checks.

Actual Cloudflare account capabilities, first deployment, Vercel WSS, staged live AI and remote storage recovery remain UNVERIFIED until run with operator credentials.
