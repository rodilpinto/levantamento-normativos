"""
Searchers package -- search backends for Brazilian legislation and standards.

Each searcher implements the BaseSearcher interface and returns NormativoResult
objects. Searchers can be used independently or orchestrated by the search
engine (Phase 3).

Available searchers:
- LexMLSearcher: Brazilian legislation via the LexML SRU API
- TCUSearcher: TCU acordaos and normative acts via the Open Data API
- GoogleSearcher: International standards and frameworks via Google Search
"""

from searchers.base import BaseSearcher
from searchers.lexml_searcher import LexMLSearcher
from searchers.tcu_searcher import TCUSearcher
from searchers.google_searcher import GoogleSearcher

__all__ = ["BaseSearcher", "LexMLSearcher", "TCUSearcher", "GoogleSearcher"]
