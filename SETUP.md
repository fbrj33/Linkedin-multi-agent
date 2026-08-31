# Setup

Ordered from zero to a running scheduler. Each step assumes the previous one worked.

## 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\playwright.exe install chromium
```

## Publishing policy and recommended mode

Use `WIMBEE_PUBLISHER=manual` in production unless LinkedIn has explicitly
approved an API/partner integration for your account. LinkedIn prohibits
unauthorized third-party software that automates creating or sharing posts.
In manual mode this project still drafts, scores, routes approvals, and
reminds you at the scheduled time; it finishes in `manual_publish_required`
instead of pretending that a post was published. Copy the approved draft into
LinkedIn yourself.

`browser` remains available only for local selector testing and a controlled
dry-run on a page you administer. Do not use it as an unattended production
publisher.

## 2. Configure `.env`

Copy `.env.example` to `.env` and fill in:

- `GMAIL_USER` / `GMAIL_APP_PASSWORD` / `ADMIN_EMAIL` — already in use for plan/post approval emails.
- `WIMBEE_LLM_PROVIDER` (`openrouter` or `gemini`) and the matching API key. Both providers work out of the box; switching is an env var, not a code change.
- `WIMBEE_ORG_NAME` — the exact text (or a substring of it) that appears in your LinkedIn company page's composer author chip. This is the identity guard's whole job — get it right before doing anything else with the publisher.
- `WIMBEE_ORG_SLUG` — the actual LinkedIn company page URL slug. It is almost never the same as `WIMBEE_ORG_NAME` — check your page's admin URL to find it.

Everything else in `.env.example` has a working default. Don't touch `WIMBEE_ALLOW_BROWSER_PUBLISHER` or `WIMBEE_DRY_RUN` yet — leave them at `no` / `yes`.

## 3. Migrate the database

```
python migrations/001_posting.py
python migrations/002_graph_thread.py
```

Both are safe to run any number of times — they only add columns/tables/indexes that don't already exist. If you're starting from a fresh (non-existent) `wimbee.db`, run any command that calls `database.models.init_db()` first (e.g. `python main.py plan 2026-08`), then run both migrations.

`002_graph_thread.py` adds `graph_thread` — the operator-facing projection of LangGraph checkpointed thread state (see "How approval actually works now" below). It also enables WAL mode on `wimbee.db`.

## 4. Save a LinkedIn browser session

```
python -m publishing.browser_publisher login
```

Opens a real, headed Chromium window. Log in **by hand** — this tool will never do it for you; a scripted login is the single strongest signal LinkedIn's anti-abuse systems look for. Once logged in, it navigates to your `WIMBEE_ORG_SLUG` admin page so you can confirm you actually have admin access to it, then saves the session into `.li_profile/` (gitignored — this directory is equivalent to a saved password, never commit it).

## 5. Start a plan and run the scheduler

```
python main.py plan 2026-08
python -m scheduling.scheduler
```

`main.py plan <month>` starts a checkpointed plan thread (`plan-2026-08`) — it collects trends, runs the planner, and emails you for approval, then the thread sits interrupted waiting for your reply. This is a manual/CLI action, not something the scheduler does on its own (see below).

`scheduling/scheduler.py` is a **pure resumption driver** — it owns no business logic. Its jobs only do one of two things: resume a graph thread, or send an email (reminders). It never imports an agent or a `Publisher`, and never touches a `Post` row directly (`tests/test_scheduler_boundaries.py` enforces this as a real, running check). Concretely, its three jobs are:

- **inbox poll** — reads your replies, parses `APPROVE <token>` / `REJECT <token> <reason>`, resumes whichever thread (plan or post) owns that token.
- **slot sweep** — wakes post threads parked at `wait_for_slot` once their scheduled time arrives.
- **reminders + expiry** — emails a reminder for threads nearing their deadline, and resumes with `decision="expired"` any that blew past it with no reply.

It also logs a publisher healthcheck at startup (a documented exception to "no business logic" — it's a read-only diagnostic, not a scheduled job) and reconciles `graph_thread` against checkpoint truth. Read both on startup; if the healthcheck says the session expired, re-run step 4 before anything else.

## How approval actually works now

Posting lives *inside* the LangGraph orchestrator (`orchestrator/`), not in the scheduler or a standalone approval module. Each plan and each post is an independent, checkpointed LangGraph thread (`plan-{month}`, `post-{post_id}`) persisted to `WIMBEE_CHECKPOINT_DB` (default `wimbee_checkpoints.db` — deliberately a separate file from `wimbee.db`, since SQLite allows one writer per file and this pipeline writes to the checkpoint far more often than to a `Post` row).

- Approval waits are `interrupt()` calls, not database polling. A thread sitting at `plan_approval` or `post_approval` is genuinely suspended — no worker, no timer, nothing running — until something calls `resume_thread(thread_id, {"decision": ..., "reason": ...})`.
- `Post.status` is still written on every transition, but it's a **projection**, not the truth — the checkpoint is authoritative. If they ever disagree, the checkpoint wins; `graph_thread` and `Post.status` get repaired to match it at scheduler startup (`orchestrator/runner.py::reconcile_on_startup`). The column exists so you can answer "what's stuck" with one SQL query at 2am, not so you can trust it as the source of truth.
- One post's stalled approval never blocks another's — they're separate threads, resumed independently.
- To manually approve/reject something without waiting for email round-trips (e.g. while testing), call `orchestrator.runner.resume_thread("post-42", {"decision": "approved", "reason": None})` directly from a Python shell.

---

## Testing ladder

Work through these in order. Do not skip ahead — each rung only proves what the previous one already covered, plus one more thing.

1. **Mock page** — `python tests/smoke_test.py` (no config, no credentials, ~7s) and `pytest tests/test_publisher.py -v` (real Chromium against the local mock, all failure modes). Proves the automation logic itself works.
2. **Your own LinkedIn page, dry run** — set `WIMBEE_ORG_NAME`/`WIMBEE_ORG_SLUG` to a page you personally control, `WIMBEE_ALLOW_BROWSER_PUBLISHER=yes`, leave `WIMBEE_DRY_RUN=yes`. Run a single publish and check the screenshot in `debug_screenshots/` — confirm the identity chip, the content, and the formatting all look right.
3. **Your own page, live** — flip `WIMBEE_DRY_RUN=no` on the same page. Confirm a real post appears, confirm the returned `PublishResult.ok` matches reality (open LinkedIn yourself and check — don't just trust the return value the first time).
4. **Client's page, dry run** — switch `WIMBEE_ORG_NAME`/`WIMBEE_ORG_SLUG` to the real target, keep `WIMBEE_DRY_RUN=yes`. Check the screenshot extremely carefully — this is the step that catches "the composer defaulted to a different page" before it matters.
5. **Client's page, live** — only after step 4's screenshots look right, every time, for a few runs.

Never jump from step 1 straight to step 5. The dry-run screenshot is the only thing standing between "the composer silently picked the wrong page" and a client's feed.

---

## Things that will bite you

**Session death.** LinkedIn invalidates sessions more often than you'd expect, especially cookie-only ones — which is why this tool uses a persistent profile (`launch_persistent_context`) instead. When it dies anyway, `healthcheck()` and `publish()` both return `needs_human=True` with a message pointing at step 4 above. The scheduler runs this healthcheck loudly at startup specifically so you find out at 9am, not when a post is due at 2pm. Never try to script around this by automating login — that's the one thing this codebase deliberately refuses to do.

**Selector drift.** LinkedIn reships its composer's DOM periodically — class names, structure, all of it. `browser_publisher.py` tries several selectors per element before giving up (see `COMPOSER_TRIGGER_SELECTORS` etc. in that file), but the day all of them stop matching, every publish will start failing with "could not find X." When that happens: open the real composer in a normal browser, inspect the current markup, and add a new selector to the front of the relevant list — don't replace the old ones, since LinkedIn sometimes reverts changes too.

**False success.** `ok=True` is only ever returned once something external actually confirms it: the composer visibly closing after a click, or a 2xx HTTP response from LinkedIn's own API. Nothing in this codebase reports success from "no exception was raised." If you're extending this and find yourself tempted to `return PublishResult(ok=True)` right after an action with no verification step in between — don't. That gap is exactly how a post silently never gets published while everything downstream thinks it did.

**Timezone mixing.** `Post.scheduled_date`/`scheduled_time` are `WIMBEE_TIMEZONE` (default `Africa/Tunis`) local wall-clock strings; every other timestamp in the schema (`approval_deadline`, `published_at`, ...) is naive-but-UTC. Two places convert between them — `scheduling/plan_expander.py` (local → UTC, once, when `approval_deadline` is first computed) and `orchestrator/nodes_post.py::wait_for_slot` (local → UTC, computing the `wake_at` the scheduler's slot-sweep compares against). If you add a new comparison against a post's schedule, it needs the same conversion — compare `datetime.utcnow()` against a naive local string directly and you'll be off by whatever `WIMBEE_TIMEZONE`'s UTC offset is.

**The interrupt-replay hazard.** LangGraph re-executes a node's logic *from the top* every time that node resumes (verified against the installed `interrupt()` docstring: "resumes from the start of the node, re-executing all logic"). Any code placed before an `interrupt()` call in the same node runs again on every resume — including code with side effects. This is why `notify_plan_approval`/`notify_post_approval` (send the email) are always separate nodes from `plan_approval`/`post_approval` (only ever call `interrupt()` and route on the result) — a combined node would re-send the approval email every time the thread got resumed. If you add a new interrupt anywhere in these graphs, keep any side-effecting step in a node of its own, upstream of the interrupt, never in the same node. `tests/test_post_graph.py::test_notify_email_sent_exactly_once_across_resumes` and the plan-graph equivalent exist specifically to catch a regression here.

**`Command(resume=...)` quirks.** Two behaviors of the installed LangGraph (1.2.6) that aren't obvious from the docs and will waste your time if you hit them cold: `Command(resume=None)` raises `UnboundLocalError` from inside LangGraph's own resume-handling internals (it branches on `resume is not None` but then unconditionally reads a variable only set inside that branch), and `Command(resume={})` is silently treated as an empty "resume map" (interrupt-id → value) rather than a plain resume value — `all(...)` over an empty dict's keys is vacuously true — so it resumes nothing and raises nothing either. `wait_for_slot`'s wake-up resume uses the literal string `"woken"` for exactly this reason. Never pass `None` or `{}` as a resume value; use any non-empty placeholder if the node doesn't actually read it.

---

## Definition of done

- [x] `python tests/smoke_test.py` passes (~7s, verified with zero env credentials set)
- [x] `pytest tests/ -v` passes with no network and no API keys — **with one exception**:
      `tests/test_planner_schedule.py::test_planner_includes_six_regular_posts_and_special_days`
      fails on a clean checkout too (confirmed via `git stash` before any of this work
      started) — it's a pre-existing issue in the posting-date scheduler logic, unrelated
      to the LLM/DB/approval/publishing pipeline built here. Not fixed, since it wasn't
      part of the brief and touching it risked masking whether it's a real bug or a
      stale fixture.
- [x] `WIMBEE_LLM_PROVIDER` switches between OpenRouter and Gemini with no code change
- [x] `WIMBEE_PUBLISHER` switches backends with no code change
- [x] An approved plan expands to Post rows and spawns independently-resumable post threads; running `expand_plan` twice adds nothing (`tests/test_plan_expander.py`), and a stalled approval on one post thread never blocks another (`tests/test_plan_graph.py::test_plan_approval_spawns_independently_resumable_post_threads`)
- [x] A rejected post regenerates using the rejection reason — now via `generate_content`'s `retry_feedback`, sourced from `post_approval`'s resume payload (`tests/test_post_graph.py::test_rejection_feeds_reason_into_retry_feedback`)
- [x] No `"null"` string can reach a database column
- [x] `WIMBEE_DRY_RUN=yes` is the default everywhere
- [x] `scheduling/scheduler.py` owns no business logic — no job imports an agent or a `Publisher`, and `Post` is never imported into the module at all, enforced by `tests/test_scheduler_boundaries.py`'s static AST checks, not just a docstring promise
- [x] `Post.status` is a projection, not truth — `orchestrator/runner.py::reconcile_on_startup` repairs it from checkpoint state (the checkpoint is authoritative) every time the scheduler starts
