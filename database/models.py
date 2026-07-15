from sqlalchemy import Column, Integer, String, Text, DateTime, Float, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import uuid

Base = declarative_base()

class Post(Base):
    __tablename__ = "posts"

    id                = Column(Integer(), primary_key=True)
    theme             = Column(String(200))
    format            = Column(String(50))
    scheduled_date    = Column(String(20))
    scheduled_time    = Column(String(10))
    special_day       = Column(String(100))
    trend_source      = Column(String(300))
    content           = Column(Text())
    hashtags          = Column(Text())
    score             = Column(Float())
    retry_count       = Column(Integer(), default=0)
    status            = Column(String(30))
    created_at        = Column(DateTime(), default=datetime.datetime.utcnow)
    approval_token    = Column(String(100), default=lambda: str(uuid.uuid4()))
    approval_deadline = Column(DateTime())


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id               = Column(Integer(), primary_key=True)
    post_id          = Column(Integer())
    admin_email      = Column(String(200))
    sent_at          = Column(DateTime(), default=datetime.datetime.utcnow)
    deadline         = Column(DateTime())
    decision         = Column(String(20))
    decided_at       = Column(DateTime())
    rejection_reason = Column(Text())


class Analytics(Base):
    __tablename__ = "analytics"

    id           = Column(Integer(), primary_key=True)
    post_id      = Column(Integer())
    likes        = Column(Integer())
    comments     = Column(Integer())
    views        = Column(Integer())
    shares       = Column(Integer())
    collected_at = Column(DateTime())


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"

    id          = Column(Integer(), primary_key=True)
    month       = Column(String(7))
    report_json = Column(Text())
    created_at  = Column(DateTime(), default=datetime.datetime.utcnow)

class MonthlyPlan(Base):
    __tablename__ = "monthly_plans"

    id               = Column(Integer(), primary_key=True)
    month            = Column(String(7))          # "2025-07"
    plan_json        = Column(Text())             # full plan as JSON
    admin_email      = Column(String(200))
    approval_token   = Column(String(100), default=lambda: str(uuid.uuid4()))
    status           = Column(String(30))         # pending / approved / rejected / auto_approved
    sent_at          = Column(DateTime())
    deadline         = Column(DateTime())         # sent_at + 72h
    decided_at       = Column(DateTime())
    
    created_at       = Column(DateTime(), default=datetime.datetime.utcnow)
# --- Database connection ---
engine       = create_engine("sqlite:///wimbee.db", echo=False)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
    # Ensure migrations for simple additive changes (like `scheduled_time`) are applied
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql("PRAGMA table_info(posts)")
            cols = [row[1] for row in result.fetchall()]
            if "scheduled_time" not in cols:
                try:
                    conn.exec_driver_sql('ALTER TABLE posts ADD COLUMN scheduled_time VARCHAR(10)')
                except Exception:
                    pass
    except Exception:
        pass