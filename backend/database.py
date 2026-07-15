import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# On the portal, DATABASE_URL is injected and the schema comes from
# backend/migrations/ (applied by the portal — never create_all there).
# Without DATABASE_URL we're in local dev: SQLite file, schema created in
# main.py from the models.
DATABASE_URL = os.getenv("DATABASE_URL")
IS_LOCAL_DEV = not DATABASE_URL

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL)
else:
    engine = create_engine(
        "sqlite:///./data/dioxengine.db", connect_args={"check_same_thread": False}
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
