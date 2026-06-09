from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from auth import hash_password, verify_password, create_access_token
import models
import schemas

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.TokenResponse, status_code=201)
def register(payload: schemas.RegisterRequest, db: Session = Depends(get_db)):
    """Create a new account. Returns a token immediately so the user is logged in."""

    # Check username taken
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    # Check email taken
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = models.User(
        username      = payload.username,
        email         = payload.email,
        password_hash = hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.username)
    return schemas.TokenResponse(
        access_token = token,
        user         = schemas.UserPublic.model_validate(user),
    )


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    """Login with username + password. Returns a JWT."""

    user = db.query(models.User).filter(
        models.User.username == payload.username.lower()
    ).first()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token(user.id, user.username)
    return schemas.TokenResponse(
        access_token = token,
        user         = schemas.UserPublic.model_validate(user),
    )
