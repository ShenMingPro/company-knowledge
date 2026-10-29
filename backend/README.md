# KnowBase FastAPI backend

The backend reuses the root `models.database` ORM models and domain services.
RAG and vector-store modules are imported only when a query or indexing job runs;
health checks and authentication do not require a configured model provider.

Run from the repository root:

```powershell
python scripts/init_db.py
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs`. The primary interface is under `/api`; `/api/v1`
is retained as a compatibility alias. Configure MySQL with `DATABASE_URL`; see
the repository `.env.example`. Redis provides read caching, endpoint rate limits,
JWT revocation, and document-index task state. Upload and reindex responses include
a `task_id`; authenticated owners or administrators can read it at
`GET /api/tasks/{task_id}`.

FastAPI `BackgroundTasks` executes indexing in the API process. Redis persists the
latest state, but it is not a durable work queue and cannot retry work interrupted by
a process crash. Use a dedicated Celery/RQ worker before scaling to multiple API
replicas. Cache and rate-limit operations fail soft when Redis is unavailable; logout
returns `503` when it cannot persist token revocation.

The application does not create schema or demo data during
startup, so initialization/migrations remain an explicit deployment step.
