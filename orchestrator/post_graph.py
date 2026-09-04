from __future__ import annotations



from langgraph.graph import END, START, StateGraph

from orchestrator import nodes_post as nodes
from orchestrator.checkpointer import get_checkpointer
from orchestrator.state import PostState


def _route_after_score(state: PostState) -> str:
    score = state.get("predicted_score")
    if (
        score is not None
        and score < nodes.refine_score_threshold()
        and state.get("refine_count", 0) < nodes.refine_max_loops()
    ):
        return "refine_content"
    return "notify_post_approval"


def _route_after_approval(state: PostState) -> str:
    decision = state.get("decision")
    if decision == "approved":
        return "wait_for_slot"
    if decision == "rejected" and state.get("retry_count", 0) <= nodes.rejection_retry_limit():
        return "send_rejection_reply"  # NEW: thread the reply first
    return "finalize"  # expired, or rejected past the retry limit


def _route_after_verify(state: PostState) -> str:
    if (
        state.get("published")
        or state.get("needs_human")
        or state.get("publish_attempt", 0) >= nodes.publish_retry_limit()
    ):
        return "finalize"
    return "publish"


def build_post_graph():
    builder = StateGraph(PostState)
    builder.add_node("load", nodes.load)
    builder.add_node("generate_content", nodes.generate_content)
    builder.add_node("score_content", nodes.score_content)
    builder.add_node("refine_content", nodes.refine_content)
    builder.add_node("notify_post_approval", nodes.notify_post_approval)
    builder.add_node("post_approval", nodes.post_approval)
    builder.add_node("send_rejection_reply", nodes.send_rejection_reply)  # NEW
    builder.add_node("wait_for_slot", nodes.wait_for_slot)
    builder.add_node("publish", nodes.publish)
    builder.add_node("verify_publish", nodes.verify_publish)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "load")
    builder.add_edge("load", "generate_content")
    builder.add_edge("generate_content", "score_content")
    builder.add_conditional_edges("score_content", _route_after_score, ["refine_content", "notify_post_approval"])
    builder.add_edge("refine_content", "score_content")
    builder.add_edge("notify_post_approval", "post_approval")
    builder.add_conditional_edges("post_approval", _route_after_approval, ["wait_for_slot", "send_rejection_reply", "finalize"])  # UPDATED
    builder.add_edge("send_rejection_reply", "score_content")
    builder.add_edge("wait_for_slot", "publish")
    builder.add_edge("publish", "verify_publish")
    builder.add_conditional_edges("verify_publish", _route_after_verify, ["publish", "finalize"])
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=get_checkpointer())


_graph = None


def get_post_graph():
    global _graph
    if _graph is None:
        _graph = build_post_graph()
    return _graph


def reset_post_graph() -> None:

    global _graph
    _graph = None