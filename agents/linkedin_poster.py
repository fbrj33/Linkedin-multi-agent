import datetime
import os
import time
 
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
 
from database.models import Post, SessionLocal
 
SESSION_FILE = os.getenv("LINKEDIN_SESSION_FILE", "linkedin_session.json")
HEADLESS = os.getenv("LINKEDIN_HEADLESS", "true").lower() == "true"
 
# LinkedIn's DOM changes over time — these are current as of testing but
# WILL need occasional updates. Keep them centralized here.
SELECTORS = {
    "start_post_button": "button[aria-label='Start a post']",
    "editor": "div.ql-editor[contenteditable='true']",
    "image_upload_button": "button[aria-label='Add a photo']",
    "file_input": "input[type='file']",
    "post_submit_button": "button.share-actions__primary-action",
}
 
 
def _publish_single_post(page, post: Post) -> None:
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
 
    page.click(SELECTORS["start_post_button"], timeout=15000)
    page.wait_for_selector(SELECTORS["editor"], timeout=15000)
    page.click(SELECTORS["editor"])
    page.keyboard.type(post.content, delay=15)  # slow-ish typing looks more human
 
    image_path = getattr(post, "image_path", None)
    if image_path and os.path.exists(image_path):
        page.click(SELECTORS["image_upload_button"])
        page.set_input_files(SELECTORS["file_input"], image_path)
        # Give the upload preview a moment to render before submitting
        page.wait_for_timeout(3000)
 
    page.click(SELECTORS["post_submit_button"], timeout=15000)
    # LinkedIn doesn't give a clean success signal — wait for the composer
    # to close as a proxy for "it went through"
    page.wait_for_selector(SELECTORS["editor"], state="detached", timeout=20000)
 
 
def run_posting_agent(dry_run: bool = False) -> dict:
    """
    dry_run=True: finds approved posts and logs what WOULD be published,
    but never opens a browser and never touches the DB. Use this from tests
    so you can exercise the pipeline without risking a real LinkedIn post.
    """
    db = SessionLocal()
    to_publish = db.query(Post).filter(Post.status == "approved").all()
 
    if not to_publish:
        print(" No approved posts waiting to be published.")
        db.close()
        return {"published": 0, "failed": 0}
 
    if dry_run:
        print(f" [DRY RUN] {len(to_publish)} approved post(s) would be published:")
        for post in to_publish:
            preview = (post.content or "")[:80].replace("\n", " ")
            print(f"   Post #{post.id} | {post.scheduled_date} | \"{preview}...\"")
        db.close()
        return {"published": 0, "failed": 0, "dry_run": len(to_publish)}
 
    if not os.path.exists(SESSION_FILE):
        raise RuntimeError(
            f"No saved LinkedIn session at {SESSION_FILE}. "
            f"Run agents/linkedin_login_setup.py once first."
        )
 
    published, failed = 0, 0
 
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
 
        for post in to_publish:
            print(f" Publishing Post #{post.id} ({post.scheduled_date})...")
            try:
                _publish_single_post(page, post)
                post.status = "published"
                post.published_at = datetime.datetime.utcnow()
                db.commit()
                published += 1
                print(f"   Post #{post.id} published.")
            except PWTimeout as e:
                post.status = "publish_failed"
                if hasattr(post, "publish_error"):
                    post.publish_error = f"timeout: {e}"
                db.commit()
                failed += 1
                print(f"   Post #{post.id} failed (timeout) — will need manual review.")
            except Exception as e:
                post.status = "publish_failed"
                if hasattr(post, "publish_error"):
                    post.publish_error = str(e)
                db.commit()
                failed += 1
                print(f"   Post #{post.id} failed: {e}")
 
            # Space out multiple posts so behavior looks human, not scripted
            time.sleep(5)
 
        browser.close()
 
    db.close()
    print(f" Posting run done — {published} published, {failed} failed.")
    return {"published": published, "failed": failed}
 
 
if __name__ == "__main__":
    import sys
    run_posting_agent(dry_run="--dry-run" in sys.argv)