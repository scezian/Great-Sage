from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from auth import get_current_user
import models
import schemas

router = APIRouter(prefix="/progress", tags=["progress"])


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[schemas.WatchProgressResponse])
def get_all_progress(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return progress for every show the user has watched."""
    return (
        db.query(models.WatchProgress)
        .filter(models.WatchProgress.user_id == current_user.id)
        .order_by(models.WatchProgress.last_watched.desc())
        .all()
    )


@router.get("/{title:path}", response_model=schemas.WatchProgressResponse)
def get_progress_by_title(
    title: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch progress for a specific show by title."""
    record = db.query(models.WatchProgress).filter(
        models.WatchProgress.user_id == current_user.id,
        models.WatchProgress.title   == title,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="No progress found for that title")
    return record


# ── Upsert ────────────────────────────────────────────────────────────────────

@router.put("", response_model=schemas.WatchProgressResponse)
def upsert_progress(
    payload: schemas.WatchProgressUpsert,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create or update watch progress for a show.

    Great Sage calls this every time playback state changes
    (episode advance, position save, etc.).
    If a record for this title already exists it is updated in-place;
    otherwise a new one is created.
    """
    record = db.query(models.WatchProgress).filter(
        models.WatchProgress.user_id == current_user.id,
        models.WatchProgress.title   == payload.title,
    ).first()

    last_watched = payload.last_watched or datetime.now(timezone.utc)

    if record:
        record.is_anime        = payload.is_anime
        record.current_episode = payload.current_episode
        record.current_season  = payload.current_season
        record.total_episodes  = payload.total_episodes
        record.position        = payload.position
        record.duration        = payload.duration
        record.last_watched    = last_watched
        if payload.file_path is not None:
            record.file_path = payload.file_path
    else:
        record = models.WatchProgress(
            user_id         = current_user.id,
            title           = payload.title,
            is_anime        = payload.is_anime,
            current_episode = payload.current_episode,
            current_season  = payload.current_season,
            total_episodes  = payload.total_episodes,
            position        = payload.position,
            duration        = payload.duration,
            file_path       = payload.file_path,
            last_watched    = last_watched,
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{title:path}", response_model=schemas.MessageResponse)
def delete_progress(
    title: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove progress for a show (e.g. when it's removed from the watching list)."""
    record = db.query(models.WatchProgress).filter(
        models.WatchProgress.user_id == current_user.id,
        models.WatchProgress.title   == title,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="No progress found for that title")
    db.delete(record)
    db.commit()
    return {"message": f"Progress for '{title}' deleted"}
