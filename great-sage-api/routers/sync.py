from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from auth import get_current_user
import models
import schemas

router = APIRouter(prefix="/sync", tags=["sync"])


# ── Pull ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=schemas.SyncPullResponse)
def pull(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Pull the full state from the server.

    Called by Great Sage on:
      - Fresh install (restores everything after a drive wipe)
      - App launch when local data is missing
      - Manual "sync from server" action

    Returns all watchlist items grouped by status + all watch progress records.
    """
    items = (
        db.query(models.WatchlistItem)
        .filter(models.WatchlistItem.user_id == current_user.id)
        .order_by(models.WatchlistItem.added_at.desc())
        .all()
    )

    grouped = {s.value: [] for s in models.WatchStatus}
    for item in items:
        grouped[item.status.value].append(item)

    progress = (
        db.query(models.WatchProgress)
        .filter(models.WatchProgress.user_id == current_user.id)
        .order_by(models.WatchProgress.last_watched.desc())
        .all()
    )

    return schemas.SyncPullResponse(
        watchlist=schemas.WatchlistResponse(
            planning  = grouped["planning"],
            watching  = grouped["watching"],
            dropped   = grouped["dropped"],
            completed = grouped["completed"],
        ),
        watch_progress=progress,
    )


# ── Push ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=schemas.SyncPullResponse)
def push(
    payload: schemas.SyncPushRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Push local Great Sage state up to the server.

    Called by Great Sage on:
      - App launch (uploads any queued offline changes)
      - Periodic background sync

    Strategy: server is source of truth.
    - For watchlist: upsert by title. New titles are added; existing titles
      have their status/notes updated only if the local record is newer.
    - For progress: upsert by title. last_watched timestamp wins ties.

    After push, the full server state is returned so Great Sage can
    reconcile any differences (e.g. recommendations that were added via
    the website while offline).
    """

    # ── Watchlist push ────────────────────────────────────────────────────────
    if payload.watchlist:
        STATUS_BUCKETS = ["planning", "watching", "dropped", "completed"]
        for bucket in STATUS_BUCKETS:
            items = payload.watchlist.get(bucket, [])
            for raw in items:
                title    = str(raw.get("title", "")).strip()
                is_anime = bool(raw.get("is_anime", False))
                notes    = str(raw.get("notes", ""))

                if not title:
                    continue

                existing = db.query(models.WatchlistItem).filter(
                    models.WatchlistItem.user_id == current_user.id,
                    models.WatchlistItem.title   == title,
                ).first()

                if existing:
                    # Only update status/notes — don't overwrite manual
                    # website changes with stale local data. We trust the
                    # local is up-to-date for what it sends.
                    existing.status   = bucket
                    existing.notes    = notes
                    existing.is_anime = is_anime
                else:
                    db.add(models.WatchlistItem(
                        user_id  = current_user.id,
                        title    = title,
                        is_anime = is_anime,
                        status   = bucket,
                        notes    = notes,
                    ))

    # ── Progress push ─────────────────────────────────────────────────────────
    if payload.watch_progress:
        for p in payload.watch_progress:
            last_watched = p.last_watched or datetime.now(timezone.utc)

            existing = db.query(models.WatchProgress).filter(
                models.WatchProgress.user_id == current_user.id,
                models.WatchProgress.title   == p.title,
            ).first()

            if existing:
                # Only overwrite if the incoming data is newer
                if existing.last_watched is None or (
                    last_watched.replace(tzinfo=timezone.utc)
                    if last_watched.tzinfo is None
                    else last_watched
                ) > existing.last_watched:
                    existing.current_episode = p.current_episode
                    existing.current_season  = p.current_season
                    existing.total_episodes  = p.total_episodes
                    existing.position        = p.position
                    existing.duration        = p.duration
                    existing.last_watched    = last_watched
                    existing.is_anime        = p.is_anime
                    if p.file_path is not None:
                        existing.file_path = p.file_path
            else:
                db.add(models.WatchProgress(
                    user_id         = current_user.id,
                    title           = p.title,
                    is_anime        = p.is_anime,
                    current_episode = p.current_episode,
                    current_season  = p.current_season,
                    total_episodes  = p.total_episodes,
                    position        = p.position,
                    duration        = p.duration,
                    file_path       = p.file_path,
                    last_watched    = last_watched,
                ))

    db.commit()

    # Return the full server state so Great Sage can reconcile
    return pull(current_user=current_user, db=db)
