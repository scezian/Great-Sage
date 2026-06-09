from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, Float,
    DateTime, ForeignKey, Text, Enum as SAEnum,
    UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
import enum

from database import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class WatchStatus(str, enum.Enum):
    planning  = "planning"
    watching  = "watching"
    dropped   = "dropped"
    completed = "completed"


class FriendshipStatus(str, enum.Enum):
    pending  = "pending"
    accepted = "accepted"
    rejected = "rejected"


# ── Models ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    is_active     = Column(Boolean, default=True)

    # Relationships
    watchlist_items  = relationship("WatchlistItem",  back_populates="user", cascade="all, delete-orphan")
    watch_progresses = relationship("WatchProgress",  back_populates="user", cascade="all, delete-orphan")

    sent_friendships     = relationship("Friendship", foreign_keys="Friendship.requester_id",
                                        back_populates="requester", cascade="all, delete-orphan")
    received_friendships = relationship("Friendship", foreign_keys="Friendship.receiver_id",
                                        back_populates="receiver", cascade="all, delete-orphan")

    sent_recommendations     = relationship("Recommendation", foreign_keys="Recommendation.sender_id",
                                            back_populates="sender", cascade="all, delete-orphan")
    received_recommendations = relationship("Recommendation", foreign_keys="Recommendation.receiver_id",
                                            back_populates="receiver", cascade="all, delete-orphan")


class WatchlistItem(Base):
    """
    Mirrors the watchlist section of ~/.config/matrix/progress.json.
    Each item belongs to one of four status buckets: planning / watching /
    dropped / completed — matching Great Sage's existing 4-list structure.
    """
    __tablename__ = "watchlist_items"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title      = Column(String(500), nullable=False)
    is_anime   = Column(Boolean, default=False)
    status     = Column(SAEnum(WatchStatus), nullable=False, default=WatchStatus.planning)
    notes      = Column(Text, default="")
    added_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="watchlist_items")

    # A user can't have the same title in their watchlist twice
    __table_args__ = (
        UniqueConstraint("user_id", "title", name="uq_user_title"),
        Index("ix_watchlist_user_status", "user_id", "status"),
    )


class WatchProgress(Base):
    """
    Mirrors the 'watching' section of progress.json — per-show playback state.
    Tracks exactly where you left off: episode, season, position in seconds.
    """
    __tablename__ = "watch_progress"

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title            = Column(String(500), nullable=False)
    is_anime         = Column(Boolean, default=False)

    # Playback position
    current_episode  = Column(Integer, default=0)
    current_season   = Column(Integer, default=1)
    total_episodes   = Column(Integer, default=0)
    position         = Column(Float, default=0.0)    # seconds into the episode
    duration         = Column(Float, default=0.0)    # total episode duration in seconds

    # File info (local — may differ per machine, kept for context)
    file_path        = Column(Text, nullable=True)

    last_watched     = Column(DateTime(timezone=True), nullable=True)
    updated_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                              onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="watch_progresses")

    __table_args__ = (
        UniqueConstraint("user_id", "title", name="uq_progress_user_title"),
        Index("ix_progress_user_last", "user_id", "last_watched"),
    )


class Friendship(Base):
    """
    Directed friendship request. Once accepted, the pair are friends.
    To check if A and B are friends: look for an accepted row where
    (requester=A, receiver=B) OR (requester=B, receiver=A).
    """
    __tablename__ = "friendships"

    id           = Column(Integer, primary_key=True, index=True)
    requester_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    receiver_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status       = Column(SAEnum(FriendshipStatus), nullable=False, default=FriendshipStatus.pending)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    requester = relationship("User", foreign_keys=[requester_id], back_populates="sent_friendships")
    receiver  = relationship("User", foreign_keys=[receiver_id],  back_populates="received_friendships")

    # Can't send duplicate requests
    __table_args__ = (
        UniqueConstraint("requester_id", "receiver_id", name="uq_friendship_pair"),
    )


class Recommendation(Base):
    """
    A show one friend sends to another.
    The receiver sees it in their recommendations inbox; they can add it to
    their watchlist directly from there, and it syncs back to Great Sage.
    """
    __tablename__ = "recommendations"

    id         = Column(Integer, primary_key=True, index=True)
    sender_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    receiver_id= Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    title      = Column(String(500), nullable=False)
    is_anime   = Column(Boolean, default=False)
    message    = Column(Text, default="")         # optional note from sender

    seen       = Column(Boolean, default=False)   # receiver has opened it
    added      = Column(Boolean, default=False)   # receiver added it to their watchlist

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    sender   = relationship("User", foreign_keys=[sender_id],   back_populates="sent_recommendations")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="received_recommendations")

    __table_args__ = (
        Index("ix_rec_receiver_seen", "receiver_id", "seen"),
    )
