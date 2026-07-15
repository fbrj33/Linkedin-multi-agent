

RSS_FEEDS = [
    # ── Google News RSS — Data & Digital  ──
    "https://news.google.com/rss/search?q=strategie+data+transformation+digitale&hl=fr&gl=TN&ceid=TN:fr",
    "https://news.google.com/rss/search?q=intelligence+artificielle+entreprise&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=GDPR+conformite+gouvernance+data&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=business+intelligence+data+driven&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=cloud+computing+big+data+entreprise&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=transformation+digitale+tunisie&hl=fr&gl=TN&ceid=TN:fr",
    "https://news.google.com/rss/search?q=cybersecurite+risk+conformite&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=customer+intelligence+CRM+data&hl=fr&gl=FR&ceid=FR:fr",
    # ── Intelligence Artificielle ──
    "https://news.google.com/rss/search?q=intelligence+artificielle+entreprise+2026&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=IA+generative+business+cas+usage&hl=fr&gl=FR&ceid=FR:fr",
    "https://news.google.com/rss/search?q=machine+learning+data+science+tunisie&hl=fr&gl=TN&ceid=TN:fr",
    # ── Trusted Business & Tech publications ──
    "https://feeds.feedburner.com/harvardbusiness",
    "https://www.lesechos.fr/rss/rss_une.xml",
    "https://www.journaldunet.com/rss/",
    "https://www.lemonde.fr/economie/rss_full.xml",
    "https://www.usine-digitale.fr/rss/",
]

BEST_DAYS = [1, 2, 3]   # Tuesday, Wednesday, Thursday
AVOID_DAYS = [4, 5, 6]  # Friday, Saturday, Sunday
TIME_SLOTS = ["08:30", "12:00", "17:30"]
MIN_DAYS_BETWEEN_POSTS = 4
SPECIAL_DAY_TIME = "09:00"

FIXED_DAYS = {
    "01-01": "Nouvel An",
    "01-14": "Fête de la Révolution et de la Jeunesse",
    "03-20": "Fête de l'Indépendance",
    "04-09": "Journée des Martyrs",
    "05-01": "Fête du Travail",
    "06-01": "Fête de la Victoire",
    "06-02": "Fête de la Jeunesse",
    "07-25": "Fête de la République",
    "08-13": "Journée de la Femme",
    "10-15": "Fête de l'Évacuation",
    "11-07": "Anniversaire du Changement",
    "12-01": "Journée de l'Arbre",
}

BUSINESS_EVENTS = {
    "03-15": "Tunisia Digital Summit",
    "04-20": "Forum Africain de l'Investissement",
    "05-15": "Tunisia StartUp Week",
    "10-10": "Journée Mondiale de la Normalisation",
    "10-28": "Smart Tunisia Forum",
    "11-15": "Forum de la Data en Tunisie",
}

INTERNATIONAL_IT_DATES = {
    "02-14": "International Day of Women and Girls in Science",
    "04-04": "International Robotics Day",
    "09-13": "International Day of Programmers",
    "10-29": "International Internet Day",
    "11-30": "Computer Security Day",
}
