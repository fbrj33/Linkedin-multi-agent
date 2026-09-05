# Wimbee LinkedIn Automation

An intelligent multi-agent system that automates the LinkedIn content pipeline for **Wimbee**, a Tunisian consulting firm expert in Data, Digital, and AI. The system generates, reviews, schedules, and publishes professional LinkedIn posts automatically — with human approval at every key step.

---

## Overview

The system runs on a monthly cycle. On the 1st of each month, it generates a full editorial plan based on real RSS trends and the Tunisian calendar, sends it to the admin for approval, then autonomously generates, revises, and schedules each post — always waiting for explicit human sign-off before publishing anything.

```
When started for a month
    ↓
Planner Agent → fetches RSS trends + Tunisian calendar → builds 6 regular posts plus special-day posts
    ↓
Admin receives plan by email → replies with an approval decision
    ↓ (approved or auto-approved after the deadline)
Content Agent → writes each post (special day posts always generated)
    ↓
Hashtags generated inline by Content Agent
    ↓
Optional carousel generation → creates 3 slide images by default through Hugging Face FLUX
    ↓
Admin receives each post by email → approves or rejects
    ↓ (approved = scheduled, rejected = regenerated within the retry limit)
Publisher → publishes at scheduled time through the configured publisher backend
    ↓ (48h later)
Analytics Agent → collects metrics → feeds next month's plan
```

---

## Agents

| Agent | Role |
|---|---|
| **Planner** | Fetches RSS trends, checks Tunisian calendar, builds the monthly editorial plan with smart posting dates |
| **Content** | Writes full LinkedIn posts in French, generates hashtags, handles retries with revision feedback |
| **Revision** | Scores content and triggers refinement when the score is below the configured threshold |
| **Publisher** | Publishes approved posts through the configured manual, relay, official API, or browser backend |
| **Analytics** | Collects post metrics 48h after publication, generates monthly report, feeds next cycle |

---

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.13 |
| Agent orchestration | LangGraph (LangChain) |
| LLM | Groq-compatible API through the centralized provider factory; current model is `qwen/qwen3.8-27b` |
| Image generation | `huggingface_hub.InferenceClient` with `black-forest-labs/FLUX.1-schnell` through the `fal-ai` provider |
| RSS trends | `feedparser` — Google News RSS + HBR + Les Echos + JDN + L'Usine Digitale |
| Tunisian calendar | Static config + `hijri-converter` for Islamic holidays |
| Email sending | Gmail SMTP via `smtplib` |
| Email reading | Gmail IMAP via `imaplib` |
| Database | SQLite (dev) → PostgreSQL (prod) via SQLAlchemy |
| Scheduler | APScheduler |
| IDE | VS Code on Windows |

---

## Project Structure

```
wimbee-linkedin-agent/
├── agents/
│   ├── planner_agent.py       # Trend fetching + editorial plan generation
│   ├── content_agent.py       # Post writing, image prompts, and carousel images
│   ├── analytique_agent.py    # Metrics collection + monthly report
│   └── linkedin_poster.py     # Legacy browser posting helper
│
├── orchestrator/
│   ├── plan_graph.py          # LangGraph monthly planning workflow
│   ├── post_graph.py          # LangGraph per-post workflow
│   ├── nodes_plan.py          # Plan graph node implementations
│   ├── nodes_post.py          # Post graph node implementations
│   ├── runner.py              # Starts and resumes graph threads
│   ├── checkpointer.py        # SQLite LangGraph checkpoint persistence
│   ├── inbox.py               # Reads approval replies and resumes threads
│   └── state.py               # Typed plan/post graph state
│
├── api/
│   ├── email_service.py       # Gmail SMTP — plan + post approval emails
│   ├── email_service.py       # Gmail SMTP — approval emails and attachments
│   └── templates/
│       ├── plan_email.html    # Monthly plan email template
│       └── post_email.html    # Post approval email template
│
├── config/
│   ├── content_config.py      # Posting configuration
│   ├── tunisian_calendar.py   # National days + business events + Islamic holidays
│   ├── rss_sources.py         # RSS feed URLs + posting schedule constants
│   └── __init__.py
│
├── database/
│   ├── models.py              # SQLAlchemy models and database initialization
│   └── __init__.py
│
├── scheduling/
│   ├── scheduler.py           # Scheduler and graph-resumption jobs
│   └── plan_expander.py       # Converts approved plans into Post rows
│
├── tests/
│   ├── test_plan_graph.py     # Plan graph tests
│   ├── test_post_graph.py     # Post graph tests
│   ├── test_carousel_generation.py # No-publish carousel generation test
│   └── test_end_to_end.py     # End-to-end pipeline tests
│
├── main.py                    # CLI entry point for plan, reply, and run commands
├── .env                       # Environment variables (not committed)
├── .env.example               # Template for environment variables
├── requirements.txt           # Python dependencies
└── README.md
```

---

## Setup

### 1. Prerequisites

- Python 3.10+
- Git

### 2. Clone and install

```bash
git clone https://github.com/your-username/wimbee-linkedin-agent.git
cd wimbee-linkedin-agent

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your credentials :

```env
# Groq LLM backend
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_key
LLM_MODEL=qwen/qwen3.8-27b

# Hugging Face carousel image generation
HF_TOKEN=your_huggingface_token
HF_IMAGE_MODEL=black-forest-labs/FLUX.1-schnell
HF_IMAGE_PROVIDER=fal-ai
WIMBEE_CAROUSEL_SLIDE_COUNT=3

# Gmail — system account that sends emails
GMAIL_USER=wimbee.automation@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

