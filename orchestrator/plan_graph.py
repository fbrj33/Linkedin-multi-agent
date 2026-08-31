from __future__ import annotations

"""
Builds the plan graph:

    collect_trends -> planning_agent <- performance_brief
        -> notify_plan_approval -> plan_approval [interrupt]
             approved -> expand_plan (spawns post threads)
             rejected/expired -> END

Interrupt-replay note: notify_plan_approval (a normal, once-only node) is
deliberately separate from plan_approval (whose only job is to call
interrupt() and route on the resumed decision) — LangGraph re-executes a
node's logic from the top on every resume (verified against the installed
interrupt() docstring: "resumes from the start of the node, re-executing
all logic"), so a node that both sends an email AND calls interrupt() would
re-send that email on every resume.
"""

from langgraph.graph import END, START, StateGraph

from orchestrator import nodes_plan as nodes
from orchestrator.checkpointer import get_checkpointer
from orchestrator.state import PlanState


def _route_after_approval(state: PlanState) -> str:
    return "expand_plan" if state.get("decision") == "approved" else END


def build_plan_graph():
    builder = StateGraph(PlanState)
    builder.add_node("collect_trends", nodes.collect_trends)
    builder.add_node("performance_brief", nodes.performance_brief)
    builder.add_node("planning_agent", nodes.planning_agent)
    builder.add_node("notify_plan_approval", nodes.notify_plan_approval)
    builder.add_node("plan_approval", nodes.plan_approval)
    builder.add_node("expand_plan", nodes.expand_plan)

    builder.add_edge(START, "collect_trends")
    builder.add_edge(START, "performance_brief")
    builder.add_edge("collect_trends", "planning_agent")
    builder.add_edge("performance_brief", "planning_agent")
    builder.add_edge("planning_agent", "notify_plan_approval")
    builder.add_edge("notify_plan_approval", "plan_approval")
    builder.add_conditional_edges("plan_approval", _route_after_approval, ["expand_plan", END])
    builder.add_edge("expand_plan", END)

    return builder.compile(checkpointer=get_checkpointer())


_graph = None


def get_plan_graph():
    global _graph
    if _graph is None:
        _graph = build_plan_graph()
    return _graph


def reset_plan_graph() -> None:
    """Test-only: drop the cached compiled graph so a changed checkpointer takes effect."""
    global _graph
    _graph = None
