"""Gemini Flash client for keyword expansion, relevance scoring, and categorization.

This module encapsulates all communication with the Google Gemini API. It is the
ONLY module in the project that imports ``google.generativeai``. All other modules
interact with Gemini exclusively through the public functions exported here.

Every public function degrades gracefully when no API key is configured:
- expand_topic_to_keywords returns []
- score_relevance returns keyword-based heuristic scores or [0.5, ...]
- categorize_results returns ["Não categorizado", ...]

No function in this module ever raises an unhandled exception.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai
except ImportError:
    genai = None
    logger.info("google-generativeai not installed — LLM features disabled.")

# ---------------------------------------------------------------------------
# API Key Resolution
# Priority: st.secrets > env var > empty string (graceful degradation)
# ---------------------------------------------------------------------------

try:
    import streamlit as st
    api_key: str = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
except Exception:
    api_key: str = os.environ.get("GEMINI_API_KEY", "")

# ---------------------------------------------------------------------------
# Lazy Singleton Client
# ---------------------------------------------------------------------------

_client = None
_no_key_logged: bool = False


def _get_client():
    """Return the singleton GenerativeModel instance, or None if unavailable.

    Configures the SDK on first call. Subsequent calls return the cached instance.
    Returns None if the google-generativeai package is not installed or no API key
    is configured, allowing callers to fall back gracefully.
    """
    global _client, _no_key_logged

    if genai is None:
        return None

    if _client is None:
        if not api_key:
            if not _no_key_logged:
                logger.info("GEMINI_API_KEY not configured — LLM features disabled.")
                _no_key_logged = True
            return None
        genai.configure(api_key=api_key)
        _client = genai.GenerativeModel("gemini-2.0-flash")
    return _client


def is_available() -> bool:
    """Check if Gemini API is configured and available.

    Returns:
        True if a non-empty API key was found at module load time.
    """
    return genai is not None and bool(api_key)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE: int = 20
"""Maximum number of results to send in a single LLM call.

Keeps prompt size under the token limit and improves response reliability.
Gemini Flash supports 1M token context, but smaller prompts produce more
accurate structured output.
"""

CATEGORIES: list[str] = [
    "Governança de TI",
    "Segurança da Informação",
    "Contratações e Licitações",
    "Auditoria e Controle",
    "Proteção de Dados",
    "Transparência e Acesso à Informação",
    "Gestão de Riscos",
    "Software e Desenvolvimento",
    "Infraestrutura de TI",
    "Marco Legal e Regulatório",
    "Gestão de Pessoas",
    "Orçamento e Finanças",
    "Outro",
]
"""Predefined thematic categories for normativo classification.

Uses accented UTF-8 strings as the canonical category names throughout the
application. The LLM is instructed to return one of these exact strings, and
a fuzzy matcher handles minor variations in the response.
"""

# ---------------------------------------------------------------------------
# Private Helpers
# ---------------------------------------------------------------------------


def _parse_json_array(text: str) -> Optional[list]:
    """Extract and parse a JSON array from LLM response text.

    Handles common LLM output quirks:
    - Markdown code fences (``\\`\\`\\`json ... \\`\\`\\``` or ``\\`\\`\\` ... \\`\\`\\```)
    - Leading/trailing whitespace
    - Embedded arrays within explanation text

    Args:
        text: Raw response text from the LLM.

    Returns:
        Parsed list if successful, None if parsing fails.
    """
    # Step 1: Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip()

    # Step 2: Try direct JSON parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Step 3: Regex fallback — find first JSON array in text
    match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return None


def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into consecutive chunks of at most chunk_size elements.

    Args:
        items: The list to split.
        chunk_size: Maximum number of elements per chunk.

    Returns:
        List of sub-lists, each with at most chunk_size elements.
    """
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def _keyword_relevance(keywords: list[str], ementa: str) -> float:
    """Estimate relevance by counting keyword matches in the ementa.

    This is a simple heuristic fallback used when the LLM is unavailable.
    It counts how many of the search keywords appear (case-insensitive) in
    the ementa text and normalizes by the total number of keywords.

    Args:
        keywords: List of search keywords to look for.
        ementa: The ementa text to search within.

    Returns:
        Float in [0.0, 1.0] representing the fraction of keywords found.
    """
    if not keywords or not ementa:
        return 0.0

    ementa_lower = ementa.lower()
    matches = sum(1 for kw in keywords if kw.lower() in ementa_lower)
    return min(matches / max(len(keywords), 1), 1.0)


def _fuzzy_match_category(candidate: str) -> Optional[str]:
    """Attempt to match a candidate string to a known category.

    Handles common LLM output variations:
    - Case differences ("governança de ti" -> "Governança de TI")
    - Missing accents ("Governanca" -> "Governança")
    - Extra whitespace

    Args:
        candidate: The category string returned by the LLM.

    Returns:
        The matching CATEGORIES entry, or None if no match is found.
    """
    def _normalize(s: str) -> str:
        """Remove accents, lowercase, strip whitespace."""
        nfkd = unicodedata.normalize("NFKD", s)
        ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
        return ascii_only.lower().strip()

    candidate_norm = _normalize(candidate)
    for category in CATEGORIES:
        if _normalize(category) == candidate_norm:
            return category
    return None