# Your real email — admin who receives and approves
ADMIN_EMAIL=your-personal-email@gmail.com
```

**How to get a Gmail App Password :**
1. Go to myaccount.google.com
2. Search "App passwords"
3. Enable 2-Step Verification if not already active
4. Create a new app password named "Wimbee"
5. Copy the 16-character password into `.env`

**How to configure the LLM and image providers :**
1. Create a Groq API key and put it in `GROQ_API_KEY`.
2. Create a Hugging Face token with inference access and put it in `HF_TOKEN`.
3. Keep `.env` local; it is excluded by `.gitignore`.

### 4. Initialize the database

```bash
python -c "from database.models import init_db; init_db(); print('DB ready')"
```

This creates `wimbee.db` in the project root with all required tables.

### 5. Run the system

```bash
python main.py plan 2026-10
```

The CLI starts a monthly plan graph. Use `python main.py run YYYY-MM` to start a plan and then keep the scheduler running in the same process. Use `python main.py check-replies` for a one-off inbox check. The scheduler resumes interrupted LangGraph threads, handles approval decisions and scheduled slots, and runs publishing/analytics jobs.

Press `Ctrl+C` to stop.

---

## How the approval flow works

### Monthly plan approval

1. On the 1st of the month, you receive an email with the full editorial plan
2. **Approve** → system starts generating the posts
3. **Reject** → the plan follows the rejection path defined by the plan graph
4. **No reply before the deadline** → the configured expiry/auto-approval behavior is applied

### Post-level approval

For each generated post you receive an email with the full content and hashtags :

1. **Approve** → post waits for its scheduled publication slot
2. **Reject** → the post is regenerated with the rejection feedback, up to the retry limit
3. **No reply before the deadline** → the post expires

> All approval emails have `[WIMBEE]` in the subject line. The inbox checker filters strictly on this tag — your personal emails are never read or processed.

---

## Posting strategy

Posts are scheduled automatically on the best days and times for LinkedIn B2B engagement :

| Setting | Value |
|---|---|
| Best days | Tuesday, Wednesday, Thursday |
| Acceptable fallback | Monday |
| Avoided days | Friday, Saturday, Sunday |
| Time slots | 08:30 / 12:00 / 17:30 (rotated) |
| Min gap between posts | 2 days |
| Special day posts | Always at 09:00 on the exact day |

---

## Tunisian calendar coverage

The system automatically creates dedicated posts for :

**National days** — Fête de la Révolution, Fête de l'Indépendance, Journée des Martyrs, Fête du Travail, Fête de la Victoire, Fête de la Jeunesse, Fête de la République, Journée de la Femme, Fête de l'Évacuation, Anniversaire du Changement, Journée de l'Arbre

**Business events** — Tunisia Digital Summit, Forum Africain de l'Investissement, Tunisia StartUp Week, Smart Tunisia Forum, Forum de la Data en Tunisie

**Islamic holidays** — Aïd el-Fitr, Aïd el-Adha, Mouled Ennabawi, Ras el-Am el-Hijri (dates confirmed for 2025, 2026, 2027)

---

## Content philosophy

Posts are written following a strict editorial philosophy :

- The post talks about the **trend or subject** — never about Wimbee directly
- Wimbee appears **only at the very end**, in one subtle sentence
- Every post must bring **concrete value** to the reader : stat, example, insight
- Structure : Accroche → Développement → Insight expert → CTA → Question finale → Référence Wimbee
- Language : French only
- Max length : 2 000 characters
- Max emojis : 3

---

## LLM provider

The agents use the centralized `llm.factory` provider selection. The current
configuration is Groq with `qwen/qwen3.8-27b`; agents request an LLM by role
and do not call provider-specific code directly. Provider credentials are read
from `.env` and are never stored in source code.

## Carousel images

Posts whose format is `carousel` or `carrousel` generate three slides by
default. Each slide has slide content, an image prompt, and a saved PNG path.
Images are generated with `huggingface_hub.InferenceClient` using
`black-forest-labs/FLUX.1-schnell` through the configured `fal-ai` provider.
Set `WIMBEE_CAROUSEL_SLIDE_COUNT=1` when a single image is sufficient.

All slides are stored as JSON metadata on the post, while `image_path` retains
the first image for compatibility with existing publishing and email paths.
Carousel generation uses Hugging Face inference credits; a depleted account
will prevent the complete carousel from being marked ready.

---

## Running tests

```bash
# Run the focused carousel generation test without publishing to LinkedIn
python -m pytest tests/test_carousel_generation.py -q

# Run the complete test suite
python -m pytest -q
```

---

## Database schema

| Table | Purpose |
|---|---|
| `monthly_plans` | Stores each generated plan with approval status and deadline |
| `posts` | Stores each generated post with content, hashtags, score, status |
| `posts.carousel_json` | Stores carousel slide content, prompts, and image paths as JSON |
| `approval_requests` | Tracks each approval email sent and the admin's decision |
| `analytics` | Stores LinkedIn metrics collected 48h after publication |
| `monthly_reports` | Stores the end-of-month performance report |

---

## RSS sources

The planner fetches trends from these sources :

- Google News RSS — Data Strategy, IA, GDPR, Transformation Digitale, Tunisie Numérique, IA Générative, Customer Intelligence, Cybersécurité
- Harvard Business Review
- Les Echos
- Journal du Net
- Le Monde Économie
- L'Usine Digitale

---

## Requirements

Dependencies are maintained in `requirements.txt`, including LangGraph,
SQLAlchemy, the OpenAI-compatible client used by the Groq provider,
`huggingface_hub`, Pillow, `python-dotenv`, requests, Playwright, APScheduler,
feedparser, and pytest.

---

## License

MIT License — see LICENSE file for details.

---


