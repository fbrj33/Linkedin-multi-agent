import feedparser
import json
import re
import time
import datetime
from config import LLM
from config.content_config import (
    RSS_FEEDS,
    BEST_DAYS,
    AVOID_DAYS,
    TIME_SLOTS,
    MIN_DAYS_BETWEEN_POSTS,
    SPECIAL_DAY_TIME,
)
from config.tunisian_calendar import get_month_special_days
from config.international_it_dates import get_month_international_it_days
import uuid
from api.email_service import send_plan_approval_email
from database.models import MonthlyPlan, SessionLocal


def chat(prompt: str, temperature: float = 0.3):
    return LLM.chat(prompt, temperature=temperature)


def build_fallback_plan(month: str, special_days: list | None = None, trends: list | None = None, count: int = 6) -> dict:
    return build_monthly_plan(month, special_days or [], trends or [])


def build_monthly_plan(month: str, special_days: list, trends: list) -> dict:
    year, month_num = map(int, month.split("-"))
    special_dates = [day["date"] for day in special_days if day.get("date")]
    regular_slots = get_posting_dates(year, month_num, special_dates, count=6)

    posts = []
    post_id = 1

    for scheduled_date, scheduled_time in regular_slots:
        posts.append(
            {
                "id": post_id,
                "scheduled_date": scheduled_date,
                "scheduled_time": scheduled_time,
                "theme": f"Tendance #{post_id} du mois",
                "format": "texte",
                "special_day": None,
                "trend_source": trends[0].get("source") if trends else None,
                "brief": f"Créer un post régulier autour d'une tendance du mois sur le thème data, digital et IA.",
            }
        )
        post_id += 1

    for day in special_days:
        day_date = day.get("date")
        if not day_date:
            continue
        posts.append(
            {
                "id": post_id,
                "scheduled_date": day_date,
                "scheduled_time": SPECIAL_DAY_TIME,
                "theme": f"Post dédié : {day.get('label', 'Événement spécial')}",
                "format": "carrousel",
                "special_day": day.get("label"),
                "trend_source": None,
                "brief": f"Créer un post dédié à {day.get('label', 'cet événement spécial')} avec un angle data, digital ou IA.",
            }
        )
        post_id += 1

    return {"month": month, "posts": posts}


def fetch_rss_trends() -> list:
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:
                articles.append(
                    {
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", "")[:200],
                        "source": feed.feed.get("title", "Source"),
                    }
                )
        except Exception as e:
            print(f"RSS error: {e}")

    return articles[:20]


def get_posting_dates(year: int, month: int, special_dates: list, count: int) -> list:
    if month == 12:
        all_days = [
            datetime.date(year, month, d)
            for d in range(1, 32)
            if datetime.date(year, month, d).month == month
        ]
    else:
        last_day = (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)).day
        all_days = [datetime.date(year, month, d) for d in range(1, last_day + 1)]

    taken_dates = set(special_dates)

    selected = []
    last_selected = None
    time_index = 0

    for day in all_days:
        if len(selected) >= count:
            break
        if day.isoformat() in taken_dates:
            continue
        if day.weekday() in [4, 5, 6]:
            continue
        if last_selected and (day - last_selected).days < MIN_DAYS_BETWEEN_POSTS:
            continue
        if day.weekday() in BEST_DAYS:
            time_slot = TIME_SLOTS[time_index % len(TIME_SLOTS)]
            selected.append((day.isoformat(), time_slot))
            last_selected = day
            time_index += 1
    return selected


