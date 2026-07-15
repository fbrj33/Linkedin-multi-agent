import datetime
import json
import logging
import operator
from typing import Annotated, List, TypedDict

from langgraph.graph import START, END, StateGraph

try:
    from langgraph.types import Send
except ImportError:  # pragma: no cover - compatibility for older LangGraph
    from langgraph.constants import Send

from agents.content_agent import run_content, save_post
from agents.planner_agent import run_planner
from api.email_service import send_plan_approval_email, send_post_approval_email
from database.models import MonthlyPlan, SessionLocal

logger = logging.getLogger(__name__)


class OrchestratorState(TypedDict):
    plan_id: int
    special_posts: List[dict]
    trend_posts: List[dict]
    results: Annotated[List[dict], operator.add]


class PostState(TypedDict):
    post_brief: dict
    results: Annotated[List[dict], operator.add]


def save_plan_to_db(plan: dict, send_email: bool = True) -> MonthlyPlan:
    """Persist the monthly plan and optionally notify the admin by email."""
    logger.info("Saving plan...")
    db = SessionLocal()
    deadline = datetime.datetime.utcnow() + datetime.timedelta(hours=72)
    deadline_label = deadline.strftime("%d/%m/%Y à %H:%M")

    db_plan = MonthlyPlan(
        month=plan.get("month"),
        plan_json=json.dumps(plan, ensure_ascii=False),
        status="pending",
        sent_at=datetime.datetime.utcnow(),
        deadline=deadline,
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    db.close()

    if send_email:
        logger.info("Sending approval email...")
        send_plan_approval_email(plan=plan, deadline=deadline_label)
        print(f"Plan saved (id={db_plan.id}) and email sent. Deadline: {deadline_label}")
    else:
        print(f"Plan saved (id={db_plan.id}) — no email sent. Deadline: {deadline_label}")

    return db_plan


def generate_and_send_monthly_plan(month: str) -> MonthlyPlan | None:
    """Generate the monthly plan and persist it for approval."""
    logger.info("Generating monthly plan...")
    plan = run_planner(month)
    if not plan or not plan.get("posts"):
        logger.error("Planner returned an empty plan.")
        return None
    return save_plan_to_db(plan, send_email=True)


def init_plan(state: OrchestratorState) -> OrchestratorState:
    """Load the plan and split posts into special-day and trend posts."""
    session = SessionLocal()
    db_plan = session.query(MonthlyPlan).filter(MonthlyPlan.id == state["plan_id"]).first()
    session.close()

    if not db_plan:
        logger.error("Plan %s not found in DB.", state["plan_id"])
        return {"special_posts": [], "trend_posts": [], "results": []}

    plan = json.loads(db_plan.plan_json)
    all_posts = plan.get("posts", [])

    special_posts = [post for post in all_posts if post.get("special_day")]
    trend_posts = []
    if db_plan.status in ["approved", "auto_approved"]:
        trend_posts = [post for post in all_posts if not post.get("special_day")]
    else:
        logger.warning("Plan status is '%s' — trend posts skipped.", db_plan.status)

    logger.info("Plan loaded: %s special post(s), %s trend post(s)", len(special_posts), len(trend_posts))
    return {
        "special_posts": special_posts,
        "trend_posts": trend_posts,
        "results": [],
    }


def generate_post(state: PostState) -> dict:
    """Generate a single post, save it, and send an approval email."""
    post_brief = state["post_brief"]
    logger.info("Generating post: %s", post_brief.get("theme", "")[:60])

    result = run_content(post_brief)
    content = result["content"]
    hashtags = result["hashtags"]

    logger.info("Saving post...")
    db_post = save_post(post_brief, content, hashtags)

    try:
        scheduled_dt = datetime.datetime.strptime(
            f"{post_brief['scheduled_date']} {post_brief.get('scheduled_time', '08:30')}",
            "%Y-%m-%d %H:%M",
        )
        approval_deadline = scheduled_dt - datetime.timedelta(hours=72)
        deadline_str = approval_deadline.strftime("%d/%m/%Y à %H:%M")
    except Exception:
        deadline_str = "72h avant la publication"

    logger.info("Sending post approval email...")
    send_post_approval_email(
        post_id=db_post.id,
        content=content,
        hashtags=hashtags,
        scheduled_date=post_brief["scheduled_date"],
        scheduled_time=post_brief.get("scheduled_time", "08:30"),
        deadline=deadline_str,
    )

    logger.info("Workflow completed.")
    return {
        "results": [
            {
                "post_id": db_post.id,
                "theme": post_brief["theme"][:60],
                "status": "email_sent",
                "deadline": deadline_str,
            }
        ]
    }


def route_posts(state: OrchestratorState) -> List[Send]:
    """Fan out the post generation workflow in parallel."""
    all_posts = state["special_posts"] + state["trend_posts"]
    if not all_posts:
        logger.info("No posts to generate.")
        return [Send("done", {})]

    logger.info("Launching post generation for %s post(s)...", len(all_posts))
    return [Send("generate_post", {"post_brief": post, "results": []}) for post in all_posts]


def done(state: OrchestratorState) -> dict:
    """Print a summary of the generated posts."""
    results = state.get("results", [])
    logger.info("Completed post generation for %s post(s).", len(results))
    for result in results:
        logger.info("Post %s | %s | %s", result["post_id"], result["theme"], result["deadline"])
    return {}


builder = StateGraph(OrchestratorState)
builder.add_node("init_plan", init_plan)
builder.add_node("generate_post", generate_post)
builder.add_node("done", done)
builder.add_edge(START, "init_plan")
builder.add_conditional_edges("init_plan", route_posts, ["generate_post", "done"])
builder.add_edge("generate_post", "done")

graph = builder.compile()


def run_post_generation(plan_id: int):
    """Run the full post-generation workflow for an approved plan."""
    logger.info("Launching post generation...")
    graph.invoke({
        "plan_id": plan_id,
        "special_posts": [],
        "trend_posts": [],
        "results": [],
    })
