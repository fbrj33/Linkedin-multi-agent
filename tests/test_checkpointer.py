from __future__ import annotations

"""
Tests orchestrator/checkpointer.py — the SQLite checkpointer setup itself,
independent of any of this project's actual graphs. Uses its own temp file
(not the WIMBEE_CHECKPOINT_DB conftest.py points at) so it can freely open
second/third connections to prove persistence survives a reload.
"""

import sqlite3

from typing_extensions import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from orchestrator import checkpointer as ckpt


def test_get_checkpointer_returns_working_sqlite_saver(monkeypatch, tmp_path):
    db_path = tmp_path / "ckpt.db"
    monkeypatch.setenv("WIMBEE_CHECKPOINT_DB", str(db_path))
    ckpt.reset_checkpointer()

    saver = ckpt.get_checkpointer()

    assert isinstance(saver, SqliteSaver)
    assert db_path.exists()


def test_get_checkpointer_enables_wal(monkeypatch, tmp_path):
    db_path = tmp_path / "ckpt.db"
    monkeypatch.setenv("WIMBEE_CHECKPOINT_DB", str(db_path))
    ckpt.reset_checkpointer()

    ckpt.get_checkpointer()

    # Independent connection to the same file — proves WAL was persisted
    # to the file itself, not just set on the in-process connection object.
    check_conn = sqlite3.connect(str(db_path))
    mode = check_conn.execute("PRAGMA journal_mode").fetchone()[0]
    check_conn.close()
    assert mode.lower() == "wal"


def test_get_checkpointer_caches_across_calls(monkeypatch, tmp_path):
    db_path = tmp_path / "ckpt.db"
    monkeypatch.setenv("WIMBEE_CHECKPOINT_DB", str(db_path))
    ckpt.reset_checkpointer()

    first = ckpt.get_checkpointer()
    second = ckpt.get_checkpointer()
    assert first is second


class _State(TypedDict, total=False):
    foo: str
    result: str


def _node_a(state):
    return {"foo": "hello"}


def _node_b(state):
    value = interrupt({"post_id": 42, "approval_token": "abc123"})
    return {"result": value}


def _build_trivial_graph(saver):
    builder = StateGraph(_State)
    builder.add_node("a", _node_a)
    builder.add_node("b", _node_b)
    builder.add_edge(START, "a")
    builder.add_edge("a", "b")
    builder.add_edge("b", END)
    return builder.compile(checkpointer=saver)


def test_interrupt_survives_reload_against_the_same_file(monkeypatch, tmp_path):
    """Simulates a process restart: a second, independent SqliteSaver
    pointed at the same file must see the exact same pending interrupt the
    first process left behind."""
    db_path = tmp_path / "ckpt.db"
    config = {"configurable": {"thread_id": "t1"}}

    conn1 = sqlite3.connect(str(db_path), check_same_thread=False)
    conn1.execute("PRAGMA journal_mode=WAL")
    saver1 = SqliteSaver(conn1)
    saver1.setup()
    graph1 = _build_trivial_graph(saver1)
    graph1.invoke({}, config)

    snapshot1 = graph1.get_state(config)
    assert snapshot1.next == ("b",)
    assert snapshot1.interrupts[0].value == {"post_id": 42, "approval_token": "abc123"}
    conn1.close()

    # Fresh connection + fresh SqliteSaver — nothing shared with the above
    # except the file on disk.
    conn2 = sqlite3.connect(str(db_path), check_same_thread=False)
    saver2 = SqliteSaver(conn2)
    graph2 = _build_trivial_graph(saver2)

    snapshot2 = graph2.get_state(config)
    assert snapshot2.next == ("b",)
    assert snapshot2.interrupts[0].value == {"post_id": 42, "approval_token": "abc123"}

    graph2.invoke(Command(resume="approved"), config)
    snapshot3 = graph2.get_state(config)
    assert snapshot3.next == ()
    assert snapshot3.values == {"foo": "hello", "result": "approved"}
    conn2.close()
