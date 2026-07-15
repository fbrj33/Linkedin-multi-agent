import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER     = os.getenv("GMAIL_USER", "").strip()
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", GMAIL_USER).strip()

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


def send_email(subject: str, html_body: str) -> bool:
    """Base function — sends any HTML email via Gmail SMTP."""
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print(" Email error: missing Gmail credentials.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = ADMIN_EMAIL

        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, ADMIN_EMAIL, msg.as_string())

        print(f" Email sent — {subject}")
        return True

    except Exception as e:
        print(f" Email error: {e}")
        return False


def send_plan_approval_email(plan: dict, deadline: str) -> bool:
    
    month = plan.get("month", "")
    posts = plan.get("posts", [])

    # Build the posts table rows
    posts_rows = ""
    for post in posts:
        special = f"⭐ {post.get('special_day')}" if post.get("special_day") else ""
        posts_rows += f"""
        <tr>
            <td style='padding:8px;border-bottom:1px solid #eee;'>{post.get('scheduled_date')} {post.get('scheduled_time', '')}</td>
            <td style='padding:8px;border-bottom:1px solid #eee;'>{post.get('theme')} {special}</td>
            <td style='padding:8px;border-bottom:1px solid #eee;'>{post.get('format')}</td>
            <td style='padding:8px;border-bottom:1px solid #eee;'>{post.get('brief', '')[:80]}...</td>
        </tr>
        """

    # Load and render template
    template = load_template("plan_email.html")
    html     = render_template(template, {
        "month":      month,
        "deadline":   deadline,
        "posts_rows": posts_rows,
    })

    return send_email(
        subject   = f" [WIMBEE] Plan LinkedIn {month} ",
        html_body = html
    )


def send_post_approval_email(post_id: int, content: str, hashtags: list,
                              scheduled_date: str, scheduled_time: str,
                              deadline: str) -> bool:
    """Sends a single post to the admin for approval."""
    hashtags_str = " ".join(hashtags) if hashtags else ""

    # Load and render template
    template = load_template("post_email.html")
    html     = render_template(template, {
        "post_id":        post_id,
        "scheduled_date": scheduled_date,
        "scheduled_time": scheduled_time,
        "deadline":       deadline,
        "content":        content,
        "hashtags":       hashtags_str,
    })

    return send_email(
        subject   = f" [WIMBEE] Post #{post_id} du {scheduled_date} à {scheduled_time} ",
        html_body = html
    )