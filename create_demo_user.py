#!/usr/bin/env python3
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.user import User
from app.core.config import settings
import bcrypt

load_dotenv()

# Use the settings to get DB URL
db_url = settings.DB_URL
print(f"Connecting to: {db_url[:50]}...")

engine = create_engine(db_url)
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

try:
    # Check if demo user exists
    demo_user = session.query(User).filter(User.username == "demo").first()
    if demo_user:
        print(f"User 'demo' already exists: {demo_user.id}")
    else:
        # Create demo user
        hashed_password = bcrypt.hashpw(b"demo", bcrypt.gensalt()).decode()
        new_user = User(
            username="demo",
            hashed_password=hashed_password,
            role="admin",
            tenant_id="demo-company",
            is_active=True
        )
        session.add(new_user)
        session.commit()
        print(f"Created user 'demo' with ID: {new_user.id}")
finally:
    session.close()
