"""Cross-source deduplication for normativo search results.

Merges overlapping results from LexML, TCU Dados Abertos, and Google Custom
Search using three strategies applied in priority order:

1. Exact ID match (SHA-256 of tipo|numero|data) -- O(1) dict lookup.
2. Tipo + Numero case-insensitive match -- O(1) dict lookup.
3. Fuzzy ementa comparison via difflib.SequenceMatcher (threshold >= 0.85).

The O(n^2) fuzzy phase is acceptable for result sets under 1000 items.
For larger sets, fuzzy matching is skipped as a safety measure.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata

from models import NormativoResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Threshold for fuzzy ementa matching. Two ementas with a SequenceMatcher
# ratio at or above this value are considered duplicates. Tuned empirically:
# 0.85 catches reformatted ementas while avoiding false merges of distinct
# normativos that share boilerplate language.
_FUZZY_THRESHOLD = 0.85

# Pre-compiled regex patterns for _normalize() to avoid recompilation per call
_RE_WHITESPACE = re.compile(r"\s+")
_RE_PUNCTUATION = re.compile(r"[^\w\s]")

# Maximum result set size for fuzzy matching. Beyond this count, fuzzy
# matching is skipped to avoid O(n^2) performance degradation, and only
# ID and tipo+numero matching are applied.
_FUZZY_MAX_ITEMS = 1000

# Source authority ranking: lower index = higher authority
_SOURCE_PRIORITY = {"lexml": 0, "tcu": 1, "google": 2}


# ---------------------------------------------------------------------------
# Normalization helper
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Normalize text for fuzzy comparison.

    Applies the following transformations in order:
    1. Strip leading/trailing whitespace
    2. Convert to lowercase
    3. Remove diacritical marks (accents) so 'regulamentacao' matches 'regulamentacao'
    4. Collapse all whitespace sequences (spaces, tabs, newlines) to single space
    5. Remove common punctuation that varies between sources

    Args:
        text: Raw ementa or title text.

    Returns:
        Normalized string suitable for SequenceMatcher comparison.
    """
    if not text:
        return ""

    text = text.strip().lower()

    # Remove accents: decompose unicode, drop combining marks, recompose
    nfkd = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in nfkd if not unicodedata.combining(ch))

    # Collapse whitespace
    text = _RE_WHITESPACE.sub(" ", text)

    # Remove punctuation that varies between sources (periods, commas, semicolons,
    # dashes, parentheses, quotes). Keep alphanumeric and spaces only.
    text = _RE_PUNCTUATION.sub("", text)

    return text


# ---------------------------------------------------------------------------
# Merge helper
# ---------------------------------------------------------------------------


def _merge(existing: NormativoResult, incoming: NormativoResult) -> None:
    """Merge incoming duplicate into the existing record in-place.

    Merge rules:
    - ementa: keep whichever is longer (more informative)
    - source: combine as comma-separated, deduplicated
    - found_by: combine as comma-separated, deduplicated
    - relevancia: keep the higher score
    - link: keep link from most authoritative source (lexml > tcu > google)
    - nome: keep whichever is longer (more descriptive)
    - orgao_emissor: keep whichever is non-empty, prefer longer
    - categoria: keep existing if non-empty, else take incoming
    - situacao: keep existing if non-empty, else take incoming
    - data: keep existing if non-empty, else take incoming

    Args:
        existing: The record already in the output list. Modified in-place.
        incoming: The new duplicate record to merge from.
    """
    # Save original source names BEFORE combining, so link priority
    # comparison uses the true original source, not the merged value.
    original_existing_source = existing.source
    original_incoming_source = incoming.source

    # Ementa: prefer longer (more information)
    if len(incoming.ementa or "") > len(existing.ementa or ""):
        existing.ementa = incoming.ementa

    # Nome: prefer longer
    if len(incoming.nome or "") > len(existing.nome or ""):
        existing.nome = incoming.nome

    # Source: combine and deduplicate
    existing_sources = set(s.strip() for s in existing.source.split(",") if s.strip())
    incoming_sources = set(s.strip() for s in incoming.source.split(",") if s.strip())
    combined_sources = existing_sources | incoming_sources
    existing.source = ", ".join(sorted(combined_sources))

    # Found_by: combine and deduplicate
    existing_keywords = set(
        k.strip() for k in existing.found_by.split(",") if k.strip()
    )
    incoming_keywords = set(
        k.strip() for k in incoming.found_by.split(",") if k.strip()
    )
    combined_keywords = existing_keywords | incoming_keywords
    existing.found_by = ", ".join(sorted(combined_keywords))

    # Relevancia: keep higher score
    existing.relevancia = max(existing.relevancia, incoming.relevancia)

    # Link: prefer more authoritative source (using original source names)
    existing_priority = _SOURCE_PRIORITY.get(
        original_existing_source.split(",")[0].strip(), 99
    )
    incoming_priority = _SOURCE_PRIORITY.get(
        original_incoming_source.split(",")[0].strip(), 99
    )
    if incoming_priority < existing_priority:
        existing.link = incoming.link

    # Orgao emissor: prefer longer non-empty value
    if not existing.orgao_emissor and incoming.orgao_emissor:
        existing.orgao_emissor = incoming.orgao_emissor
    elif (
        incoming.orgao_emissor
        and len(incoming.orgao_emissor) > len(existing.orgao_emissor or "")
    ):
        existing.orgao_emissor = incoming.orgao_emissor

    # Categoria: keep existing if non-empty, else take incoming
    if not existing.categoria and incoming.categoria:
        existing.categoria = incoming.categoria

    # Situacao: keep existing if non-empty, else take incoming
    if not existing.situacao and incoming.situacao:
        existing.situacao = incoming.situacao

    # Data: keep existing if non-empty, else take incoming
    if not existing.data and incoming.data:
        existing.data = incoming.data


