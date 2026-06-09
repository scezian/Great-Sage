import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/greatsage")
# Normalise: if someone puts a bare postgresql:// in .env, upgrade it to psycopg v3
DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+psycopg://", 1) \
    if _raw_url.startswith("postgresql://") and "+psycopg" not in _raw_url else _raw_url

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # reconnect on stale connections
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency: yields a DB session and guarantees cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
