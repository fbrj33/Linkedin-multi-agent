import datetime
import email
import imaplib
import os
import time

from dotenv import load_dotenv

from database.models import MonthlyPlan, SessionLocal
from orchestrator import workflow

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def connect_inbox():
    """Connect to Gmail via IMAP and return the connection."""
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("📭 Email reply check skipped: Gmail credentials are missing.")
        return None

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)
    mail.select("inbox")
    return mail


def get_unread_replies() -> list:
    """Read unread email replies that look like plan approvals or rejections."""
    mail = connect_inbox()
    if not mail:
        return []

    _, messages = mail.search(None, "UNSEEN")
    replies = []

    for num in messages[0].split():
        _, data = mail.fetch(num, "(RFC822)")
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = msg.get("Subject", "") or ""
        in_reply_to = msg.get("In-Reply-To", "") or ""
        references = msg.get("References", "") or ""
        subject_upper = subject.upper()
        headers_text = f"{subject_upper} {in_reply_to.upper()} {references.upper()}"

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        full_text = (subject + " " + body).upper()

        decision = None
        if "OUI" in full_text:
            decision = "approved"
        elif "NON" in full_text:
            decision = "rejected"

        is_thread_reply = bool(in_reply_to or references or subject_upper.startswith("RE:"))
        is_wimbee_related = "[WIMBEE]" in subject_upper or "WIMBEE" in headers_text

        if decision and (is_wimbee_related or is_thread_reply):
            replies.append({"subject": subject, "decision": decision})

    mail.logout()
    return replies


def process_plan_replies(generate_posts: bool = True, poll_interval_minutes: int = 15, max_polls: int | None = None):
    """Keep checking the inbox every 15 minutes until a reply is found, then process it."""
    poll_count = 0
    while True:
        replies = get_unread_replies()
        if replies:
            break

        poll_count += 1
        if max_polls is not None and poll_count >= max_polls:
            print(f"⏳ No reply found after {poll_count} check(s).")
            return None

        print(f"⏳ No reply yet. Checking again in {poll_interval_minutes} minute(s)...")
        time.sleep(poll_interval_minutes * 60)

    db = SessionLocal()
    pending_plan = db.query(MonthlyPlan).filter(MonthlyPlan.status == "pending").first()

    if not pending_plan:
        print("ℹ️ Aucun plan en attente d'approbation.")
        db.close()
        return None

    processed = 0
    for reply in replies:
        print(f"📨 Réponse trouvée : {reply['subject']} → {reply['decision']}")
        pending_plan.status = reply["decision"]
        pending_plan.decided_at = datetime.datetime.utcnow()
        db.commit()

        print(f"✅ Plan {pending_plan.month} mis à jour : {reply['decision']}")
        processed += 1

        if reply["decision"] == "approved":
            print("Approval received. Launching post generation...")
            if generate_posts:
                workflow.run_post_generation(pending_plan.id)
            else:
                print("Post generation is disabled for now.")
        else:
            print("Plan rejected. No post generation will be launched.")
        break

    db.close()
    return {
        "processed": processed,
        "plan_month": getattr(pending_plan, "month", None),
        "new_status": getattr(pending_plan, "status", None),
        "generate_posts": generate_posts,
    }
