# BloxServer API

Backend API for BloxServer (OpenBlox.ai) - Visual AI Agent Workflow Builder.

## Quick Start

### With Docker Compose (Recommended)

```bash
cd bloxserver

# Start PostgreSQL, Redis, and API
docker-compose up -d

# Check logs
docker-compose logs -f api

# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### Local Development

```bash
cd bloxserver

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env
# Edit .env with your settings

# Start PostgreSQL and Redis (or use Docker)
docker-compose up -d postgres redis

# Run the API
python -m bloxserver.api.main
# Or with uvicorn directly:
uvicorn bloxserver.api.main:app --reload
```

## API Endpoints

### Health

- `GET /health` - Basic health check
- `GET /health/ready` - Readiness check (includes DB)
- `GET /health/live` - Liveness check

### Flows

- `GET /api/v1/flows` - List flows
- `POST /api/v1/flows` - Create flow
- `GET /api/v1/flows/{id}` - Get flow
- `PATCH /api/v1/flows/{id}` - Update flow
- `DELETE /api/v1/flows/{id}` - Delete flow
- `POST /api/v1/flows/{id}/start` - Start flow
- `POST /api/v1/flows/{id}/stop` - Stop flow

### Triggers

- `GET /api/v1/flows/{flow_id}/triggers` - List triggers
- `POST /api/v1/flows/{flow_id}/triggers` - Create trigger
- `GET /api/v1/flows/{flow_id}/triggers/{id}` - Get trigger
- `DELETE /api/v1/flows/{flow_id}/triggers/{id}` - Delete trigger
- `POST /api/v1/flows/{flow_id}/triggers/{id}/regenerate-token` - Regenerate webhook token

### Executions

- `GET /api/v1/flows/{flow_id}/executions` - List executions
- `GET /api/v1/flows/{flow_id}/executions/{id}` - Get execution
- `POST /api/v1/flows/{flow_id}/executions/run` - Manual trigger
- `GET /api/v1/flows/{flow_id}/executions/stats` - Get stats

### Webhooks

- `POST /webhooks/{token}` - Trigger flow via webhook
- `GET /webhooks/{token}/test` - Test webhook token

## Project Structure

```
bloxserver/
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── dependencies.py      # Auth, DB session dependencies
│   ├── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py      # SQLAlchemy engine/session
│   │   └── tables.py        # ORM table definitions
│   └── routes/
│       ├── __init__.py
│       ├── flows.py         # Flow CRUD
│       ├── triggers.py      # Trigger CRUD
│       ├── executions.py    # Execution history
│       ├── webhooks.py      # Webhook handler
│       └── health.py        # Health checks
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Authentication

Uses Clerk for JWT authentication. All `/api/v1/*` endpoints require a valid JWT.

```bash
curl -H "Authorization: Bearer <clerk-jwt>" \
     http://localhost:8000/api/v1/flows
```

## Environment Variables

See `.env.example` for all configuration options.

Key variables:
- `DATABASE_URL` - PostgreSQL connection string
- `CLERK_ISSUER` - Clerk JWT issuer URL
- `STRIPE_SECRET_KEY` - Stripe API key
- `API_KEY_ENCRYPTION_KEY` - Fernet key for encrypting user API keys

## Database Migrations

Using Alembic for migrations (not yet set up):

```bash
# Initialize (first time)
alembic init alembic

# Create migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

## Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest tests/ -v
```

## Deployment

### Railway / Render / Fly.io

1. Connect your repo
2. Set environment variables
3. Deploy

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bloxserver-api
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: api
        image: your-registry/bloxserver-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: bloxserver-secrets
              key: database-url
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8000
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
```

## Next Steps

- [ ] Alembic migrations setup
- [ ] Stripe webhook handlers
- [ ] Redis rate limiting
- [ ] Container orchestration integration
- [ ] WebSocket for real-time logs
