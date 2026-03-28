import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, String, DateTime
from app.db.base import Base


class User(Base):
    __tablename__ = "app_user"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    tenant_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
