# Wimbee LinkedIn Automation

An intelligent multi-agent system that automates the LinkedIn content pipeline for **Wimbee**, a Tunisian consulting firm expert in Data, Digital, and AI. The system generates, reviews, schedules, and publishes professional LinkedIn posts automatically — with human approval at every key step.

---

## Overview

The system runs on a monthly cycle. On the 1st of each month, it generates a full editorial plan based on real RSS trends and the Tunisian calendar, sends it to the admin for approval, then autonomously generates, revises, and schedules each post — always waiting for explicit human sign-off before publishing anything.

```
1st of month
    ↓
Planner Agent → fetches RSS trends + Tunisian calendar → builds 12-post plan
    ↓
Admin receives plan by email → replies OUI or NON
    ↓ (OUI or auto-approved after 72h)
Content Agent → writes each post (special day posts always generated)
    ↓
Hashtags generated inline by Content Agent
    ↓
Admin receives each post by email → replies OUI or NON
    ↓ (OUI = scheduled, NON or ignored = cancelled)
Publisher → publishes at scheduled time via LinkedIn API
    ↓ (48h later)
Analytics Agent → collects metrics → feeds next month's plan
```

---

## Agents

| Agent | Role |
|---|---|
| **Planner** | Fetches RSS trends, checks Tunisian calendar, builds the monthly editorial plan with smart posting dates |
| **Content** | Writes full LinkedIn posts in French, generates hashtags, handles retries with revision feedback |
| **Revision** | Scores content on 4 criteria (clarity, engagement, conformity, tone), triggers retry if score < 7 |
| **Publisher** | Publishes approved posts via LinkedIn API at the scheduled time |
| **Analytics** | Collects post metrics 48h after publication, generates monthly report, feeds next cycle |

---

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.13 |
| Agent orchestration | LangGraph (LangChain) |
| LLM | OpenRouter API (llama-4-scout, deepseek, mistral — free tier with auto-fallback) |
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
│   ├── content_agent.py       # Post writing + hashtag generation
│   ├── revision_agent.py      # Content scoring + retry logic
│   ├── analytique_agent.py    # Metrics collection + monthly report
│   └── __init__.py
│
├── orchestrator/
│   ├── graph.py               # LangGraph pipeline — parallel post generation
│   ├── run_monthly_plan.py    # Monthly plan generation + DB save + email
│   └── __init__.py
│
├── api/
│   ├── email_service.py       # Gmail SMTP — plan + post approval emails
│   ├── inbox_checker.py       # Gmail IMAP — reads OUI/NON replies
│   └── templates/
│       ├── plan_email.html    # Monthly plan email template
│       └── post_email.html    # Post approval email template
│
├── config/
│   ├── LLM.py                 # OpenRouter client + model fallback logic
│   ├── tunisian_calendar.py   # National days + business events + Islamic holidays
│   ├── rss_sources.py         # RSS feed URLs + posting schedule constants
│   └── __init__.py
│
├── db/
│   ├── models.py              # SQLAlchemy models: Post, MonthlyPlan, Analytics...
│   └── __init__.py
│
├── scheduler/
│   └── jobs.py                # APScheduler jobs — monthly plan, inbox check, publish
│
├── tests/
│   ├── test_planner.py        # Planner agent tests
│   ├── test_content.py        # Content agent tests
│   └── test_e2e.py            # End-to-end pipeline tests
│
├── main.py                    # Entry point — initializes DB and starts scheduler
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
# OpenRouter API (free tier)
OPENROUTER_API_KEY=sk-or-v1-xxxxxx

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

**How to get an OpenRouter API key :**
1. Go to openrouter.ai and sign up
2. Go to API Keys → Create key
3. Copy the key into `.env`

### 4. Initialize the database

```bash
python -c "from db.models import init_db; init_db(); print('DB ready')"
```

This creates `wimbee.db` in the project root with all required tables.

### 5. Run the system

```bash
python main.py
```

The scheduler starts and runs continuously. It will :
- Generate and send the monthly plan on the 1st of each month at 08:00
- Check your inbox every hour for OUI/NON replies
- Publish approved posts at their scheduled times
- Collect analytics 48h after each publication

Press `Ctrl+C` to stop.

---

## How the approval flow works

### Monthly plan approval

1. On the 1st of the month, you receive an email with the full editorial plan
2. **Reply OUI** → system starts generating all posts
3. **Reply NON** → trend posts cancelled, special day posts still generated
4. **No reply within 72h** → plan auto-approved, all posts generated

### Post-level approval

For each generated post you receive an email with the full content and hashtags :

1. **Reply OUI** → post queued for publication at its scheduled time
2. **Reply NON** → post cancelled
3. **No reply before 72h deadline** → post automatically cancelled

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

## LLM fallback chain

The system automatically retries with the next available model if one is rate-limited :

```
1. meta-llama/llama-4-scout:free
2. meta-llama/llama-4-maverick:free
3. deepseek/deepseek-chat-v3-0324:free
4. mistralai/mistral-small-3.1-24b-instruct:free
```

All models are free tier via OpenRouter. No credits required to get started.

---

## Running tests

```bash
# Fast test — no LLM calls, no emails sent (tests pipeline logic only)
python tests\test_e2e.py

# Planner agent only
python tests\test_planner.py

# Content agent only
python tests\test_content.py
```

---

## Database schema

| Table | Purpose |
|---|---|
| `monthly_plans` | Stores each generated plan with approval status and deadline |
| `posts` | Stores each generated post with content, hashtags, score, status |
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

```
langgraph
langchain
langchain-community
langchain-ollama
openai
feedparser
apscheduler
sqlalchemy
alembic
flask
python-dotenv
loguru
hijri-converter
```

---

## License

MIT License — see LICENSE file for details.

---

## About Wimbee

Wimbee is a Tunisian consulting firm expert in Data, Digital, and Artificial Intelligence.
*"Une équipe au cœur de votre stratégie Data & Digital."*

Expertises : Stratégie Data · Transformation Data Driven · Transformation Digitale · Customer Intelligence · Risk & Conformité · Big Data · GDPR · Intelligence Artificielle · IA Générative
