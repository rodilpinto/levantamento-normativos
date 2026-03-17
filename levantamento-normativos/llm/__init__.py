"""LLM integration module for Gemini Flash.

Provides keyword expansion, relevance scoring, and auto-categorization
of Brazilian legislation search results. All functions degrade gracefully
when the Gemini API key is not configured.

Usage:
    from llm import is_available, expand_topic_to_keywords, score_relevance, categorize_results

    if is_available():
        keywords = expand_topic_to_keywords("governança de TI")
"""

from .gemini_client import (
    CATEGORIES,
    categorize_results,
    expand_topic_to_keywords,
    is_available,
    score_relevance,
)

__all__ = [
    "is_available",
    "expand_topic_to_keywords",
    "score_relevance",
    "categorize_results",
    "CATEGORIES",
]
