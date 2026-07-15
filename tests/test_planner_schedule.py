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
