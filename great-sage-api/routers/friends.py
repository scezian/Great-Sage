from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import List

from database import get_db
from auth import get_current_user
import models
import schemas

router = APIRouter(prefix="/friends", tags=["friends"])


def _are_friends(db: Session, user_a_id: int, user_b_id: int) -> bool:
    """Return True if an accepted friendship exists between two users."""
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


def _get_friendship(db: Session, friendship_id: int, user_id: int) -> models.Friendship:
    """Fetch a friendship row that involves the given user, or 404."""
    row = db.query(models.Friendship).filter(
        models.Friendship.id == friendship_id,
        or_(
            models.Friendship.requester_id == user_id,
            models.Friendship.receiver_id  == user_id,
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Friend request not found")
    return row


# ── User search ───────────────────────────────────────────────────────────────

@router.get("/search", response_model=List[schemas.UserSearch])
def search_users(
    q: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Search for users by username prefix.
    Used when sending a friend request or recommendation.
    Returns up to 10 results, excluding the current user.
    """
    if len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")

    results = (
        db.query(models.User)
        .filter(
            models.User.username.ilike(f"{q.strip()}%"),
            models.User.id != current_user.id,
            models.User.is_active == True,
        )
        .limit(10)
        .all()
    )
    return results


# ── Send friend request ───────────────────────────────────────────────────────

@router.post("/request", response_model=schemas.MessageResponse, status_code=201)
def send_friend_request(
    payload: schemas.FriendRequestSend,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = db.query(models.User).filter(
        models.User.username  == payload.username,
        models.User.is_active == True,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't friend yourself")

    # Check no existing relationship in either direction
    existing = db.query(models.Friendship).filter(
        or_(
            and_(
                models.Friendship.requester_id == current_user.id,
                models.Friendship.receiver_id  == target.id,
            ),
            and_(
                models.Friendship.requester_id == target.id,
                models.Friendship.receiver_id  == current_user.id,
            ),
        )
    ).first()

    if existing:
        if existing.status == models.FriendshipStatus.accepted:
            raise HTTPException(status_code=400, detail="You are already friends")
        if existing.status == models.FriendshipStatus.pending:
            raise HTTPException(status_code=400, detail="A friend request already exists")
        if existing.status == models.FriendshipStatus.rejected:
            # Allow re-sending after a rejection
            existing.status       = models.FriendshipStatus.pending
            existing.requester_id = current_user.id
            existing.receiver_id  = target.id
            db.commit()
            return {"message": f"Friend request sent to {target.username}"}

    friendship = models.Friendship(
        requester_id = current_user.id,
        receiver_id  = target.id,
    )
    db.add(friendship)
    db.commit()
    return {"message": f"Friend request sent to {target.username}"}


# ── Incoming requests ─────────────────────────────────────────────────────────

@router.get("/requests/incoming", response_model=List[schemas.FriendRequestResponse])
def get_incoming_requests(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pending requests sent to the current user."""
    rows = db.query(models.Friendship).filter(
        models.Friendship.receiver_id == current_user.id,
        models.Friendship.status      == models.FriendshipStatus.pending,
    ).order_by(models.Friendship.created_at.desc()).all()

    return [
        schemas.FriendRequestResponse(
            id         = r.id,
            status     = r.status,
            created_at = r.created_at,
            requester  = schemas.UserSearch.model_validate(r.requester),
        )
        for r in rows
    ]


# ── Accept / reject ───────────────────────────────────────────────────────────

@router.patch("/request/{friendship_id}/accept", response_model=schemas.MessageResponse)
def accept_request(
    friendship_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_friendship(db, friendship_id, current_user.id)

    if row.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only accept requests sent to you")
    if row.status != models.FriendshipStatus.pending:
        raise HTTPException(status_code=400, detail="Request is not pending")

    row.status = models.FriendshipStatus.accepted
    db.commit()
    return {"message": f"You are now friends with {row.requester.username}"}


@router.patch("/request/{friendship_id}/reject", response_model=schemas.MessageResponse)
def reject_request(
    friendship_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_friendship(db, friendship_id, current_user.id)

    if row.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only reject requests sent to you")
    if row.status != models.FriendshipStatus.pending:
        raise HTTPException(status_code=400, detail="Request is not pending")

    row.status = models.FriendshipStatus.rejected
    db.commit()
    return {"message": "Friend request rejected"}


# ── List friends ──────────────────────────────────────────────────────────────

@router.get("", response_model=List[schemas.FriendshipResponse])
def list_friends(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all accepted friendships for the current user."""
    rows = db.query(models.Friendship).filter(
        models.Friendship.status == models.FriendshipStatus.accepted,
        or_(
            models.Friendship.requester_id == current_user.id,
            models.Friendship.receiver_id  == current_user.id,
        )
    ).order_by(models.Friendship.updated_at.desc()).all()

    result = []
    for r in rows:
        # "other_user" is whoever isn't the current user
        other = r.receiver if r.requester_id == current_user.id else r.requester
        result.append(schemas.FriendshipResponse(
            id         = r.id,
            status     = r.status,
            created_at = r.created_at,
            other_user = schemas.UserSearch.model_validate(other),
        ))
    return result


# ── Remove friend ─────────────────────────────────────────────────────────────

@router.delete("/{friendship_id}", response_model=schemas.MessageResponse)
def remove_friend(
    friendship_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_friendship(db, friendship_id, current_user.id)

    if row.status != models.FriendshipStatus.accepted:
        raise HTTPException(status_code=400, detail="Not a confirmed friendship")

    db.delete(row)
    db.commit()
    return {"message": "Friend removed"}
