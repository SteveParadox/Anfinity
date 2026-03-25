# CogniFlow Backend

Production-ready backend for the CogniFlow AI Knowledge Operating System.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   FastAPI   │────▶│   Celery    │────▶│    Redis    │
│   (API)     │     │  (Worker)   │     │   (Queue)   │
└─────────────┘     └─────────────┘     └─────────────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  PostgreSQL │     │   Qdrant    │     │    S3/MinIO │
│  (Metadata) │     │  (Vectors)  │     │   (Storage) │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Quick Start

### 1. Clone and Setup

```bash
cd cogniflow-backend
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start Services

```bash
docker-compose up -d
```

This starts:
- FastAPI (http://localhost:8000)
- PostgreSQL (port 5432)
- Redis (port 6379)
- Qdrant (http://localhost:6333)
- MinIO (http://localhost:9000)
- Celery Worker
- Celery Beat

### 3. Run Migrations

```bash
docker-compose exec api alembic upgrade head
```

### 4. API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API Endpoints

### Documents
- `POST /documents/upload` - Upload document
- `GET /documents` - List documents
- `GET /documents/{id}` - Get document
- `DELETE /documents/{id}` - Delete document

### Notes
- `POST /notes` - Create note
- `GET /notes` - List notes
- `GET /notes/{id}` - Get note
- `PATCH /notes/{id}` - Update note
- `DELETE /notes/{id}` - Delete note

### Workspaces
- `POST /workspaces` - Create workspace
- `GET /workspaces` - List workspaces
- `GET /workspaces/{id}` - Get workspace
- `PATCH /workspaces/{id}` - Update workspace

### Search
- `POST /search?workspace_id={id}` - Semantic search

## Ingestion Pipeline

1. **Upload** → S3 storage
2. **Parse** → Extract text (PDF, Word, Markdown)
3. **Chunk** → Smart chunking with overlap
4. **Embed** → Generate embeddings (OpenAI/Cohere/BGE)
5. **Index** → Store in Qdrant

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection | `postgresql://postgres:postgres@localhost:5432/cogniflow` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `QDRANT_URL` | Qdrant connection | `http://localhost:6333` |
| `S3_ENDPOINT_URL` | S3 endpoint | `http://localhost:9000` |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `EMBEDDING_PROVIDER` | Embedding provider | `openai` |

## Development

### Local Setup (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start API
uvicorn app.main:app --reload

# Start Celery worker (in another terminal)
# NOTE: -A parameter must point to the Celery APP instance (app.celery_app), not a task module
# NOTE: Using --pool=solo on Windows to avoid billiard compatibility issues
python -m celery -A app.celery_app worker --pool=solo --loglevel=info
```

### Running Tests

```bash
pytest
```

## Production Deployment

### Environment Variables

```bash
ENVIRONMENT=production
DEBUG=false
JWT_SECRET=<strong-random-secret>
DATABASE_URL=<postgres-url>
REDIS_URL=<redis-url>
QDRANT_URL=<qdrant-url>
QDRANT_API_KEY=<qdrant-api-key>
AWS_ACCESS_KEY_ID=<aws-key>
AWS_SECRET_ACCESS_KEY=<aws-secret>
S3_BUCKET_NAME=cogniflow-prod
OPENAI_API_KEY=<openai-key>
```

### Docker Compose Production

```yaml
version: '3.8'
services:
  api:
    image: cogniflow/api:latest
    environment:
      - ENVIRONMENT=production
    deploy:
      replicas: 3
  
  worker:
    image: cogniflow/api:latest
    command: celery -A app.tasks.worker worker --concurrency=8
    deploy:
      replicas: 2
```

## Monitoring

- Flower (Celery monitoring): http://localhost:5555
- Qdrant Dashboard: http://localhost:6333/dashboard
- MinIO Console: http://localhost:9001

## License

MIT
