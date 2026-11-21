import time
from datetime import datetime

from sqlalchemy import (Column, DateTime, Float, ForeignKey, Integer, LargeBinary,
                        String, Text, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def utcnow():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    filter_name = Column(String(20), nullable=False)
    radius = Column(Integer, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    total_strips = Column(Integer, nullable=False)
    status = Column(String(20), default="active", nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, nullable=True)
    workers_used = Column(Text, nullable=True)
    mime = Column(String(30), default="image/png")
    input_image = Column(LargeBinary, nullable=False)
    result_image = Column(LargeBinary, nullable=True)


def init_db():
    last_error = None
    for _ in range(15):
        try:
            Base.metadata.create_all(engine)
            return
        except Exception as e:
            last_error = e
            print("База ще не готова, чекаємо...", e)
            time.sleep(2)
    raise RuntimeError("Не вдалося підключитися до бази: %s" % last_error)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
