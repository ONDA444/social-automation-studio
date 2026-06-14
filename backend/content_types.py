"""
Canonical catalogue of content types supported by the pipeline.

Single source of truth consumed by:
  - backend.routers.jobs        -> request validation + GET /jobs/content-types
  - the frontend                -> populates the content-type <select> dynamically

Each agent (scriptwriter / editing_director / seo_agent) carries its own
per-type config dict; the keys here MUST stay in sync with those (see
CONTENT_TYPE_KEYS). Adding a new type means: append one entry below AND a
matching entry in TEMPLATE_GUIDE, HEURISTICS and YT_CATEGORY.
"""
from __future__ import annotations

# Ordered, UI-facing list. `value` is the stable key used everywhere in the
# backend; `label`/`description` are human-facing (pt-BR) for the frontend.
CONTENT_TYPES: list[dict[str, str]] = [
    {
        "value": "film_recap_ai_images",
        "label": "Recap de Filme (imagens IA)",
        "description": "Recap dramático narrado estilo documentário, com imagens geradas por IA.",
    },
    {
        "value": "sports_highlights",
        "label": "Melhores Momentos (Esportes)",
        "description": "Lances e melhores momentos esportivos com tom energético e cortes rápidos.",
    },
    {
        "value": "quote_viral",
        "label": "Frase Viral",
        "description": "Frase única reflexiva/provocativa exibida na tela, sem narração.",
    },
    {
        "value": "top_list_ranking",
        "label": "Top 5/10 (Ranking)",
        "description": "Contagem regressiva 'Top N' com hook 'o nº1 vai te chocar' e cortes rápidos.",
    },
    {
        "value": "explainer_curiosity",
        "label": "Curiosidade Explicada",
        "description": "Formato 'por que X?' com curiosidade científica/histórica, narração + imagens IA.",
    },
    {
        "value": "true_crime_mystery",
        "label": "True Crime / Mistério",
        "description": "Caso ou mistério não resolvido, tom sombrio/noir e trilha tensa.",
    },
    {
        "value": "reaction_commentary",
        "label": "Reação / Comentário",
        "description": "Comentário dinâmico sobre tendência ou notícia do momento.",
    },
    {
        "value": "reddit_story",
        "label": "História do Reddit",
        "description": "Narração de história estilo 'r/...' com legendas grandes, vertical para Shorts.",
    },
    {
        "value": "motivational_speech",
        "label": "Discurso Motivacional",
        "description": "Discurso motivacional com b-roll cinematográfico e música épica.",
    },
]

# Set of valid content-type keys — used for O(1) validation in routers.
CONTENT_TYPE_KEYS: set[str] = {ct["value"] for ct in CONTENT_TYPES}


def public_list() -> list[dict[str, str]]:
    """Lightweight [{value, label}] list for populating a frontend <select>."""
    return [{"value": ct["value"], "label": ct["label"]} for ct in CONTENT_TYPES]
