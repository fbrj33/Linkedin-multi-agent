import imaplib
import email
import os
import datetime
from dotenv import load_dotenv
from database.models import SessionLocal, MonthlyPlan
# Replace the imports at the bottom of process_plan_replies
from orchestrator.graph import run_post_generation




load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")        # system account — checks its own inbox
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def connect_inbox():
    """Connecte à Gmail via IMAP et retourne la connexion."""
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("📭 Email reply check skipped: Gmail credentials are missing.")
        return None

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)
    mail.select("inbox")
    return mail


def get_unread_replies() -> list:
    """
    Cherche tous les emails non lus dans la boîte du SYSTÈME
    qui ressemblent à une réponse d'approbation/rejet concernant un plan Wimbee.
    """
    mail = connect_inbox()
    if not mail:
        return []

    status, messages = mail.search(None, "UNSEEN")

    replies = []

    for num in messages[0].split():
        status, data = mail.fetch(num, "(RFC822)")
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = msg.get("Subject", "") or ""
        in_reply_to = msg.get("In-Reply-To", "") or ""
        references = msg.get("References", "") or ""
        subject_upper = subject.upper()
        headers_text = f"{subject_upper} {in_reply_to.upper()} {references.upper()}"

        # Extraire le corps du message
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
            replies.append({
                "subject":  subject,
                "decision": decision,
            })

    mail.logout()
    return replies



def process_plan_replies(generate_posts: bool = True):
    replies = get_unread_replies()

    if not replies:
        print("📭 Aucune nouvelle réponse Wimbee trouvée.")
        return

    db = SessionLocal()
    pending_plan = db.query(MonthlyPlan).filter(MonthlyPlan.status == "pending").first()

    if not pending_plan:
        print("ℹ️ Aucun plan en attente d'approbation.")
        db.close()
        return

    processed = 0
    for reply in replies:
        print(f"📨 Réponse trouvée : {reply['subject']} → {reply['decision']}")

        pending_plan.status     = reply["decision"]
        pending_plan.decided_at = datetime.datetime.utcnow()
        db.commit()

        print(f"✅ Plan {pending_plan.month} mis à jour : {reply['decision']}")
        processed += 1

        # Run or skip post generation depending on flag
        if generate_posts:
            from orchestrator.graph import run_post_generation
            run_post_generation(pending_plan.id)
        else:
            print("Post generation is disabled for now.")

        break

    db.close()
    return {
        "processed": processed,
        "plan_month": getattr(pending_plan, "month", None),
        "new_status": getattr(pending_plan, "status", None),
        "generate_posts": generate_posts,
    }
