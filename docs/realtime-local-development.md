# Local real-time development

Use Node.js 24 (minimum 22), Python compatible with `Server/requirements.txt`, Redis >=6.2, PostgreSQL and the application's existing services. Wrangler and the Worker test runtime are installed by npm. A Cloudflare login is not needed for local Worker tests.

1. Configure/start FastAPI using `Server/README.md` and its existing environment. Set `REALTIME_SERVICE_SECRET` to a random value of at least 32 characters. Set a stable `ENCRYPTION_KEY` for multiple API processes. Start Redis and the application's database/search services. The frontend and Worker must use the same FastAPI URL.
2. Start the backend and Celery in separate terminals:

```bash
cd Server
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

```bash
cd Server
python -m celery -A app.celery_app:celery_app worker -Q celery --loglevel=info
```

3. Install/start the Worker:

```bash
cd frontend
npm ci
cp .dev.vars.example .dev.vars
# Replace REALTIME_SERVICE_SECRET with the same backend value.
npm run realtime:dev
```

Wrangler uses port **8787**, backend `http://127.0.0.1:8080`, origins `http://localhost:5173,http://127.0.0.1:5173`. Edit local Wrangler vars if your backend uses a different port. Avoid changing shared Redis port settings unrelated to this migration.

4. Start Vite in another terminal:

```bash
cd frontend
# .env.local: VITE_REALTIME_HOST=localhost:8787
# .env.local: VITE_API_URL=http://127.0.0.1:8080
npm run dev
```

5. Sign into two authorized accounts, open the same existing note, edit concurrently, inspect cursors/typing, disconnect one tab, continue editing, then reconnect/reload. A viewer should receive updates but cannot mutate binary Yjs state. Inspect Worker `note_projection_failed` events and verify FastAPI returns the latest plaintext.
6. Create a thinking session, join as host and participant, contribute/vote, transition through phases and start synthesis. A configured provider and running Celery worker are required. Disconnect the host during synthesis, reconnect, and verify completion and history from PostgreSQL.

```bash
npm run realtime:typecheck
npm run realtime:lint
npm run test:unit
npm run test:realtime
npm run build
npm run realtime:check -- --env staging
```

Worker runtime tests run real Durable Objects/WebSocket upgrades with mocked backend HTTP and include concurrent clients and a forced room restart. Node unit mocks cover application events; neither suite proves a live deployment or paid AI provider behavior.
