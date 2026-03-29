from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
from app.core.config import settings

db_url = settings.effective_db_url
is_sqlite = db_url.startswith("sqlite")
is_postgres = db_url.startswith("postgresql")

connect_args = {"check_same_thread": False} if is_sqlite else {}
engine_kwargs = {"pool_pre_ping": settings.DB_POOL_PRE_PING}

if not is_sqlite:
    engine_kwargs["pool_recycle"] = settings.DB_POOL_RECYCLE_SECONDS

if is_postgres and settings.DB_SSL_REQUIRE and "sslmode=" not in db_url:
    connect_args["sslmode"] = "require"

engine = create_engine(db_url, connect_args=connect_args, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
