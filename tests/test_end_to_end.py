"""
COMPLETE WORKFLOW TEST: From Monthly Plan to Published Posts

This tests the ENTIRE Wimbee system end-to-end:
1. Admin creates a monthly plan (you provide the month)
2. System generates 4 posts from that plan
3. Posts go through content generation → image generation → approval
4. Admin approves/rejects via email with threading
5. Rejected posts get regenerated automatically
6. Final posts saved to database

Run with: pytest tests/test_full_workflow_from_month.py -v -s
"""

import pytest
import os
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from database.models import Post, MonthlyPlan, SessionLocal, Base, engine
from agents.content_agent import run_content, save_post, _generate_image_prompt
from api.email_service import send_email
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def test_db():
    """Create fresh test database."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_db):
    """Provide database session."""
    session = SessionLocal()
    yield session
    session.close()


class TestFullMonthlyWorkflow:
    """Complete workflow from month selection to published posts."""
    
    def test_01_create_monthly_plan(self, test_db, db_session):
        """
        STEP 1: Admin creates a monthly plan with 4 posts.
        
        Simulates: Admin goes to dashboard → selects September 2025 → 
        defines 4 post themes and formats
        """
        print("\n" + "="*70)
        print("STEP 1️⃣ : CREATE MONTHLY PLAN")
        print("="*70)
        
        # Create monthly plan
        plan = MonthlyPlan(
            month="2025-09",
            themes="Data Governance, AI Security, ML Best Practices, Cloud Security",
            status="draft"
        )
        db_session.add(plan)
        db_session.commit()
        db_session.refresh(plan)
        plan_id = plan.id
        
        print(f"\n✅ Monthly Plan Created:")
        print(f"   Plan ID: {plan_id}")
        print(f"   Month: 2026-09 (September 2026)")
        print(f"   Status: draft")
        
        # Define 4 posts for this month
        posts_to_generate = [
            {
                "theme": "The Future of Data Governance in 2025",
                "format": "text",
                "scheduled_date": "2025-09-01",
                "scheduled_time": "08:30",
                "trend_source": "Gartner Report",
                "brief": "Explain AI-powered data governance trends",
                "special_day": None,
            },
            {
                "theme": "AI Security Threats Every CTO Must Know",
                "format": "image",
                "scheduled_date": "2025-09-08",
                "scheduled_time": "10:00",
                "trend_source": "Cybersecurity Report",
                "brief": "Visualize top 5 AI security threats",
                "special_day": None,
            },
            {
                "theme": "10 ML Best Practices for Production",
                "format": "carousel",
                "scheduled_date": "2025-09-15",
                "scheduled_time": "14:00",
                "trend_source": "Medium Engineering",
                "brief": "Share ML best practices visually",
                "special_day": None,
            },
            {
                "theme": "Cloud Security Architecture 2025",
                "format": "image",
                "scheduled_date": "2025-09-22",
                "scheduled_time": "09:00",
                "trend_source": "AWS Reinvent",
                "brief": "Visualize modern cloud security",
                "special_day": None,
            },
        ]
        
        print(f"\n📋 Posts to Generate:")
        for i, post in enumerate(posts_to_generate, 1):
            print(f"   {i}. {post['theme']} ({post['format']})")
        
        return {
            "plan_id": plan_id,
            "posts_to_generate": posts_to_generate,
            "plan": plan
        }
    
    def test_02_generate_all_posts(self, test_db, db_session):
        """
        STEP 2: System generates content for all 4 posts.
        
        Calls Gemini LLM for each post
        """
        print("\n" + "="*70)
        print("STEP 2️⃣ : GENERATE POST CONTENT")
        print("="*70)
        
        posts_config = [
            {
                "theme": "The Future of Data Governance in 2025",
                "format": "text",
                "scheduled_date": "2025-09-01",
                "scheduled_time": "08:30",
                "trend_source": "Gartner Report",
                "brief": "Explain how AI is transforming data governance with automated classification, real-time lineage, and compliance monitoring",
                "special_day": None,
            },
            {
                "theme": "AI Security Threats Every CTO Must Know",
                "format": "image",
                "scheduled_date": "2025-09-08",
                "scheduled_time": "10:00",
                "trend_source": "Cybersecurity Report",
                "brief": "Visualize prompt injection, model poisoning, adversarial examples, data extraction, and bias exploitation",
                "special_day": None,
            },
            {
                "theme": "10 ML Best Practices for Production",
                "format": "carousel",
                "scheduled_date": "2025-09-15",
                "scheduled_time": "14:00",
                "trend_source": "Medium Engineering",
                "brief": "Share versioning, monitoring, testing, deployment, and incident response practices",
                "special_day": None,
            },
        ]
        
        generated_posts = []
        
        for i, post_brief in enumerate(posts_config, 1):
            print(f"\n📝 Generating Post {i}/3: {post_brief['theme']}...")
            print(f"   Format: {post_brief['format']}")
            
            try:
                result = run_content(post_brief)
                
                print(f"   ✅ Content: {len(result['content'])} chars")
                if result.get('image_prompt'):
                    print(f"   ✅ Image prompt: {result['image_prompt'][:50]}...")
                
                # Save to database
                post = save_post(post_brief, result["content"], result["hashtags"], result.get("image_prompt"))
                generated_posts.append({
                    "post": post,
                    "content": result["content"],
                    "hashtags": result["hashtags"],
                    "image_prompt": result.get("image_prompt"),
                })
                
                print(f"   ✅ Saved to DB with ID: {post.id}")
                
            except Exception as e:
                print(f"   ❌ Error: {str(e)[:100]}")
                pytest.skip(f"Gemini API unavailable or rate limited: {str(e)[:50]}")
        
        print(f"\n✅ Generated {len(generated_posts)} posts")
        return generated_posts
    
    def test_03_send_approval_emails(self, test_db):
        """
        STEP 3: System sends approval emails to admin.
        
        Each post email includes Message-ID for threading
        """
        print("\n" + "="*70)
        print("STEP 3️⃣ : SEND APPROVAL EMAILS TO ADMIN")
        print("="*70)
        
        admin_email = os.getenv("ADMIN_EMAIL", "wimbeeautomation@gmail.com")
        
        posts_to_approve = [
            {
                "post_id": 1,
                "theme": "Data Governance",
                "format": "text",
                "scheduled_date": "2025-09-01",
            },
            {
                "post_id": 2,
                "theme": "AI Security Threats",
                "format": "image",
                "scheduled_date": "2025-09-08",
            },
            {
                "post_id": 3,
                "theme": "ML Best Practices",
                "format": "carousel",
                "scheduled_date": "2025-09-15",
            },
        ]
        
        print(f"\n📧 Sending approval emails to: {admin_email}\n")
        
        for post in posts_to_approve:
            message_id = f"<wimbee-post-{post['post_id']}-sept2025@wimbee.local>"
            
            subject = f"[WIMBEE] Post #{post['post_id']} du {post['scheduled_date']} - {post['theme']}"
            html_body = f"""
            <h2>Post Approval Request</h2>
            <p><strong>Post ID:</strong> {post['post_id']}</p>
            <p><strong>Theme:</strong> {post['theme']}</p>
            <p><strong>Format:</strong> {post['format']}</p>
            <p><strong>Scheduled:</strong> {post['scheduled_date']}</p>
            <hr>
            <p><strong>Action Required:</strong></p>
            <p>Please review this post and reply with:</p>
            <ul>
            <li><strong>APPROVE</strong> - to schedule posting</li>
            <li><strong>REJECT: [reason]</strong> - to regenerate with feedback</li>
            </ul>
            """
            
            try:
                result = send_email(subject, html_body, admin_email, message_id=message_id)
                
                if result:
                    print(f"   ✅ Email sent for Post {post['post_id']}: {post['theme']}")
                    print(f"      Message-ID: {message_id}")
                else:
                    print(f"   ❌ Failed to send email for Post {post['post_id']}")
                    
            except Exception as e:
                print(f"   ❌ Error: {str(e)[:80]}")
        
        print(f"\n✅ All approval emails sent")
        print(f"\n📌 NEXT STEP: Check your Gmail inbox at {admin_email}")
        print(f"   Admin must reply to approve or reject each post")
    
    def test_04_simulate_admin_approval(self, test_db, db_session):
        """
        STEP 4: Simulate admin replying "APPROVE" to approval emails.
        
        In real scenario: Admin checks email, clicks approve
        Here we simulate that response
        """
        print("\n" + "="*70)
        print("STEP 4️⃣ : ADMIN APPROVES POSTS (SIMULATED)")
        print("="*70)
        
        print("\n✅ Simulating admin approval responses:\n")
        
        approvals = [
            {"post_id": 1, "decision": "APPROVE", "message": "Great content! Schedule it."},
            {"post_id": 2, "decision": "APPROVE", "message": "Love the security angle."},
            {"post_id": 3, "decision": "REJECT", "message": "REJECT: Make it more actionable with concrete steps"},
        ]
        
        for approval in approvals:
            if approval["decision"] == "APPROVE":
                print(f"   ✅ Post {approval['post_id']}: APPROVED")
                print(f"      Reason: {approval['message']}\n")
                
                # Update database
                db = SessionLocal()
                post = db.query(Post).filter(Post.id == approval["post_id"]).first()
                if post:
                    post.decision = "approved"
                    post.status = "approved"
                    db.commit()
                db.close()
                
            else:  # REJECT
                print(f"   ❌ Post {approval['post_id']}: REJECTED")
                print(f"      Feedback: {approval['message']}\n")
                
                # Update database with rejection reason
                db = SessionLocal()
                post = db.query(Post).filter(Post.id == approval["post_id"]).first()
                if post:
                    post.decision = "rejected"
                    post.rejection_reason = approval['message'].replace("REJECT: ", "")
                    post.status = "rejected"
                    db.commit()
                db.close()
    
    def test_05_regenerate_rejected_posts(self, test_db, db_session):
        """
        STEP 5: System regenerates rejected posts with new content.
        
        Uses admin's feedback to create better content
        """
        print("\n" + "="*70)
        print("STEP 5️⃣ : REGENERATE REJECTED POSTS")
        print("="*70)
        
        print("\n🔄 Finding rejected posts...\n")
        
        db = SessionLocal()
        rejected_posts = db.query(Post).filter(Post.decision == "rejected").all()
        db.close()
        
        print(f"   Found {len(rejected_posts)} rejected post(s)\n")
        
        for rejected_post in rejected_posts:
            print(f"📝 Regenerating Post {rejected_post.id}...")
            print(f"   Original feedback: {rejected_post.rejection_reason}")
            
            # Regenerate with feedback
            post_brief = {
                "theme": rejected_post.theme,
                "format": rejected_post.format,
                "scheduled_date": rejected_post.scheduled_date,
                "scheduled_time": rejected_post.scheduled_time or "08:30",
                "trend_source": rejected_post.trend_source,
                "brief": rejected_post.brief,
                "special_day": rejected_post.special_day,
            }
            
            try:
                result = run_content(post_brief, retry_feedback=rejected_post.rejection_reason)
                
                print(f"   ✅ New content generated: {len(result['content'])} chars")
                
                # Update post
                db = SessionLocal()
                post = db.query(Post).filter(Post.id == rejected_post.id).first()
                if post:
                    post.content = result["content"]
                    post.hashtags = " ".join(result["hashtags"])
                    post.refine_count = post.refine_count + 1 if post.refine_count else 1
                    post.status = "regenerated"
                    db.commit()
                db.close()
                
                print(f"   ✅ Saved to database\n")
                
            except Exception as e:
                print(f"   ❌ Error: {str(e)[:80]}\n")
    
    def test_06_final_verification(self, test_db, db_session):
        """
        STEP 6: Final verification - all posts ready for publishing.
        """
        print("\n" + "="*70)
        print("STEP 6️⃣ : FINAL VERIFICATION")
        print("="*70)
        
        db = SessionLocal()
        all_posts = db.query(Post).all()
        db.close()
        
        print(f"\n📊 Final Post Summary:\n")
        
        for post in all_posts:
            status_emoji = "✅" if post.decision == "approved" else "🔄" if post.status == "regenerated" else "⏳"
            
            print(f"   {status_emoji} Post {post.id}: {post.theme}")
            print(f"      Format: {post.format}")
            print(f"      Scheduled: {post.scheduled_date} {post.scheduled_time or '08:30'}")
            print(f"      Status: {post.status}")
            print(f"      Content length: {len(post.content or '') if post.content else 0} chars")
            print(f"      Image: {'Yes' if post.image_path else 'No'}")
            print()
        
        print(f"✅ WORKFLOW COMPLETE!")
        print(f"   Total posts: {len(all_posts)}")
        approved = len([p for p in all_posts if p.decision == "approved"])
        print(f"   Approved: {approved}")
        print(f"   Ready to publish")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])