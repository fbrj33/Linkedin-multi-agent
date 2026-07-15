# Special international dates related to IT and technology for the planner agent

from config.content_config import INTERNATIONAL_IT_DATES


def get_month_international_it_days(year: int, month: int) -> list:
    result = []
    for date_key, label in INTERNATIONAL_IT_DATES.items():
        m, d = date_key.split("-")
        if int(m) == month:
            result.append({
                "date": f"{year}-{m}-{d}",
                "label": label,
            })
    return result
