from config.content_config import FIXED_DAYS, BUSINESS_EVENTS

# special tunisian days for the planner agent
def get_islamic_holidays(year: int) -> dict:
    """
    Returns Islamic holidays for a given Gregorian year.
    Confirmed dates for 2025 and 2026 are hardcoded for accuracy.
    Future years use hijri_converter as an approximation.
    """

    # Confirmed dates — verified from official sources
    CONFIRMED = {
        2026: {
        "03-20": "Aïd el-Fitr ",
        "05-27": "Aïd el-Adha ",
        "06-16": "Ras el-Am el-Hijri ",
        "08-25": "Mouled Ennabawi ",
    },
    2027: {
        "03-10": "Aïd el-Fitr ",
        "05-16": "Aïd el-Adha ",
        "06-06": "Ras el-Am el-Hijri ",
        "08-15": "Mouled Ennabawi ",
    }
    }

    if year in CONFIRMED:
        return CONFIRMED[year]

    # Fallback for future years using hijri_converter
    holidays = {}
    try:
        from hijri_converter import convert
        
        hijri_year = year - 579
        dates = {
            "Aïd el-Fitr ":       (hijri_year, 10, 1),
            "Aïd el-Adha ":       (hijri_year, 12, 10),
            "Mouled Ennabawi ":    (hijri_year, 3, 12),
            "Ras el-Am el-Hijri ": (hijri_year, 1, 1),
        }
        for label, (hy, hm, hd) in dates.items():
            greg = convert.Hijri(hy, hm, hd).to_gregorian()
            holidays[greg.strftime("%m-%d")] = label
    except Exception as e:
        print(f"Islamic calendar error: {e}")
    return holidays

def get_all_special_days(year: int) -> dict:
    all_days={}
    all_days.update(FIXED_DAYS)
    all_days.update(BUSINESS_EVENTS)
    all_days.update(get_islamic_holidays(year))
    return all_days

def get_month_special_days(year:int,month:int)-> list:
    all_days=get_all_special_days(year)
    result=[]
    for date_key, label in all_days.items():
        m, d = date_key.split("-")
        if int(m) == month:
            result.append({
                "date":  f"{year}-{m}-{d}",
                "label": label
            })
    return result