# ---------------------------------------------------------------------------
# Main deduplication function
# ---------------------------------------------------------------------------


def deduplicate(results: list[NormativoResult]) -> list[NormativoResult]:
    """Remove duplicates from merged search results.

    Applies three deduplication strategies in priority order:
    1. Exact ID match (SHA-256 of tipo|numero|data)
    2. Tipo + Numero case-insensitive match
    3. Fuzzy ementa comparison (SequenceMatcher ratio >= 0.85)

    When a duplicate is found, the records are merged: the existing record
    is updated with the best metadata from both (see _merge for rules).

    Args:
        results: Combined results from all searchers. May contain duplicates
                 across sources. Order is preserved (first occurrence wins).

    Returns:
        Deduplicated list of NormativoResult objects, preserving the order
        of first occurrence. Source and found_by fields reflect all original
        sources that contributed to each merged record.
    """
    if not results:
        return []

    seen_ids: dict[str, int] = {}  # id -> index in output list
    seen_tipo_num: dict[tuple[str, str], int] = {}  # (tipo, numero) -> index
    output: list[NormativoResult] = []
    # Parallel list caching normalized ementas for each output record,
    # avoiding redundant _normalize() calls during the O(n^2) fuzzy phase.
    normalized_ementas: list[str] = []

    skip_fuzzy = len(results) > _FUZZY_MAX_ITEMS
    if skip_fuzzy:
        logger.info(
            "Fuzzy matching skipped: result set size (%d) exceeds threshold (%d).",
            len(results),
            _FUZZY_MAX_ITEMS,
        )

    for result in results:
        # --- Strategy 1: Exact ID match ---
        if result.id and result.id in seen_ids:
            existing = output[seen_ids[result.id]]
            logger.debug(
                "Merged duplicate: '%s' into '%s' (strategy: id_match)",
                result.nome, existing.nome,
            )
            _merge(existing, result)
            continue

        # --- Strategy 2: Tipo + Numero match ---
        tipo_lower = (result.tipo or "").lower().strip()
        numero_lower = (result.numero or "").lower().strip()
        key = (tipo_lower, numero_lower)

        if tipo_lower and numero_lower and key in seen_tipo_num:
            existing = output[seen_tipo_num[key]]
            logger.debug(
                "Merged duplicate: '%s' into '%s' (strategy: tipo_numero)",
                result.nome, existing.nome,
            )
            _merge(existing, result)
            continue

        # --- Strategy 3: Fuzzy ementa match ---
        merged = False
        if not skip_fuzzy and result.ementa:
            normalized_incoming = _normalize(result.ementa)
            if normalized_incoming:  # skip if ementa normalizes to empty
                for i, existing in enumerate(output):
                    normalized_existing = normalized_ementas[i]
                    if not normalized_existing:
                        continue

                    try:
                        ratio = difflib.SequenceMatcher(
                            None, normalized_existing, normalized_incoming
                        ).ratio()
                    except Exception as exc:
                        logger.warning(
                            "SequenceMatcher error comparing '%s' with '%s': %s",
                            existing.nome, result.nome, exc,
                        )
                        continue

                    if ratio >= _FUZZY_THRESHOLD:
                        logger.debug(
                            "Merged duplicate: '%s' into '%s' (strategy: fuzzy, ratio: %.2f)",
                            result.nome, existing.nome, ratio,
                        )
                        _merge(existing, result)
                        # Update cached ementa if the merge picked the longer one
                        normalized_ementas[i] = _normalize(existing.ementa)
                        merged = True
                        break

        if not merged:
            idx = len(output)
            output.append(result)
            normalized_ementas.append(_normalize(result.ementa) if result.ementa else "")

            # Index for future lookups
            if result.id:
                seen_ids[result.id] = idx
            if tipo_lower and numero_lower:
                seen_tipo_num[key] = idx

    removed = len(results) - len(output)
    if removed > 0:
        logger.info(
            "Deduplication complete: %d -> %d results (%d duplicates removed).",
            len(results), len(output), removed,
        )

    return output
