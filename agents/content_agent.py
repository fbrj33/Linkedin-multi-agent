from config import LLM
from database.models import SessionLocal, Post
import datetime
import json
import re




def chat(prompt: str, temperature: float = 0.7):
    return LLM.chat(prompt, temperature=temperature)

WIMBEE_CONTEXT = """
Tu es un expert en Data, Digital et Intelligence Artificielle qui rédige du contenu LinkedIn
professionnel, engageant et à haute valeur ajoutée.

IDENTITÉ DE WIMBEE :
Wimbee est un cabinet conseil tunisien expert en Data, Digital et Intelligence Artificielle.
"Une équipe au cœur de votre stratégie Data & Digital. Des experts métiers, des experts
fonctionnels, des consultants et développeurs techniques qui maîtrisent les innovations
et les solutions du marché."

Expertises Wimbee :
- Stratégie Data & gouvernance des données
- Transformation Data Driven
- Transformation Digitale & Web
- Intelligence Artificielle & Machine Learning
- IA Générative & automatisation intelligente
- Customer Intelligence & CRM
- Risk & Conformité (GDPR, réglementation)
- Big Data & architecture de données

PHILOSOPHIE ÉDITORIALE OBLIGATOIRE :
- Le post parle du SUJET ou de la TENDANCE, PAS de Wimbee
- Wimbee n'apparaît QU'À LA TOUTE FIN, en une seule phrase naturelle et discrète
- Le lecteur doit apprendre quelque chose de concret, pas lire une publicité
- Ton : expert qui partage son savoir, pas une entreprise qui se vend
- Le lecteur doit finir en pensant "cet expert sait de quoi il parle"
"""


def run_content(post_brief: dict, retry_feedback: str = None):
    

    feedback_block = ""
    if retry_feedback:
        feedback_block = f"""
 RÉÉCRITURE DEMANDÉE — VERSION PRÉCÉDENTE INSUFFISANTE :
Feedback de l'agent de révision :
{retry_feedback}
Tu DOIS absolument intégrer ces corrections. Ne répète pas les mêmes erreurs.
"""

    special_block = ""
    if post_brief.get("special_day"):
        special_block = f"""
📌 POST JOUR SPÉCIAL : {post_brief['special_day']}
- Commence OBLIGATOIREMENT par ce titre seul sur une ligne : 🇹🇳 {post_brief['special_day']}
- Connecte ce jour à un enjeu Data, Digital ou IA de façon subtile et intelligente
- Ne pas être générique ni banal — trouve un angle original et pertinent
- Exemples d'angles :
  * Fête du Travail → transformation des métiers par l'IA
  * Fête de l'Indépendance → souveraineté numérique et données
  * Fête de la République → gouvernance des données et démocratie numérique
  * Journée de la Femme → femmes dans la Tech et la Data en Tunisie
  * Aïd → digitalisation des services pendant les fêtes, impact sur la data
"""

    prompt = f"""
{WIMBEE_CONTEXT}

BRIEF DU POST :
- Thème         : {post_brief.get('theme', 'N/A')}
- Format        : {post_brief.get('format', 'texte')}
- Date prévue   : {post_brief.get('scheduled_date', 'N/A')} à {post_brief.get('scheduled_time', '08:30')}
- Source        : {post_brief.get('trend_source', 'N/A')}
- Brief         : {post_brief.get('brief', '')}
{special_block}
{feedback_block}

STRUCTURE OBLIGATOIRE DU POST :

1. TITRE (1 ligne) :
   - Le titre doit être écrit en **gras** (Markdown : **Titre**).
   - Il doit être court, percutant et conçu pour attirer immédiatement l'attention.
   - Si une journée spéciale est disponible, utiliser : {"**🇹🇳 " + post_brief['special_day'] + "**" if post_brief.get('special_day') else ""}
   - Sinon, générer un titre fort en gras lié au sujet.

1. ACCROCHE (1-2 lignes max) — stat choc, question provocante, ou affirmation contre-intuitive qui arrête le scroll

2. DÉVELOPPEMENT — expliquer la tendance avec un exemple concret, un chiffre réel, un cas d'usage

3. INSIGHT EXPERT — observation pointue que seul un vrai expert remarquerait

4. CTA — action concrète que le lecteur peut faire maintenant

5. QUESTION FINALE — pour générer des commentaires et de l'engagement

6. RÉFÉRENCE WIMBEE (1 phrase max, discrète) :
   Utilise l'une de ces formulations ou similaire :
   - "C'est exactement ce sur quoi nous travaillons chez Wimbee avec nos clients."
   - "Un sujet au cœur des missions de Wimbee depuis plusieurs années."
   - "Chez Wimbee, nous accompagnons nos clients sur ces enjeux au quotidien."

RÈGLES STRICTES :
- Maximum 2 000 caractères
- Français uniquement
- Ne jamais commencer par "Bonjour", "Chez Wimbee", "Nous" ou "Notre"
- Pas de jargon creux : interdit d'écrire "synergies", "solutions innovantes", "approche holistique"
- Pas de bullet points excessifs — narration fluide et naturelle
- Maximum 3 emojis dans tout le post, utilisés avec parcimonie
- Ne jamais mentionner de concurrents

Rédige le post LinkedIn complet maintenant.

Après le post, sur une nouvelle ligne, écris exactement :
---HASHTAGS---
Puis liste 5 à 7 hashtags optimaux séparés par des espaces.

Règles hashtags :
- Toujours inclure : #Wimbee #DataDigital #Tunisie
- Mix populaires (#Data #IA #Digital) + niche (#DataGovernance #GDPR #MLOps)
- Si jour spécial tunisien : ajouter un hashtag dédié (ex: #FêteDeLaRépublique)
- Pas de hashtags spam ou trop génériques seuls
"""

    raw = chat(prompt)

    # Split content and hashtags
    if "---HASHTAGS---" in raw:
        parts    = raw.split("---HASHTAGS---")
        content  = parts[0].strip()
        hashtags = parts[1].strip().split() if len(parts) > 1 else ["#Wimbee", "#DataDigital", "#Tunisie"]
    else:
        # Fallback if model didn't follow the separator
        content  = raw.strip()
        hashtags = ["#Wimbee", "#DataDigital", "#Tunisie", "#Data", "#IA"]

    return {
        "content":  content,
        "hashtags": hashtags,
    }


