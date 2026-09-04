from llm import get_llm
from database.models import SessionLocal, Post
import datetime
import json
import os
import logging

log = logging.getLogger(__name__)


def _gemini_retry_delay(error: str, default: float = 30.0) -> float:
    match = re.search(r"retryDelay\s*[\"']?\s*[:=]\s*[\"']?(\d+(?:\.\d+)?)s", error, re.IGNORECASE)
    return float(match.group(1)) if match else default

def chat(prompt: str, temperature: float = 0.7) -> str:
    llm = get_llm(role="content")
    response = llm.complete(prompt, temperature=temperature)
    if response.ok:
        return response.text
    raise RuntimeError(response.error)

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

_IMAGE_PROMPT_TEMPLATE = """Tu es un expert en création de visuels LinkedIn professionnel.
Crée un prompt descriptif et concis pour générer une image professionnelle basée sur ce post :

POST :
{post_content}

Réponds UNIQUEMENT avec un prompt d'image court (1-2 phrases), sans JSON ni explications.
Le prompt doit être en anglais pour FLUX.1-schnell.
Exemple de réponse : "Professional infographic about data analytics trends, modern blue and white design, clean typography, minimal text"
"""

def _generate_image_prompt(post_content: str) -> str:
    """Use LLM to generate a visual prompt from post content."""
    try:
        response = get_llm(role="content").complete(
            _IMAGE_PROMPT_TEMPLATE.format(post_content=post_content),
            temperature=0.6
        )
        if response.ok:
            return response.text.strip()
        else:
            log.warning(f"Failed to generate image prompt: {response.error}")
            return None
    except Exception as e:
        log.error(f"Error generating image prompt: {e}")
        return None


def _call_flux_image_generation(prompt: str, post_id: int) -> str | None:
    """Call FLUX.1-schnell via Hugging Face Inference API to generate image."""
    hf_api_key = os.getenv("HUGGINGFACE_API_KEY", "").strip()
    
    if not hf_api_key:
        log.warning(f"HUGGINGFACE_API_KEY not set; skipping image generation for post {post_id}")
        return None

    try:
        import requests
        import time

        api_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
        headers = {"Authorization": f"Bearer {hf_api_key}"}
        payload = {"inputs": prompt}
        log.info(f"Generating image for post {post_id} with prompt: {prompt[:80]}...")

        response = requests.post(api_url, headers=headers, json=payload, timeout=120)
        if response.status_code != 200:
            log.error(f"Image generation failed for post {post_id}: {response.status_code} — {response.text}")
            return None

        image_dir = os.path.join(os.getcwd(), "generated_images")
        os.makedirs(image_dir, exist_ok=True)
        image_path = os.path.join(image_dir, f"post_{post_id}_{int(time.time())}.png")
        with open(image_path, "wb") as f:
            f.write(response.content)

        log.info(f"Image generated for post {post_id}: {image_path}")
        return image_path
    except Exception as e:
        log.error(f"Image generation error for post {post_id}: {e}")
        return None


def generate_image_for_post(post_id: int, prompt: str | None = None) -> str | None:
    """Generate and persist an image for an existing post.

    The post graph creates the row before content generation, so it can reuse
    the same image implementation without introducing a second agent module.
    """
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if post is None:
            log.warning("Post %s not found; cannot generate image", post_id)
            return None
        image_prompt = prompt or post.content or "Professional LinkedIn visual about data, digital transformation, and AI"
    finally:
        db.close()

    image_path = _call_flux_image_generation(image_prompt, post_id)
    if image_path is None:
        return None

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if post is not None:
            post.image_path = image_path
            db.commit()
    finally:
        db.close()
    return image_path


def run_content(post_brief: dict, retry_feedback: str = None):
    """Generate post content and optionally an image if format requires it."""
    
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

    # Generate visuals for all visual format spellings produced by the planner.
    image_path = None
    image_prompt = None
    post_format = (post_brief.get("format") or "").strip().lower()
    if post_format in {"image", "photo", "carousel", "carrousel"}:
        log.info(f"Post format is '{post_format}' — generating image...")
        
        # Generate image prompt using LLM
        image_prompt = _generate_image_prompt(content)
        
        if image_prompt:
            log.info(f"Generated image prompt: {image_prompt[:100]}...")
            # Call FLUX.1-schnell (but post_id is not available yet at this stage)
            # So we'll return the prompt and let the caller handle the actual image generation
            # OR generate it here if post already exists
        else:
            log.warning("Failed to generate image prompt; skipping image generation")

    return {
        "content":  content,
        "hashtags": hashtags,
        "image_prompt": image_prompt if post_format in {"image", "photo", "carousel", "carrousel"} else None,
    }


def save_post(post_brief: dict, content: str, hashtags: list, image_prompt: str = None) -> Post:
    """
    Saves the generated post and hashtags to the database with status 'draft'.
    If image_prompt is provided and format is image/carousel, generates the image.
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

    # NEW: Generate image if format requires it and prompt is available
    post_format = (post_brief.get("format") or "").strip().lower()
    if post_format in ["image", "photo", "carousel"] and image_prompt:
        image_path = _call_flux_image_generation(image_prompt, db_post.id)
        
        if image_path:
            db = SessionLocal()
            try:
                post = db.query(Post).filter(Post.id == db_post.id).first()
                if post:
                    post.image_path = image_path
                    db.commit()
                    log.info(f"Image path saved to post {db_post.id}")
            finally:
                db.close()

    print(
        f" Post saved (id={db_post.id}) | {db_post.theme[:50]}"
    )

    return db_post