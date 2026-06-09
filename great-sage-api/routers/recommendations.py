from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import List

from database import get_db
from auth import get_current_user
import models
import schemas

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _are_friends(db: Session, user_a_id: int, user_b_id: int) -> bool:
    return db.query(models.Friendship).filter(
        models.Friendship.status == models.FriendshipStatus.accepted,
        or_(
            and_(
                models.Friendship.requester_id == user_a_id,
                models.Friendship.receiver_id  == user_b_id,
            ),
            and_(
                models.Friendship.requester_id == user_b_id,
                models.Friendship.receiver_id  == user_a_id,
            ),
        )
    ).first() is not None


# ── Send ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=schemas.MessageResponse, status_code=201)
def send_recommendation(
    payload: schemas.RecommendationSend,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Send a show recommendation to a friend.
    Both users must be confirmed friends before sending.
    """
    target = db.query(models.User).filter(
        models.User.username  == payload.to_username,
        models.User.is_active == True,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't recommend to yourself")

    if not _are_friends(db, current_user.id, target.id):
        raise HTTPException(
            status_code=403,
            detail="You can only send recommendations to friends"
        )

    # Prevent duplicate unseen recommendations for the same title
    duplicate = db.query(models.Recommendation).filter(
        models.Recommendation.sender_id   == current_user.id,
        models.Recommendation.receiver_id == target.id,
        models.Recommendation.title       == payload.title,
        models.Recommendation.seen        == False,
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=400,
            detail=f"You already have a pending recommendation for '{payload.title}' to {target.username}"
        )

    rec = models.Recommendation(
        sender_id   = current_user.id,
        receiver_id = target.id,
        title       = payload.title,
        is_anime    = payload.is_anime,
        message     = payload.message,
    )
    db.add(rec)
    db.commit()
    return {"message": f"Recommended '{payload.title}' to {target.username}"}


# ── Inbox (received) ──────────────────────────────────────────────────────────

@router.get("/inbox", response_model=List[schemas.RecommendationResponse])
def get_inbox(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    All recommendations received by the current user.
    Great Sage polls this to surface new recommendations in the app.
    Unread ones come first.
    """
    recs = (
        db.query(models.Recommendation)
        .filter(models.Recommendation.receiver_id == current_user.id)
        .order_by(
            models.Recommendation.seen.asc(),        # unseen first
            models.Recommendation.created_at.desc(),
        )
        .all()
    )
    return [
        schemas.RecommendationResponse(
            id         = r.id,
            title      = r.title,
            is_anime   = r.is_anime,
            message    = r.message,
            seen       = r.seen,
            added      = r.added,
            created_at = r.created_at,
            sender     = schemas.UserSearch.model_validate(r.sender),
        )
        for r in recs
    ]


@router.get("/inbox/unseen-count")
def unseen_count(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Quick count for notification badge in Great Sage / website."""
    count = db.query(models.Recommendation).filter(
        models.Recommendation.receiver_id == current_user.id,
        models.Recommendation.seen        == False,
    ).count()
    return {"unseen": count}


# ── Sent ──────────────────────────────────────────────────────────────────────

@router.get("/sent", response_model=List[schemas.RecommendationSentResponse])
def get_sent(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """All recommendations the current user has sent."""
    recs = (
        db.query(models.Recommendation)
        .filter(models.Recommendation.sender_id == current_user.id)
        .order_by(models.Recommendation.created_at.desc())
        .all()
    )
    return [
        schemas.RecommendationSentResponse(
            id         = r.id,
            title      = r.title,
            is_anime   = r.is_anime,
            message    = r.message,
            seen       = r.seen,
            added      = r.added,
            created_at = r.created_at,
            receiver   = schemas.UserSearch.model_validate(r.receiver),
        )
        for r in recs
    ]


# ── Mark seen ─────────────────────────────────────────────────────────────────

@router.patch("/{rec_id}/seen", response_model=schemas.MessageResponse)
def mark_seen(
    rec_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a recommendation as seen. Called when user opens it."""
    rec = db.query(models.Recommendation).filter(
        models.Recommendation.id          == rec_id,
        models.Recommendation.receiver_id == current_user.id,
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    rec.seen = True
    db.commit()
    return {"message": "Marked as seen"}


# ── Add to watchlist ──────────────────────────────────────────────────────────

@router.post("/{rec_id}/add-to-watchlist", response_model=schemas.WatchlistItemResponse)
def add_rec_to_watchlist(
    rec_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    One-tap: move a recommendation into the user's watchlist (planning status).

    This is the core social action — friend recommends a show, you tap
    'Add to Watchlist', it appears in your planning list and syncs down
    to Great Sage on the next pull.
    """
    rec = db.query(models.Recommendation).filter(
        models.Recommendation.id          == rec_id,
        models.Recommendation.receiver_id == current_user.id,
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    if rec.added:
        # Already added — just return the existing watchlist item
        existing = db.query(models.WatchlistItem).filter(
            models.WatchlistItem.user_id == current_user.id,
            models.WatchlistItem.title   == rec.title,
        ).first()
        if existing:
            return existing

    # Check if already in watchlist under a different status
    existing = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.user_id == current_user.id,
        models.WatchlistItem.title   == rec.title,
    ).first()

    if existing:
        rec.seen  = True
        rec.added = True
        db.commit()
        return existing

    # Add to watchlist as planning
    item = models.WatchlistItem(
        user_id  = current_user.id,
        title    = rec.title,
        is_anime = rec.is_anime,
        status   = models.WatchStatus.planning,
        notes    = f"Recommended by {rec.sender.username}" + (f": {rec.message}" if rec.message else ""),
    )
    db.add(item)

    rec.seen  = True
    rec.added = True

    db.commit()
    db.refresh(item)
    return item
