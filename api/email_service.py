import smtplib
import os
from email.mime.base import MIMEBase
from email import encoders
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


def send_email(subject: str, html_body: str, to_addr: str | None = None, message_id: str | None = None, in_reply_to: str | None = None, references: str | None = None, attachment_path: str | None = None) -> bool:
    """Send email via Gmail SMTP with optional threading headers."""
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
        
        if message_id:
            msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        msg.attach(MIMEText(html_body, "html"))

        if attachment_path and os.path.isfile(attachment_path):
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=os.path.basename(attachment_path),
            )
            msg.attach(part)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient, msg.as_string())

        print(f" Email sent — {subject}")
        return True

    except Exception as e:
        print(f" Email error: {e}")
        return False