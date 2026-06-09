from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, field_validator, model_validator
import re

from models import WatchStatus, FriendshipStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _username_valid(v: str) -> str:
    if not re.match(r"^[a-zA-Z0-9_]{3,50}$", v):
        raise ValueError("Username must be 3–50 characters: letters, numbers, underscores only")
    return v.lower()


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        return _username_valid(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserPublic"


# ── Users ─────────────────────────────────────────────────────────────────────

class UserPublic(BaseModel):
    id: int
    username: str
    created_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class UserSearch(BaseModel):
    id: int
    username: str

    model_config = {"from_attributes": True}


# ── Watchlist ─────────────────────────────────────────────────────────────────

class WatchlistItemCreate(BaseModel):
    title: str
    is_anime: bool = False
    status: WatchStatus = WatchStatus.planning
    notes: str = ""

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty")
        return v


class WatchlistItemUpdate(BaseModel):
    status: Optional[WatchStatus] = None
    notes: Optional[str] = None
    is_anime: Optional[bool] = None


class WatchlistItemResponse(BaseModel):
    id: int
    user_id: int
    title: str
    is_anime: bool
    status: WatchStatus
    notes: str
    added_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WatchlistResponse(BaseModel):
    """Full 4-list watchlist — mirrors Great Sage's local structure."""
    planning:  List[WatchlistItemResponse]
    watching:  List[WatchlistItemResponse]
    dropped:   List[WatchlistItemResponse]
    completed: List[WatchlistItemResponse]


# ── Watch Progress ─────────────────────────────────────────────────────────────

class WatchProgressUpsert(BaseModel):
    """
    Create or update progress for a show.
    Sent by Great Sage on the desktop whenever playback state changes.
    """
    title:           str
    is_anime:        bool  = False
    current_episode: int   = 0
    current_season:  int   = 1
    total_episodes:  int   = 0
    position:        float = 0.0
    duration:        float = 0.0
    file_path:       Optional[str] = None
    last_watched:    Optional[datetime] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty")
        return v


class WatchProgressResponse(BaseModel):
    id:              int
    user_id:         int
    title:           str
    is_anime:        bool
    current_episode: int
    current_season:  int
    total_episodes:  int
    position:        float
    duration:        float
    file_path:       Optional[str]
    last_watched:    Optional[datetime]
    updated_at:      datetime

    model_config = {"from_attributes": True}


# ── Full Sync ─────────────────────────────────────────────────────────────────

class SyncPushRequest(BaseModel):
    """
    Sent by Great Sage on launch (or periodic sync).
    Pushes the full local state up to the server.
    """
    watchlist: Optional[dict] = None          # { planning: [...], watching: [...], ... }
    watch_progress: Optional[List[WatchProgressUpsert]] = None


class SyncPullResponse(BaseModel):
    """
    Returned on login or explicit pull.
    Contains everything needed to restore a fresh Great Sage install.
    """
    watchlist:      WatchlistResponse
    watch_progress: List[WatchProgressResponse]


# ── Friends ───────────────────────────────────────────────────────────────────

class FriendRequestSend(BaseModel):
    username: str   # the person you want to friend

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        return _username_valid(v)


class FriendshipResponse(BaseModel):
    id:           int
    status:       FriendshipStatus
    created_at:   datetime
    # The other person in the relationship (not the caller)
    other_user:   UserSearch

    model_config = {"from_attributes": True}


class FriendRequestResponse(BaseModel):
    """Incoming friend request — shows who sent it."""
    id:           int
    status:       FriendshipStatus
    created_at:   datetime
    requester:    UserSearch

    model_config = {"from_attributes": True}


# ── Recommendations ───────────────────────────────────────────────────────────

class RecommendationSend(BaseModel):
    to_username: str
    title:       str
    is_anime:    bool = False
    message:     str  = ""

    @field_validator("to_username")
    @classmethod
    def validate_username(cls, v):
        return _username_valid(v)

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty")
        return v


class RecommendationResponse(BaseModel):
    id:          int
    title:       str
    is_anime:    bool
    message:     str
    seen:        bool
    added:       bool
    created_at:  datetime
    sender:      UserSearch

    model_config = {"from_attributes": True}


class RecommendationSentResponse(BaseModel):
    id:          int
    title:       str
    is_anime:    bool
    message:     str
    seen:        bool
    added:       bool
    created_at:  datetime
    receiver:    UserSearch

    model_config = {"from_attributes": True}


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str


# Rebuild for forward refs
TokenResponse.model_rebuild()
