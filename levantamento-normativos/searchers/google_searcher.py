"""
Web searcher for international standards, frameworks, and government publications.

Supports three search backends (in priority order):
1. Google Custom Search API — requires GOOGLE_API_KEY + GOOGLE_CSE_ID (most reliable)
2. DuckDuckGo (ddgs) — no keys needed, reliable, no blocking (default)
3. googlesearch-python scraping — no keys, but easily blocked by Google

This searcher complements LexML and TCU by finding reference material
that is not available in structured legal databases.
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from models import KeywordStatus, NormativoResult
from searchers.base import BaseSearcher, ProgressCallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API Key Resolution for Google Custom Search
# ---------------------------------------------------------------------------

try:
    import streamlit as st
    _google_api_key: str = st.secrets.get(
        "GOOGLE_API_KEY", os.environ.get("GOOGLE_API_KEY", "")
    )
    _google_cse_id: str = st.secrets.get(
        "GOOGLE_CSE_ID", os.environ.get("GOOGLE_CSE_ID", "")
    )
except Exception:
    _google_api_key = os.environ.get("GOOGLE_API_KEY", "")
    _google_cse_id = os.environ.get("GOOGLE_CSE_ID", "")

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

_BACKEND = "none"

if _google_api_key and _google_cse_id:
    _BACKEND = "cse"
    logger.info("Google Custom Search API keys found — using CSE API.")
else:
    # Try DuckDuckGo (preferred free option)
    try:
        from ddgs import DDGS
        _BACKEND = "ddgs"
        logger.info("Using DuckDuckGo (ddgs) — no API keys needed.")
    except ImportError:
        # Fallback to googlesearch-python scraping
        try:
            from googlesearch import search as _google_search_fn
            _BACKEND = "scraping"
            logger.info(
                "Using googlesearch-python (scraping fallback). "
                "Install 'ddgs' for more reliable search: pip install ddgs"
            )
        except ImportError:
            logger.warning(
                "No search backend available. Install 'ddgs' or "
                "'googlesearch-python', or configure Google CSE API keys."
            )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "localhost",
    "127.0.0.1",
})

# Site restriction for scraping mode (appended to query)
SITE_RESTRICTION = (
    "site:isaca.org OR site:iso.org OR site:coso.org OR "
    "site:itsmf.org OR site:gov.br"
)

# Domains to search (DuckDuckGo and CSE use these differently)
ALLOWED_DOMAINS = [
    "isaca.org", "iso.org", "coso.org", "itsmf.org", "gov.br",
]

DOMAIN_ORG_MAP = {
    "isaca.org": "ISACA (COBIT)",
    "iso.org": "ISO",
    "coso.org": "COSO",
    "itsmf.org": "itSMF (ITIL)",
    "gov.br": "Governo Federal",
    "tcu.gov.br": "TCU",
    "camara.leg.br": "Camara dos Deputados",
    "senado.leg.br": "Senado Federal",
    "planalto.gov.br": "Presidencia da Republica",
    "cgu.gov.br": "CGU",
}

MAX_GOOGLE_KEYWORDS = 5
RESULTS_PER_QUERY = 10
PAGE_FETCH_TIMEOUT = 10
CSE_API_URL = "https://www.googleapis.com/customsearch/v1"
CSE_TIMEOUT = 15


class GoogleSearcher(BaseSearcher):
    """Search the web for standards, frameworks, and government publications."""

    RATE_LIMIT_DELAY = 2.0
    RATE_LIMIT_JITTER = 0.5

    def source_name(self) -> str:
        return "Google (Frameworks/Padroes)"

    # ------------------------------------------------------------------
    # Search backend implementations
    # ------------------------------------------------------------------

    def _search_urls(self, keyword: str) -> tuple[list[dict], str]:
        """Search for results matching the keyword.

        Returns:
            Tuple of (results_list, error_message).
            Each result is a dict with keys: url, title, snippet.
            error_message is empty on success.
        """
        if _BACKEND == "cse":
            return self._search_cse_api(keyword)
        elif _BACKEND == "ddgs":
            return self._search_ddgs(keyword)
        elif _BACKEND == "scraping":
            return self._search_scraping(keyword)
        else:
            return [], "Nenhum backend de busca disponivel. Instale 'ddgs': pip install ddgs"

    def _search_ddgs(self, keyword: str) -> tuple[list[dict], str]:
        """Search using DuckDuckGo (ddgs package).

        Reliable, free, no API keys, no blocking.
        """
        from ddgs import DDGS

        # Build site-restricted query
        site_query = " OR ".join(f"site:{d}" for d in ALLOWED_DOMAINS)
        query = f"{keyword} ({site_query})"

        try:
            raw_results = list(DDGS().text(query, max_results=RESULTS_PER_QUERY))
            results = []
            for r in raw_results:
                results.append({
                    "url": r.get("href", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                })
            return results, ""
        except Exception as e:
            return [], f"DuckDuckGo: {e}"

    def _search_cse_api(self, keyword: str) -> tuple[list[dict], str]:
        """Search using Google Custom Search JSON API."""
        params = {
            "key": _google_api_key,
            "cx": _google_cse_id,
            "q": keyword,
            "num": min(RESULTS_PER_QUERY, 10),
            "lr": "lang_pt",
        }

        try:
            response = requests.get(CSE_API_URL, params=params, timeout=CSE_TIMEOUT)

            if response.status_code == 429:
                return [], "Google CSE: cota diaria excedida (100 queries/dia no plano gratuito)"
            if response.status_code == 403:
                return [], "Google CSE: acesso negado (verifique GOOGLE_API_KEY e GOOGLE_CSE_ID)"

            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("items", []):
                results.append({
                    "url": item.get("link", ""),
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results, ""

        except requests.exceptions.Timeout:
            return [], "Google CSE: timeout na requisicao"
        except requests.exceptions.RequestException as e:
            return [], f"Google CSE: erro de rede: {e}"
        except Exception as e:
            return [], f"Google CSE: erro inesperado: {e}"

    def _search_scraping(self, keyword: str) -> tuple[list[dict], str]:
        """Search using googlesearch-python (scraping, no API key needed)."""
        from googlesearch import search as google_search

        query = f"{keyword} {SITE_RESTRICTION}"
        try:
            urls = list(google_search(query, num_results=RESULTS_PER_QUERY, lang="pt"))
            return [{"url": u, "title": "", "snippet": ""} for u in urls], ""
        except Exception as e:
            return [], str(e)

    # ------------------------------------------------------------------
    # Main search method
    # ------------------------------------------------------------------

    def search(
        self,
        keywords: list[str],
        max_results: int = 50,
        progress_callback: ProgressCallback = None,
    ) -> list[NormativoResult]:
        """Search the web for each keyword (up to MAX_GOOGLE_KEYWORDS).

        Args:
            keywords: Search terms. Only the first MAX_GOOGLE_KEYWORDS are used.
            max_results: Maximum total results.
            progress_callback: Optional callback(current, total, message).

        Returns:
            List of NormativoResult objects.
        """
        active_keywords = keywords[:MAX_GOOGLE_KEYWORDS]
        if len(keywords) > MAX_GOOGLE_KEYWORDS:
            logger.info(
                f"Google: limiting to first {MAX_GOOGLE_KEYWORDS} of "
                f"{len(keywords)} keywords"
            )

        logger.info(f"Google: using backend '{_BACKEND}'")

        total_steps = len(active_keywords)
        seen_urls: set[str] = set()
        results: list[NormativoResult] = []
        self.keyword_statuses: list[KeywordStatus] = []
        failed_keywords: list[str] = []

        for idx, keyword in enumerate(active_keywords):
            if len(results) >= max_results:
                break

            if progress_callback:
                progress_callback(idx, total_steps, f"Google: buscando '{keyword}'")

            logger.info(f"Google [{idx+1}/{total_steps}]: buscando '{keyword}'")

            search_results, error_msg = self._search_urls(keyword)

            if error_msg:
                logger.warning(f"Google search failed for '{keyword}': {error_msg}")
                self.keyword_statuses.append(KeywordStatus(
                    keyword=keyword, source="google", result_count=0,
                    status="error", error_message=error_msg,
                ))
                failed_keywords.append(keyword)
                continue

            logger.info(f"Google: {len(search_results)} results returned for '{keyword}'")

            # Detect possible blocking (scraping mode only)
            if _BACKEND == "scraping" and len(search_results) == 0:
                consecutive_zeros = sum(
                    1 for s in self.keyword_statuses
                    if s.source == "google" and s.result_count == 0
                )
                if consecutive_zeros >= 2:
                    logger.warning(
                        f"Google scraping: {consecutive_zeros + 1} consecutive zeros. "
                        f"Possible IP blocking."
                    )
                    self.keyword_statuses.append(KeywordStatus(
                        keyword=keyword, source="google", result_count=0,
                        status="error",
                        error_message=(
                            "Google retornou 0 resultados (possivel bloqueio de IP). "
                            "Instale 'ddgs' para busca sem bloqueio: pip install ddgs"
                        ),
                    ))
                    continue

            kw_count = 0
            for sr in search_results:
                if len(results) >= max_results:
                    break

                url = sr.get("url", "")
                if not url:
                    continue

                normalized_url = self._normalize_url(url)
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)

                # Use title/snippet from search results when available
                title = sr.get("title", "")
                description = sr.get("snippet", "")

                # If search results didn't include metadata, fetch from page
                if not title or not description:
                    fetched_title, fetched_desc = self._fetch_page_metadata(url)
                    title = title or fetched_title
                    description = description or fetched_desc

                org = self._extract_org(url)

                results.append(NormativoResult(
                    nome=title if title else url,
                    tipo="Framework/Padrao",
                    numero="",
                    data=None,
                    orgao_emissor=org,
                    ementa=description,
                    link=url,
                    source="google",
                    found_by=keyword,
                    relevancia=0.3,
                    raw_data={"url": url, "title": title, "description": description},
                ))
                kw_count += 1

            self.keyword_statuses.append(KeywordStatus(
                keyword=keyword, source="google", result_count=kw_count,
                status="ok" if kw_count > 0 else "empty",
            ))

            if idx < total_steps - 1:
                self._rate_limit()

        # --- Retry failed keywords (max 3, bail if API is down) ---
        MAX_RETRIES = 3
        if failed_keywords:
            retry_count = min(len(failed_keywords), MAX_RETRIES)
            logger.info(f"Google: retrying {retry_count} of {len(failed_keywords)} failed keywords")
            import time
            time.sleep(5)

            api_still_down = False
            for keyword in failed_keywords[:MAX_RETRIES]:
                if len(results) >= max_results or api_still_down:
                    for kws in self.keyword_statuses:
                        if kws.keyword == keyword and kws.source == "google" and kws.status == "error":
                            kws.retried = True
                            if api_still_down:
                                kws.error_message = "API indisponivel (retry skipped)"
                            break
                    continue

                search_results, error_msg = self._search_urls(keyword)
                for kws in self.keyword_statuses:
                    if kws.keyword == keyword and kws.source == "google" and kws.status == "error":
                        kws.retried = True
                        if error_msg:
                            kws.error_message = f"Retry failed: {error_msg}"
                            api_still_down = True
                        else:
                            kw_count = 0
                            for sr in search_results:
                                if len(results) >= max_results:
                                    break
                                url = sr.get("url", "")
                                if not url:
                                    continue
                                normalized_url = self._normalize_url(url)
                                if normalized_url in seen_urls:
                                    continue
                                seen_urls.add(normalized_url)
                                title = sr.get("title", "")
                                description = sr.get("snippet", "")
                                if not title or not description:
                                    ft, fd = self._fetch_page_metadata(url)
                                    title = title or ft
                                    description = description or fd
                                org = self._extract_org(url)
                                results.append(NormativoResult(
                                    nome=title if title else url,
                                    tipo="Framework/Padrao", numero="", data=None,
                                    orgao_emissor=org, ementa=description, link=url,
                                    source="google", found_by=keyword, relevancia=0.3,
                                    raw_data={"url": url, "title": title, "description": description},
                                ))
                                kw_count += 1
                            kws.status = "ok" if kw_count > 0 else "empty"
                            kws.error_message = ""
                            kws.result_count = kw_count
                        break

                if not api_still_down:
                    self._rate_limit()

        if progress_callback:
            progress_callback(
                total_steps, total_steps,
                f"Google: {len(results)} resultados encontrados"
            )

        logger.info(f"Google: total {len(results)} resultados")
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Validate that a URL is safe to fetch (SSRF protection)."""
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname or ""
        if hostname.lower() in _BLOCKED_HOSTS:
            return False
        try:
            resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
            addr = ipaddress.ip_address(resolved_ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                logger.warning("Blocked SSRF attempt to private IP: %s -> %s", url, resolved_ip)
                return False
        except (socket.gaierror, ValueError, OSError):
            return False
        return True

    def _fetch_page_metadata(self, url: str) -> tuple[str, str]:
        """Fetch title and meta description from a URL."""
        if not self._is_safe_url(url):
            logger.warning("Skipping unsafe URL: %s", url)
            return "", ""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        try:
            response = requests.get(url, headers=headers, timeout=PAGE_FETCH_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.debug(f"Failed to fetch metadata from {url}: {e}")
            return "", ""
        try:
            soup = BeautifulSoup(response.content, "html.parser")
        except Exception:
            return "", ""

        title = ""
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()

        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()
        if not description:
            body = soup.find("body")
            if body:
                description = body.get_text(separator=" ", strip=True)[:300].strip()

        return title, description

    def _extract_org(self, url: str) -> str:
        """Extract organization name from URL domain."""
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            return ""
        for mapped_domain in sorted(DOMAIN_ORG_MAP.keys(), key=len, reverse=True):
            if domain.endswith(mapped_domain):
                return DOMAIN_ORG_MAP[mapped_domain]
        return domain

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL for deduplication."""
        url = url.strip().lower()
        for prefix in ("https://", "http://"):
            if url.startswith(prefix):
                url = url[len(prefix):]
        if url.startswith("www."):
            url = url[4:]
        return url.rstrip("/")