def run_planner(month: str, analytics_report: dict = None) -> dict:
    year, m = int(month.split("-")[0]), int(month.split("-")[1])
    trends = fetch_rss_trends()
    tunisian_special_days = get_month_special_days(year, m)
    international_it_days = get_month_international_it_days(year, m)
    spec_days = tunisian_special_days + international_it_days

    trends_text = "\n".join([f"- [{a['source']}] {a['title']}" for a in trends[:15]])
    spec_days_text = (
        "\n".join([f"- {d['date']} : {d['label']}" for d in spec_days])
        or "Aucun jour spécial ce mois-ci."
    )

    special_dates = [d["date"] for d in spec_days]
    plan_template = build_monthly_plan(month, spec_days, trends)

    perf_context = ""
    if analytics_report:
        perf_context = (
            f"""
PERFORMANCE MOIS PRÉCÉDENT :
- Top thèmes    : {analytics_report.get('top_themes', [])}
- Meilleur format : {analytics_report.get('best_format', 'texte')}
- Engagement moyen : {analytics_report.get('avg_engagement', 0)} interactions
Utilise ces données pour orienter le plan.
"""
        )

    regular_posts = [post for post in plan_template["posts"] if not post.get("special_day")]
    regular_slots_text = "\n".join(
        [
            f"- Post régulier {i+1} : date {post.get('scheduled_date')} à {post.get('scheduled_time')}"
            for i, post in enumerate(regular_posts[:6])
        ]
    )
    special_days_text = "\n".join(
        [f"- Jour spécial : {day.get('date')} — {day.get('label')}" for day in spec_days if day.get('date')]
    )

    prompt = f"""
Tu es planificateur éditorial LinkedIn senior spécialisé en Data, Digital et Intelligence Artificielle B2B.

CONTEXTE :
Tu planifies le contenu LinkedIn de Wimbee, cabinet conseil tunisien expert en Data, Digital et Intelligence Artificielle.
Wimbee accompagne ses clients sur : Stratégie Data, Transformation Data Driven, Transformation Digitale,
Customer Intelligence, Risk & Conformité, Big Data, GDPR, IA & Machine Learning, IA Générative.

PHILOSOPHIE ÉDITORIALE :
- Les posts ne parlent PAS de Wimbee directement
- Ils parlent des TENDANCES et SUJETS du secteur Data, Digital et IA
- Wimbee n'apparaît qu'en fin de post, en une seule phrase discrète
- L'objectif est de positionner Wimbee comme référence experte, pas de faire de la publicité
- Chaque post doit apporter de la VALEUR au lecteur : insight, chiffre, cas concret, tendance

Mois : {month}
{perf_context}

TENDANCES ACTUELLES (sources fiables — Data, Digital, IA) :
{trends_text}

JOURS SPÉCIAUX TUNISIENS CE MOIS (créer un post dédié pour CHACUN) :
{spec_days_text}

CRÉNEAUX DISPONIBLES POUR LES 6 POSTS RÉGULIERS (dates et heures optimisées Tue/Wed/Thu) :
{regular_slots_text}

JOURS SPÉCIAUX À TRAITER (un post dédié pour chacun) :
{special_days_text}

INSTRUCTIONS :
1. Crée un plan complet pour ce mois avec EXACTEMENT 6 posts réguliers plus 1 post dédié pour chaque jour spécial listé.
2. Utilise les dates et heures fournies pour les 6 posts réguliers.
3. Pour chaque jour spécial : crée un post dédié à la date du jour spécial, à {SPECIAL_DAY_TIME}, avec "special_day" égal au nom du jour.
4. Chaque post doit s'inspirer d'une tendance RSS différente et ne pas répéter les sources.
5. Les briefs doivent être précis : angle éditorial, point clé à développer, ton attendu.
6. Formats variés : texte, carrousel, vidéo.
7. Ton : expert, pédagogique, jamais publicitaire.

EXEMPLES DE BONS BRIEFS :
- "Expliquer pourquoi 80% des projets data échouent à cause du manque de gouvernance, avec des pistes concrètes pour l'éviter"
- "Décrypter l'impact de l'IA générative sur les métiers du conseil en 2026, avec exemples réels"
- "Analyser les nouvelles obligations GDPR pour les entreprises tunisiennes qui traitent des données européennes"

Réponds UNIQUEMENT en JSON valide sans texte avant ou après :
{{
  "month": "{month}",
  "posts": [
    {{
      "id": 1,
      "scheduled_date": "YYYY-MM-DD",
      "scheduled_time": "HH:MM",
      "theme": "...",
      "format": "texte|carrousel",
      "special_day": "nom du jour ou null",
      "trend_source": "source RSS ou null",
      "brief": "description précise du post : angle éditorial, point clé à développer, ton attendu"
    }}
  ]
}}
"""

    try:
        raw = chat(prompt, temperature=0.3)
    except Exception as exc:
        print("LLM error: failed to generate the plan.")
        print("This usually means the API model could not be reached or the request failed.")
        print("Error details:", exc)
        return plan_template

    decoder = json.JSONDecoder()
    start = raw.find("{")
    if start == -1:
        print(" No JSON found in response")
        return build_fallback_plan(month)

    try:
        result, _ = decoder.raw_decode(raw[start:])
        if isinstance(result, dict) and isinstance(result.get("posts"), list):
            regular_posts = [post for post in result["posts"] if not post.get("special_day")]
            special_posts = [post for post in result["posts"] if post.get("special_day")]
            if len(regular_posts) < 6 or len(special_posts) < len(spec_days):
                merged_posts = []
                for template_post in plan_template["posts"]:
                    matching_post = next(
                        (
                            post
                            for post in result["posts"]
                            if post.get("scheduled_date") == template_post.get("scheduled_date")
                            and post.get("special_day") == template_post.get("special_day")
                        ),
                        None,
                    )
                    merged_posts.append(matching_post or template_post)
                result["posts"] = merged_posts
            return result
        return plan_template
    except json.JSONDecodeError as e:
        print(f" JSON parse error: {e}")
        print("RAW RESPONSE:", raw[:500])
        return plan_template


def save_plan_to_db(plan: dict, send_email: bool = False) -> MonthlyPlan:
    db = SessionLocal()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    deadline = now + datetime.timedelta(hours=72)
    deadline_label = deadline.strftime("%d/%m/%Y à %H:%M")

    db_plan = MonthlyPlan(
        month=plan.get("month"),
        plan_json=json.dumps(plan, ensure_ascii=False),
        status="pending",
        sent_at=now,
        deadline=deadline,
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    db.close()

    if send_email:
        send_plan_approval_email(
            plan=plan,
            deadline=deadline_label,
        )
        print(f"Plan saved (id={db_plan.id}) and email sent. Deadline: {deadline_label}")
    else:
        print(f"Plan saved (id={db_plan.id}) to wimbee.db. Deadline: {deadline_label}")

    return db_plan


def save_and_notify_plan(plan: dict) -> MonthlyPlan:
    return save_plan_to_db(plan, send_email=True)