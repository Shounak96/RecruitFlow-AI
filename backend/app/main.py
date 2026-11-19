from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.drafts_api import router as drafts_router
from app.gmail_api import router as gmail_router
from app.history_api import router as history_router
from app.settings_api import router as settings_router

settings = get_settings()

app = FastAPI(title="RecruitFlow AI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(settings.frontend_origin).rstrip("/")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(settings_router)
app.include_router(drafts_router)
app.include_router(gmail_router)
app.include_router(history_router)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}

