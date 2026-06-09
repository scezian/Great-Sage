import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from database import engine, Base
import models  # ensure all models are registered before create_all

from routers import auth, users, watchlist, progress, sync, friends, recommendations

load_dotenv()

# ── Create tables ─────────────────────────────────────────────────────────────
# In production you should use Alembic migrations instead of create_all.
# For local dev this is fine.
Base.metadata.create_all(bind=engine)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title       = os.getenv("APP_NAME", "Great Sage API"),
    description = "Backend for the Great Sage social platform — watchlist sync, friend recommendations, and backup/restore.",
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# During development allow all origins. Lock this down to your actual
# frontend domain before going to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(watchlist.router)
app.include_router(progress.router)
app.include_router(sync.router)
app.include_router(friends.router)
app.include_router(recommendations.router)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "app": os.getenv("APP_NAME", "Great Sage API")}


@app.get("/", tags=["meta"])
def root():
    return {
        "message": "Great Sage API is running",
        "docs":    "/docs",
    }
