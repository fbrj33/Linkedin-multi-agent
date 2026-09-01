from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import os
import uuid

Base = declarative_base()

class Post(Base):
    __tablename__ = "posts"

    id                = Column(Integer(), primary_key=True)
    plan_id           = Column(Integer(), index=True)       # links this Post to its MonthlyPlan
    theme             = Column(String(200))
    format            = Column(String(50))
    scheduled_date    = Column(String(20))
    scheduled_time    = Column(String(10))
    special_day       = Column(String(100))
    trend_source      = Column(String(300))
    brief             = Column(Text())                       # editorial brief from the plan item, needed at content-gen time
    content           = Column(Text())
    hashtags          = Column(Text())
    score             = Column(Float())
    score_reason      = Column(Text())                       # scoring LLM's specific critique — fed into refine_content's next attempt
    retry_count       = Column(Integer(), default=0)
    status            = Column(String(30), index=True)
    created_at        = Column(DateTime(), default=datetime.datetime.utcnow)
    approval_token    = Column(String(100), default=lambda: str(uuid.uuid4()), index=True)
    approval_deadline = Column(DateTime())

    # --- added for scheduler / reminders / posting agent ---
    reminder_sent     = Column(Boolean(), default=False)   # tracked by send_pending_reminders()
    published_at      = Column(DateTime())                  # set by the posting agent on success
    publish_error     = Column(Text())                      # set by the posting agent on failure
    image_path        = Column(String(300))                 # optional image attached to a post
    email_message_id  = Column(String(255))                 # original approval email, for revision threading
    external_id       = Column(String(255))                 # LinkedIn URN, set after a successful publish


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
    reminder_sent_at = Column(DateTime())    # guards send_deadline_reminders() from re-nudging every sweep


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


class GraphThread(Base):
    """Operator-queryable projection of LangGraph checkpointed thread state.

    The checkpointer (see orchestrator/checkpointer.py) is the actual source
    of truth for a thread's state — this table exists so "what's stuck"
    answers with one SQL query instead of a get_state() call per thread.
    Reminders, sweeps, and dashboards read this table, never the
    checkpointer directly. On disagreement between this table and
    checkpoint state, the checkpoint wins (see runner.reconcile_on_startup).
    """
    __tablename__ = "graph_thread"

    thread_id         = Column(String(100), primary_key=True)  # "plan-2026-08" / "post-42" / "analytics"
    thread_type       = Column(String(20), index=True)         # plan | post | analytics
    status            = Column(String(20), index=True)         # running | interrupted | done
    current_node      = Column(String(100))
    post_id           = Column(Integer(), index=True)
    month             = Column(String(7))
    interrupt_payload = Column(Text())                          # JSON: post_id/approval_token/deadline/...
    reminder_sent_at  = Column(DateTime())                       # guards the reminder sweep from re-nudging every tick
    created_at        = Column(DateTime(), default=datetime.datetime.utcnow)
    updated_at        = Column(DateTime(), default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# --- Database connection ---
# WIMBEE_DATABASE_URL lets tests redirect to an isolated file instead of the
# real wimbee.db (see tests/conftest.py) without monkeypatching this module.
DATABASE_URL = os.getenv("WIMBEE_DATABASE_URL", "sqlite:///wimbee.db")
engine       = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)

    # WAL so this DB and the checkpoint DB (see orchestrator/checkpointer.py)
    # can each be read/written concurrently by the scheduler's worker
    # threads without one writer blocking another. Idempotent — setting it
    # again on an already-WAL file is a no-op.
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.commit()
    except Exception:
        pass

    # Ensure migrations for simple additive changes are applied
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql("PRAGMA table_info(posts)")
            cols = [row[1] for row in result.fetchall()]

            additive_columns = {
                "scheduled_time": "VARCHAR(10)",
                "reminder_sent":  "BOOLEAN DEFAULT 0",
                "published_at":   "DATETIME",
                "publish_error":  "TEXT",
                "image_path":     "VARCHAR(300)",
                "email_message_id": "VARCHAR(255)",
            }
            for col_name, col_type in additive_columns.items():
                if col_name not in cols:
                    try:
                        conn.exec_driver_sql(f'ALTER TABLE posts ADD COLUMN {col_name} {col_type}')
                    except Exception:
                        pass
    except Exception:
        pass
