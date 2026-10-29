"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.routers import audits, auth, chat, documents, health, spaces, tasks


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="KnowBase knowledge management and permission-aware RAG HTTP interface.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for route_module in (health, auth, spaces, chat, documents, audits, tasks):
    app.include_router(route_module.router, prefix=settings.api_prefix)

# Keep the versioned path available for existing deployment health checks and clients.
compatibility_prefix = "/api/v1" if settings.api_prefix != "/api/v1" else "/api"
for route_module in (health, auth, spaces, chat, documents, audits, tasks):
    app.include_router(
        route_module.router,
        prefix=compatibility_prefix,
        include_in_schema=False,
    )


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs", "health": f"{settings.api_prefix}/health"}
