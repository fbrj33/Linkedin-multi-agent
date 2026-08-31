from __future__ import annotations

"""
Tests orchestrator/inbox.py's parsing logic — subject-based thread
resolution and the APPROVE/REJECT body parser — independent of any real
IMAP connection (check_and_resume itself needs a real mailbox and isn't
unit-tested here; parse_decision/thread_id_from_subject are the pure
functions that matter for correctness).
"""

from orchestrator.inbox import parse_decision, thread_id_from_subject


# ---------------------------------------------------------------------------
# thread_id_from_subject
# ---------------------------------------------------------------------------

def test_thread_id_from_post_subject():
    assert thread_id_from_subject("[WIMBEE] Post #43 du 2026-08-04 à 08:30") == "post-43"


def test_thread_id_from_post_subject_with_re_prefix():
    assert thread_id_from_subject("Re: [WIMBEE] Post #43 du 2026-08-04 à 08:30") == "post-43"


def test_thread_id_from_plan_subject():
    assert thread_id_from_subject("[WIMBEE] Plan LinkedIn 2026-10") == "plan-2026-10"


def test_thread_id_from_plan_subject_with_re_prefix():
    assert thread_id_from_subject("Re: [WIMBEE] Plan LinkedIn 2026-10") == "plan-2026-10"


def test_thread_id_from_reminder_subject():
    """Must match the exact subject shape scheduling/scheduler.py's
    job_reminders_and_expiry() constructs — see that function's comment on
    why it can't just reuse thread_id verbatim."""
    assert thread_id_from_subject("[WIMBEE] Reminder — Post #43 needs your APPROVE/REJECT") == "post-43"
    assert thread_id_from_subject("[WIMBEE] Reminder — Plan LinkedIn 2026-10 needs your APPROVE/REJECT") == "plan-2026-10"


def test_thread_id_from_unrelated_subject_is_none():
    assert thread_id_from_subject("Re: dinner tonight?") is None
    assert thread_id_from_subject("") is None
    assert thread_id_from_subject(None) is None


# ---------------------------------------------------------------------------
# parse_decision
# ---------------------------------------------------------------------------

def test_parse_decision_approve_bare():
    assert parse_decision("APPROVE") == ("approved", None)
    assert parse_decision("Approve\n\nThanks!") == ("approved", None)


def test_parse_decision_reject_with_reason():
    assert parse_decision("REJECT too promotional, rewrite the hook") == (
        "rejected", "too promotional, rewrite the hook",
    )


def test_parse_decision_returns_none_when_no_match():
    assert parse_decision("Looks fine to me, thanks!") is None
    assert parse_decision("") is None
    assert parse_decision("OUI") is None  # the old, retired format


def test_parse_decision_ignores_boilerplate_in_quoted_original_on_wrote():
    body = (
        "REJECT the hook is weak\n"
        "\n"
        "On Mon, Jan 5, 2026 at 10:23 AM Wimbee Bot <bot@wimbee.com> wrote:\n"
        "> Reply APPROVE to approve, or REJECT <reason> to reject.\n"
    )
    assert parse_decision(body) == ("rejected", "the hook is weak")


def test_parse_decision_ignores_boilerplate_in_quoted_original_message():
    body = "APPROVE\n-----Original Message-----\nReply APPROVE to approve.\n"
    assert parse_decision(body) == ("approved", None)
