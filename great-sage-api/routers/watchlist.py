from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from auth import get_current_user
import models
import schemas

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _get_item_or_404(db: Session, item_id: int, user_id: int) -> models.WatchlistItem:
    item = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.id      == item_id,
        models.WatchlistItem.user_id == user_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return item


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=schemas.WatchlistResponse)
def get_watchlist(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the full watchlist grouped into the four status buckets —
    mirrors the exact structure Great Sage uses locally.
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

    return schemas.WatchlistResponse(
        planning  = grouped["planning"],
        watching  = grouped["watching"],
        dropped   = grouped["dropped"],
        completed = grouped["completed"],
    )


@router.get("/{item_id}", response_model=schemas.WatchlistItemResponse)
def get_item(
    item_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_item_or_404(db, item_id, current_user.id)


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("", response_model=schemas.WatchlistItemResponse, status_code=201)
def add_item(
    payload: schemas.WatchlistItemCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a show to the watchlist. Title must be unique per user."""
    existing = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.user_id == current_user.id,
        models.WatchlistItem.title   == payload.title,
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"'{payload.title}' is already in your watchlist"
        )

    item = models.WatchlistItem(
        user_id  = current_user.id,
        title    = payload.title,
        is_anime = payload.is_anime,
        status   = payload.status,
        notes    = payload.notes,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ── Update ────────────────────────────────────────────────────────────────────

@router.patch("/{item_id}", response_model=schemas.WatchlistItemResponse)
def update_item(
    item_id: int,
    payload: schemas.WatchlistItemUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Partially update status, notes, or is_anime flag."""
    item = _get_item_or_404(db, item_id, current_user.id)

    if payload.status   is not None: item.status   = payload.status
    if payload.notes    is not None: item.notes     = payload.notes
    if payload.is_anime is not None: item.is_anime  = payload.is_anime

    db.commit()
    db.refresh(item)
    return item


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{item_id}", response_model=schemas.MessageResponse)
def remove_item(
    item_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = _get_item_or_404(db, item_id, current_user.id)
    db.delete(item)
    db.commit()
    return {"message": f"'{item.title}' removed from watchlist"}
