import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

# Read fresh on each call rather than frozen at import — a module-level
# constant sourced from os.getenv() would silently ignore monkeypatch.setenv()
# in tests (same trap noted throughout this codebase, e.g.
# publishing/browser_publisher.py).
def _gmail_user() -> str:
    return os.getenv("GMAIL_USER", "").strip()


def _gmail_password() -> str:
    return os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()


def _default_admin_email() -> str:
    return os.getenv("ADMIN_EMAIL", _gmail_user()).strip()


# Path to templates folder
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def load_template(filename: str) -> str:
    
    path = os.path.join(TEMPLATES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def render_template(template: str, variables: dict) -> str:
    
    for key, value in variables.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def send_email(subject: str, html_body: str, to_addr: str | None = None) -> bool:
    """Base function — sends any HTML email via Gmail SMTP.

    to_addr defaults to ADMIN_EMAIL — orchestrator/nodes_plan.py's
    notify_plan_approval relies on that default; nodes_post.py's
    notify_post_approval and orchestrator/inbox.py pass it explicitly so the
    recipient isn't implicitly tied to this module's env var there.
    """
    gmail_user = _gmail_user()
    gmail_password = _gmail_password()
    if not gmail_user or not gmail_password:
        print(" Email error: missing Gmail credentials.")
        return False

    recipient = to_addr or _default_admin_email()

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = gmail_user
        msg["To"]      = recipient

        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient, msg.as_string())

        print(f" Email sent — {subject}")
        return True

    except Exception as e:
        print(f" Email error: {e}")
        return False