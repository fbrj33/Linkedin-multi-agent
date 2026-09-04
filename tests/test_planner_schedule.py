import datetime
import importlib

from agents import planner_agent as pa


def test_planner_includes_six_regular_posts_and_special_days(monkeypatch):
    month = "2026-08"
    year, month_num = map(int, month.split("-"))

    special_days = [
        {"date": "2026-08-13", "label": "Journée de la Femme"},
        {"date": "2026-08-15", "label": "Autre jour spécial"},
    ]

    monkeypatch.setattr(pa, "fetch_rss_trends", lambda: [{"title": "x", "summary": "y", "source": "src"}])
    monkeypatch.setattr(pa, "get_month_special_days", lambda y, m: special_days)
    monkeypatch.setattr(pa, "get_month_international_it_days", lambda y, m: [])
    monkeypatch.setattr(pa, "chat", lambda prompt, temperature=0.3: '{"month": "2026-08", "posts": []}')

    result = pa.run_planner(month)

    assert result["month"] == month
    assert isinstance(result.get("posts"), list)
    assert len(result["posts"]) >= 8

    regular_posts = [p for p in result["posts"] if not p.get("special_day")]
    special_posts = [p for p in result["posts"] if p.get("special_day")]

    assert len(regular_posts) >= 6
    assert len(special_posts) >= len(special_days)


def test_planner_sanitizes_nullish_fields_from_llm_response(monkeypatch):
    """The bug this whole project is framed around: a model writing the
    literal string "null" for an optional field (the prompt itself asks for
    "nom du jour ou null") used to sail through as a truthy value. run_planner
    must sanitize its own parsed JSON, not rely solely on expand_plan()
    catching it later."""
    month = "2026-08"

    monkeypatch.setattr(pa, "fetch_rss_trends", lambda: [])
    monkeypatch.setattr(pa, "get_month_special_days", lambda y, m: [])
    monkeypatch.setattr(pa, "get_month_international_it_days", lambda y, m: [])
    monkeypatch.setattr(
        pa, "chat",
        lambda prompt, temperature=0.3: (
            '{"month": "2026-08", "posts": [{"id": 1, "scheduled_date": "2026-08-04", '
            '"scheduled_time": "08:30", "theme": "x", "format": "texte", '
            '"special_day": "null", "trend_source": "N/A", "brief": "y"}]}'
        ),
    )

    result = pa.run_planner(month)

    assert result["posts"][0]["special_day"] is None
    assert result["posts"][0]["trend_source"] is None


def test_planner_recovers_fenced_json_response(monkeypatch):
    month = "2026-08"

    monkeypatch.setattr(pa, "fetch_rss_trends", lambda: [])
    monkeypatch.setattr(pa, "get_month_special_days", lambda y, m: [])
    monkeypatch.setattr(pa, "get_month_international_it_days", lambda y, m: [])
    monkeypatch.setattr(
        pa, "chat",
        lambda prompt, temperature=0.3: '```json\n{"month": "2026-08", "posts": []}\n```',
    )

    result = pa.run_planner(month)

    assert result["month"] == "2026-08"


def test_planner_fallback_keeps_rss_articles_when_model_response_is_invalid(monkeypatch):
    month = "2026-08"
    article = {
        "title": "Actualite IA",
        "summary": "Un resume RSS utile.",
        "source": "Tech Source",
        "url": "https://example.com/article",
    }

    monkeypatch.setattr(pa, "fetch_rss_trends", lambda: [article])
    monkeypatch.setattr(pa, "get_month_special_days", lambda y, m: [])
    monkeypatch.setattr(pa, "get_month_international_it_days", lambda y, m: [])
    monkeypatch.setattr(pa, "chat", lambda prompt, temperature=0.3: "not valid json")

    result = pa.run_planner(month)

    regular_post = next(post for post in result["posts"] if not post.get("special_day"))
    assert regular_post["trend_article_title"] == article["title"]
    assert regular_post["trend_article_url"] == article["url"]
    assert regular_post["theme"] == article["title"]
