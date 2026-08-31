import os
import sys
 
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
from sqlalchemy import inspect, text
 
from database.models import engine, init_db, SessionLocal, Post
 
REQUIRED_POST_COLUMNS = {
    # column_name: SQL type used if it needs to be added
    "reminder_sent": "BOOLEAN DEFAULT 0",   # tracked by send_pending_reminders()
    "published_at": "DATETIME",             # set by the posting agent on success
    "publish_error": "TEXT",                # set by the posting agent on failure
    "image_path": "VARCHAR",                # optional image attached to a post
}
 
 
def ensure_posts_table_exists():
    inspector = inspect(engine)
    if "posts" not in inspector.get_table_names():
        print(" No 'posts' table found at all — creating all tables from models.py...")
        init_db()
        return True
    return False
 
 
def ensure_required_columns():
    just_created = ensure_posts_table_exists()
    if just_created:
        print(" Tables created fresh — all required columns already present.")
        return
 
    inspector = inspect(engine)
    existing_columns = {col["name"] for col in inspector.get_columns("posts")}
 
    with engine.connect() as conn:
        for col_name, col_type in REQUIRED_POST_COLUMNS.items():
            if col_name in existing_columns:
                print(f" '{col_name}' already exists — skipping.")
                continue
            print(f" Adding missing column 'posts.{col_name}'...")
            conn.execute(text(f"ALTER TABLE posts ADD COLUMN {col_name} {col_type}"))
        conn.commit()
 
 
def fix_bad_special_day_values():
    """
    Known bug: the plan-generation LLM sometimes writes the literal string
    "null" (or "None"/"") into special_day instead of leaving it empty,
    which breaks the special-day auto-approve logic in cancel_expired_posts.
    """
    db = SessionLocal()
    bad_posts = db.query(Post).filter(Post.special_day.in_(["null", "None", ""])).all()
 
    if not bad_posts:
        print(" No bad special_day string values found.")
        db.close()
        return
 
    for post in bad_posts:
        print(f" Post #{post.id}: special_day was {post.special_day!r} → setting to real NULL")
        post.special_day = None
 
    db.commit()
    db.close()
    print(f" Fixed {len(bad_posts)} post(s).")
 
 
def main():
    print("== Wimbee database fix ==\n")
    ensure_required_columns()
    print()
    fix_bad_special_day_values()
    print("\nDone. Safe to run again anytime.")
 
 
if __name__ == "__main__":
    main()