def save_post(post_brief: dict, content: str, hashtags: list) -> Post:
    """
    Saves the generated post and hashtags to the database with status 'draft'.
    """
    db = SessionLocal()

    # Only include scheduled_time if the DB table actually has that column
    include_time = False
    try:
        from database import models as db_models

        with db_models.engine.connect() as conn:
            cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(posts)").fetchall()]
            include_time = "scheduled_time" in cols
    except Exception:
        include_time = False

    if include_time:
        db_post = Post(
            theme          = post_brief.get("theme"),
            format         = post_brief.get("format"),
            scheduled_date = post_brief.get("scheduled_date"),
            scheduled_time = post_brief.get("scheduled_time", "08:30"),
            special_day    = post_brief.get("special_day"),
            trend_source   = post_brief.get("trend_source"),
            content        = content,
            hashtags       = " ".join(hashtags),
            retry_count    = 0,
            status         = "draft",
            created_at     = datetime.datetime.utcnow(),
        )
    else:
        db_post = Post(
            theme          = post_brief.get("theme"),
            format         = post_brief.get("format"),
            scheduled_date = post_brief.get("scheduled_date"),
            special_day    = post_brief.get("special_day"),
            trend_source   = post_brief.get("trend_source"),
            content        = content,
            hashtags       = " ".join(hashtags),
            retry_count    = 0,
            status         = "draft",
            created_at     = datetime.datetime.utcnow(),
        )

    db.add(db_post)

    try:
        db.commit()
        db.refresh(db_post)

    except Exception as exc:
        from sqlalchemy.exc import OperationalError

        msg = str(exc)

        if isinstance(exc, OperationalError) and "no column named scheduled_time" in msg:

            try:
                from database import models as db_models

                with db_models.engine.connect() as conn:

                    cols = [
                        r[1]
                        for r in conn.exec_driver_sql(
                            "PRAGMA table_info(posts)"
                        ).fetchall()
                    ]

                    insert_cols = [
                        "theme",
                        "format",
                        "scheduled_date",
                        "special_day",
                        "trend_source",
                        "content",
                        "hashtags",
                        "score",
                        "retry_count",
                        "status",
                        "created_at",
                        "approval_token",
                        "approval_deadline",
                    ]

                    available = [
                        c for c in insert_cols if c in cols
                    ]

                    placeholders = ", ".join(
                        ["?" for _ in available]
                    )

                    sql = (
                        f"INSERT INTO posts "
                        f"({', '.join(available)}) "
                        f"VALUES ({placeholders})"
                    )

                    values = [
                        getattr(db_post, c)
                        for c in available
                    ]

                    conn.exec_driver_sql(
                        sql,
                        tuple(values)
                    )

                    try:
                        last_id = conn.exec_driver_sql(
                            "SELECT last_insert_rowid()"
                        ).fetchone()[0]

                        db_post.id = last_id

                    except Exception:
                        pass

            except Exception:
                raise

        else:
            raise

    finally:
        db.close()


    print(
        f" Post saved (id={db_post.id}) | {db_post.theme[:50]}"
    )

    return db_post