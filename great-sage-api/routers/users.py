from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user, hash_password, verify_password
import models
import schemas
from pydantic import BaseModel, EmailStr
from typing import Optional

router = APIRouter(prefix="/users", tags=["users"])


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class ProfileUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=schemas.UserPublic)
def get_me(current_user: models.User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


@router.patch("/me", response_model=schemas.UserPublic)
def update_profile(
    payload: ProfileUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update mutable profile fields (email for now)."""
    if payload.email and payload.email != current_user.email:
        taken = db.query(models.User).filter(
            models.User.email == payload.email,
            models.User.id    != current_user.id,
        ).first()
        if taken:
            raise HTTPException(status_code=400, detail="Email already in use")
        current_user.email = payload.email

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/change-password", response_model=schemas.MessageResponse)
def change_password(
    payload: PasswordChangeRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password updated successfully"}


@router.delete("/me", response_model=schemas.MessageResponse)
def deactivate_account(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete: marks the account inactive. Data is retained."""
    current_user.is_active = False
    db.commit()
    return {"message": "Account deactivated"}


# ── Public profiles ───────────────────────────────────────────────────────────

@router.get("/{username}", response_model=schemas.UserPublic)
def get_user(
    username: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Look up any active user by username."""
    user = db.query(models.User).filter(
        models.User.username  == username.lower(),
        models.User.is_active == True,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