# ---------------------------------------------------------------------------
# Public Functions
# ---------------------------------------------------------------------------


def expand_topic_to_keywords(topic: str) -> list[str]:
    """Generate search keywords from a natural language topic description.

    Uses Gemini Flash to expand a topic into a comprehensive list of search
    keywords for Brazilian legislation databases. If the LLM is unavailable,
    returns an empty list -- the caller should then prompt the user to enter
    keywords manually.

    Args:
        topic: Natural language description of the research topic in Portuguese.
               Example: "governança de TI no setor público"

    Returns:
        List of 15-30 keyword strings in Portuguese, or an empty list if the
        LLM is unavailable or encounters an error.

    Example:
        >>> expand_topic_to_keywords("governança de TI")
        ["governança de TI", "EGTI", "PDTIC", "SISP", "Decreto 10.332", ...]
    """
    # Sanitize: truncate excessively long topic strings
    topic = (topic or "")[:500].strip()

    # Guard: LLM unavailable
    client = _get_client()
    if client is None:
        logger.info("Gemini unavailable — skipping keyword expansion.")
        return []

    prompt = f'''Você é um especialista em legislação brasileira e auditoria governamental de TI.

Dado o tema: "{topic}"

Gere uma lista abrangente de palavras-chave de busca em português para encontrar TODAS as leis, decretos, instruções normativas, portarias, acórdãos do TCU e padrões/frameworks relevantes a este tema.

Inclua:
- Nomes específicos de leis conhecidas (ex: "Lei 13.709" para LGPD)
- Termos técnicos e suas variações
- Siglas e nomes por extenso
- Órgãos reguladores relevantes
- Frameworks e padrões internacionais (COBIT, ISO, COSO, ITIL)
- Termos relacionados que possam aparecer em ementas de normativos

Retorne APENAS um JSON array de strings, sem explicação adicional.
Exemplo de formato: ["palavra-chave 1", "palavra-chave 2", "palavra-chave 3"]

Gere entre 15 e 30 palavras-chave.'''

    try:
        response = client.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=1024,
            ),
        )

        if not response.text:
            logger.warning("Gemini returned empty response for keyword expansion.")
            return []

        parsed = _parse_json_array(response.text)
        if parsed is None:
            logger.warning("Failed to parse keyword list from Gemini response.")
            return []

        # Validate: must be a list of strings (coerce numeric values to str)
        keywords = [str(item) for item in parsed if isinstance(item, (str, int, float))]
        if not keywords:
            logger.warning("Gemini returned empty or non-string keyword list.")
            return []

        logger.info("Gemini expanded topic into %d keywords.", len(keywords))
        return keywords

    except Exception as e:
        logger.warning("Gemini API error during keyword expansion: %s", e)
        return []


