import feedparser
import json
import re
import time
import datetime
from llm import get_llm
from llm.base import extract_json, sanitize_nullish
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
from database.models import MonthlyPlan, SessionLocal


def chat(prompt: str, temperature: float = 0.3) -> str:
    response = get_llm(role="planning").complete(
        prompt,
        temperature=temperature,
        max_tokens=6000,
    )
    if not response.ok:
        raise RuntimeError(response.error)
    return response.text


def build_fallback_plan(month: str, special_days: list | None = None, trends: list | None = None, count: int = 6) -> dict:
    return build_monthly_plan(month, special_days or [], trends or [])


def build_monthly_plan(month: str, special_days: list, trends: list) -> dict:
    year, month_num = map(int, month.split("-"))
    special_dates = [day["date"] for day in special_days if day.get("date")]
    regular_slots = get_posting_dates(year, month_num, special_dates, count=6)

    posts = []
    post_id = 1

    for scheduled_date, scheduled_time in regular_slots:
        article = trends[(post_id - 1) % len(trends)] if trends else {}
        article_title = article.get("title") or "Actualité Data, Digital ou IA"
        article_summary = article.get("summary") or ""
        posts.append(
            {
                "id": post_id,
                "scheduled_date": scheduled_date,
                "scheduled_time": scheduled_time,
                "theme": article_title,
                "format": "texte",
                "special_day": None,
                "trend_source": article.get("source"),
                "trend_article_title": article_title,
                "trend_article_url": article.get("url"),
                "brief": f"S'appuyer sur l'article « {article_title} ». Résumé source : {article_summary}. Déduire un angle éditorial, un point clé concret et un exemple à développer à partir de cet article.",
            }
        )
        post_id += 1

    for day in special_days:
        day_date = day.get("date")
        if not day_date:
            continue
        article = trends[(post_id - 1) % len(trends)] if trends else {}
        posts.append(
            {
                "id": post_id,
                "scheduled_date": day_date,
                "scheduled_time": SPECIAL_DAY_TIME,
                "theme": f"Post dédié : {day.get('label', 'Événement spécial')}",
                "format": "carrousel",
                "special_day": day.get("label"),
                "trend_source": article.get("source"),
                "trend_article_title": article.get("title"),
                "trend_article_url": article.get("url"),
                "brief": f"S'appuyer sur l'article « {article.get('title', '')} » et relier son résumé ({article.get('summary', '')}) à {day.get('label', 'cet événement spécial')} avec un angle data, digital ou IA.",
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
                        "url": entry.get("link", ""),
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

    def select(allowed_weekdays: set[int]) -> list:
        selected = []
        last_selected = None
        time_index = 0
        for day in all_days:
            if len(selected) >= count:
                break
            if day.isoformat() in taken_dates or day.weekday() not in allowed_weekdays:
                continue
            if last_selected and (day - last_selected).days < MIN_DAYS_BETWEEN_POSTS:
                continue
            selected.append((day.isoformat(), TIME_SLOTS[time_index % len(TIME_SLOTS)]))
            last_selected = day
            time_index += 1
        return selected

    # Prefer the empirically best days, but a short month or special dates
    # can make six posts mathematically impossible under that restriction.
    # In that case recompute over all weekdays (never weekends) rather than
    # silently returning an incomplete monthly plan.
    preferred = select(set(BEST_DAYS))
    return preferred if len(preferred) >= count else select({0, 1, 2, 3, 4})


def run_planner(month: str, analytics_report: dict = None) -> dict:
    year, m = int(month.split("-")[0]), int(month.split("-")[1])
    trends = fetch_rss_trends()
    tunisian_special_days = get_month_special_days(year, m)
    international_it_days = get_month_international_it_days(year, m)
    spec_days = tunisian_special_days + international_it_days

    trends_text = "\n".join(
        [
            f"[{i}] [{a['source']}] {a['title']}\n    Résumé : {a.get('summary', '')}\n    URL : {a.get('url', '')}"
            for i, a in enumerate(trends[:15], 1)
        ]
    ) or "Aucun article RSS récupéré."
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
4. Chaque post régulier doit sélectionner UN article RSS précis dans la liste numérotée ci-dessus.
5. Pour chaque post régulier, recopie exactement le titre et l'URL de l'article sélectionné dans
    "trend_article_title" et "trend_article_url". Le "theme" doit être dérivé du titre de cet
    article, et le "brief" doit s'appuyer sur son résumé et expliquer l'angle à développer.
6. N'utilise jamais de libellé générique comme "Tendance #1 du mois" et n'invente pas d'article.
7. Les articles sélectionnés doivent être différents autant que possible. Pour un jour spécial,
    conserve l'angle du jour mais rattache aussi le post à l'article RSS le plus pertinent.
8. Les briefs doivent être précis : angle éditorial, point clé à développer, ton attendu.
9. Formats variés : texte, carrousel.
10. Ton : expert, pédagogique, jamais publicitaire.

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
    "trend_article_title": "titre exact de l'article RSS sélectionné",
    "trend_article_url": "URL exacte de l'article RSS sélectionné",
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
        plan_template["generation_source"] = "rss_fallback"
        return plan_template

    # extract_json + sanitize_nullish (Phase 1) instead of a bare
    # json.JSONDecoder().raw_decode() — the old version couldn't recover a
    # fenced ```json response and, more importantly, never sanitized the
    # result, so a model writing the literal string "null" for special_day
    # (see the prompt above: "nom du jour ou null") sailed straight through
    # as a truthy value. chat() stays the seam here (rather than switching to
    # get_llm().complete_json() directly) so this still shares one LLM
    # call path with the rest of the module.
    result = extract_json(raw)
    if result is None:
        print(" No JSON found in response")
        plan_template["generation_source"] = "rss_fallback"
        return plan_template

    result = sanitize_nullish(result)

    if isinstance(result, dict) and isinstance(result.get("posts"), list):
        regular_result_posts = [post for post in result["posts"] if not post.get("special_day")]
        for index, post in enumerate(regular_result_posts[:6]):
            article = trends[index % len(trends)] if trends else {}
            if article and not post.get("trend_article_title"):
                post["trend_article_title"] = article.get("title")
            if article and not post.get("trend_article_url"):
                post["trend_article_url"] = article.get("url")
            if article and (not post.get("theme") or str(post.get("theme")).lower().startswith("tendance #")):
                post["theme"] = article.get("title")
            if article and not post.get("trend_source"):
                post["trend_source"] = article.get("source")
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
        result["generation_source"] = "llm"
        return result
    plan_template["generation_source"] = "rss_fallback"
    return plan_template


def save_plan_to_db(plan: dict) -> MonthlyPlan:
    """Persists the plan only — sending the approval email is
    orchestrator/nodes_plan.py::notify_plan_approval's job, kept as its own
    graph node so a resume of plan_approval can never re-trigger it (see
    that module's interrupt-replay note). This function must never send
    email itself: doing so here would create a MonthlyPlan row with no
    corresponding graph_thread, and any reply to that email would have no
    thread to resume — permanently unresolvable.
    """
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

    print(f"Plan saved (id={db_plan.id}) to wimbee.db. Deadline: {deadline_label}")
    return db_plan