def score_relevance(
    topic: str,
    results: list[dict],
    keywords: Optional[list[str]] = None,
) -> list[float]:
    """Score how relevant each search result is to the research topic.

    Processes results in batches of 20 to stay within token limits. When the
    LLM is unavailable, falls back to a keyword-matching heuristic.

    Args:
        topic: The original research topic in natural language.
        results: List of dicts, each containing at minimum:
                 - "nome" (str): Name/identifier of the normativo
                 - "ementa" (str): Summary/description text
                 Additional keys are ignored.
        keywords: Optional list of search keywords for the fallback heuristic.
                  Used only when the LLM is unavailable.

    Returns:
        List of float scores in [0.0, 1.0], same length and order as ``results``.
        Higher scores indicate greater relevance to the topic.

    Example:
        >>> score_relevance("LGPD", [{"nome": "Lei 13.709", "ementa": "Proteção de dados..."}])
        [0.95]
    """
    # Sanitize: truncate excessively long topic strings
    topic = (topic or "")[:500].strip()

    if not results:
        return []

    client = _get_client()

    # Fallback path: no LLM available
    if client is None:
        if keywords:
            logger.info("Gemini unavailable — using keyword heuristic for relevance.")
            return [
                _keyword_relevance(keywords, r.get("ementa", ""))
                for r in results
            ]
        logger.info("Gemini unavailable and no keywords — returning default scores.")
        return [0.5] * len(results)

    # LLM path: process in batches
    all_scores: list[float] = []
    batches = _chunk_list(results, BATCH_SIZE)

    for batch_idx, batch in enumerate(batches):
        formatted_list = "\n".join(
            f"{i+1}. {r.get('nome', '')}: {r.get('ementa', '')[:200]}"
            for i, r in enumerate(batch)
        )

        prompt = f'''Você é um especialista em legislação brasileira e auditoria de TI.

Tema da pesquisa: "{topic}"

Avalie a relevância de cada normativo abaixo para o tema acima.
Atribua uma nota de 0.0 (irrelevante) a 1.0 (altamente relevante).

Critérios:
- 0.8-1.0: Diretamente aplicável ao tema, normativo essencial
- 0.5-0.7: Relacionado ao tema, pode ser relevante
- 0.2-0.4: Tangencialmente relacionado
- 0.0-0.1: Não relacionado ao tema

Normativos:
{formatted_list}

Retorne APENAS um JSON array de números (floats), na mesma ordem dos normativos acima.
Exemplo: [0.9, 0.3, 0.7, 0.1]'''

        try:
            response = client.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=512,
                ),
            )

            if not response.text:
                logger.warning("Gemini returned empty response for relevance batch %d.", batch_idx)
                all_scores.extend([0.5] * len(batch))
                continue

            parsed = _parse_json_array(response.text)
            if parsed is not None and len(parsed) == len(batch):
                batch_scores = []
                for val in parsed:
                    try:
                        score = float(val)
                        score = max(0.0, min(1.0, score))  # Clamp to [0.0, 1.0]
                    except (TypeError, ValueError):
                        score = 0.5  # Default for unparseable individual scores
                    batch_scores.append(score)
                all_scores.extend(batch_scores)
            else:
                logger.warning(
                    "Batch %d: expected %d scores, got %s. Using 0.5 fallback.",
                    batch_idx, len(batch),
                    len(parsed) if parsed else "None",
                )
                all_scores.extend([0.5] * len(batch))

        except Exception as e:
            logger.warning("Gemini API error in relevance batch %d: %s", batch_idx, e)
            all_scores.extend([0.5] * len(batch))

        # Rate limiting for large result sets (>200 items = >10 batches)
        if len(batches) > 10 and batch_idx < len(batches) - 1:
            time.sleep(4.0)

    return all_scores


def categorize_results(topic: str, results: list[dict]) -> list[str]:
    """Assign a thematic category to each search result.

    Each result is assigned exactly one category from the CATEGORIES list.
    Processes results in batches of 20. When the LLM is unavailable, returns
    "Não categorizado" for all results.

    Args:
        topic: The original research topic (provides context for categorization).
        results: List of dicts with "nome" and "ementa" keys.

    Returns:
        List of category strings, same length and order as ``results``.
        Each string is guaranteed to be from CATEGORIES or "Não categorizado".

    Example:
        >>> categorize_results("LGPD", [{"nome": "Lei 13.709", "ementa": "Proteção de dados..."}])
        ["Proteção de Dados"]
    """
    # Sanitize: truncate excessively long topic strings
    topic = (topic or "")[:500].strip()

    if not results:
        return []

    client = _get_client()
    if client is None:
        logger.info("Gemini unavailable — returning uncategorized for all results.")
        return ["Não categorizado"] * len(results)

    all_categories: list[str] = []
    batches = _chunk_list(results, BATCH_SIZE)
    categories_list = "\n".join(f"- {cat}" for cat in CATEGORIES)

    for batch_idx, batch in enumerate(batches):
        formatted_list = "\n".join(
            f"{i+1}. {r.get('nome', '')}: {r.get('ementa', '')[:200]}"
            for i, r in enumerate(batch)
        )

        prompt = f'''Você é um especialista em legislação brasileira e auditoria de TI.

Categorize cada normativo abaixo em UMA das seguintes categorias:
{categories_list}

Normativos:
{formatted_list}

Retorne APENAS um JSON array de strings com a categoria de cada normativo, na mesma ordem.
Exemplo: ["Governança de TI", "Segurança da Informação", "Outro"]'''

        try:
            response = client.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=512,
                ),
            )

            if not response.text:
                logger.warning("Gemini returned empty response for categorize batch %d.", batch_idx)
                all_categories.extend(["Não categorizado"] * len(batch))
                continue

            parsed = _parse_json_array(response.text)
            if parsed is not None and len(parsed) == len(batch):
                for val in parsed:
                    val_str = str(val).strip()
                    if val_str in CATEGORIES:
                        all_categories.append(val_str)
                    else:
                        # Attempt fuzzy match for LLM output variations
                        matched = _fuzzy_match_category(val_str)
                        all_categories.append(matched if matched else "Outro")
            else:
                logger.warning(
                    "Batch %d: expected %d categories, got %s. Using fallback.",
                    batch_idx, len(batch),
                    len(parsed) if parsed else "None",
                )
                all_categories.extend(["Não categorizado"] * len(batch))

        except Exception as e:
            logger.warning("Gemini API error in categorize batch %d: %s", batch_idx, e)
            all_categories.extend(["Não categorizado"] * len(batch))

        # Rate limiting for large result sets (>200 items = >10 batches)
        if len(batches) > 10 and batch_idx < len(batches) - 1:
            time.sleep(4.0)

    return all_categories
