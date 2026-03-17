"""
Google-based searcher for international standards and frameworks.

Uses googlesearch-python to find documents from authoritative sources
(ISACA/COBIT, ISO, COSO, ITIL, gov.br) related to the search keywords.

This searcher complements LexML and TCU by finding reference material
that is not available in structured legal databases.

IMPORTANT: Google may temporarily block requests if too many are sent.
This searcher uses aggressive rate limiting (3s between queries) and
limits the number of keywords sent to Google (max 5) to minimize
the risk of blocking.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from googlesearch import search as google_search

from models import NormativoResult
from searchers.base import BaseSearcher, ProgressCallback

logger = logging.getLogger(__name__)

# Hostnames that must never be fetched (SSRF protection)
_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "localhost",
    "127.0.0.1",
})

# Site restriction appended to every Google query
SITE_RESTRICTION = (
    "site:isaca.org OR site:iso.org OR site:coso.org OR "
    "site:itsmf.org OR site:gov.br"
)

# Map URL domains to organization names
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

# Maximum keywords to send to Google (to avoid blocking)
MAX_GOOGLE_KEYWORDS = 5

# Results per Google query
RESULTS_PER_QUERY = 10

# Timeout for fetching individual pages (seconds)
PAGE_FETCH_TIMEOUT = 10


class GoogleSearcher(BaseSearcher):
    """Search Google for standards, frameworks, and government publications."""

    RATE_LIMIT_DELAY = 3.0    # Override: Google needs longer delays
    RATE_LIMIT_JITTER = 1.0   # Extra jitter for Google

    def source_name(self) -> str:
        return "Google (Frameworks/Padroes)"

    def search(
        self,
        keywords: list[str],
        max_results: int = 50,
        progress_callback: ProgressCallback = None,
    ) -> list[NormativoResult]:
        """Search Google for each keyword (up to MAX_GOOGLE_KEYWORDS).

        For each keyword, constructs a site-restricted Google query,
        retrieves URLs, fetches page metadata (title + description),
        and maps to NormativoResult objects.

        Results are deduplicated by URL.

        Args:
            keywords: Search terms. Only the first MAX_GOOGLE_KEYWORDS are used.
            max_results: Maximum total results.
            progress_callback: Optional callback(current, total, message).

        Returns:
            List of NormativoResult objects.
        """
        # Limit keywords to avoid Google blocking
        active_keywords = keywords[:MAX_GOOGLE_KEYWORDS]
        if len(keywords) > MAX_GOOGLE_KEYWORDS:
            logger.info(
                f"Google: limiting to first {MAX_GOOGLE_KEYWORDS} of "
                f"{len(keywords)} keywords to avoid blocking"
            )

        total_steps = len(active_keywords)
        seen_urls: set[str] = set()
        results: list[NormativoResult] = []

        for idx, keyword in enumerate(active_keywords):
            if len(results) >= max_results:
                break

            if progress_callback:
                progress_callback(idx, total_steps, f"Google: buscando '{keyword}'")

            logger.info(f"Google [{idx+1}/{total_steps}]: buscando '{keyword}'")

            query = f"{keyword} {SITE_RESTRICTION}"

            try:
                urls = list(google_search(query, num_results=RESULTS_PER_QUERY, lang="pt"))
            except Exception as e:
                # googlesearch-python may raise on blocking or network errors
                logger.warning(f"Google search failed for '{keyword}': {e}")
                continue

            logger.info(f"Google: {len(urls)} URLs returned for '{keyword}'")

            for url in urls:
                if len(results) >= max_results:
                    break

                # Deduplicate by normalized URL
                normalized_url = self._normalize_url(url)
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)

                # Fetch page metadata (title and description)
                title, description = self._fetch_page_metadata(url)

                # Extract organization from domain
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

            # Rate limit between Google queries
            if idx < total_steps - 1:
                self._rate_limit()

        # Final callback
        if progress_callback:
            progress_callback(
                total_steps, total_steps,
                f"Google: {len(results)} resultados encontrados"
            )

        logger.info(f"Google: total {len(results)} resultados")
        return results

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Validate that a URL is safe to fetch (SSRF protection).

        Only allows http/https schemes and blocks requests to private,
        loopback, and link-local IP ranges as well as known cloud
        metadata endpoints.

        Args:
            url: The URL to validate.

        Returns:
            True if the URL is considered safe, False otherwise.
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Only allow http and https schemes
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname or ""
        hostname_lower = hostname.lower()

        # Block known dangerous hostnames
        if hostname_lower in _BLOCKED_HOSTS:
            return False

        # Resolve hostname and block private/loopback/link-local IPs
        try:
            resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
            addr = ipaddress.ip_address(resolved_ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                logger.warning("Blocked SSRF attempt to private IP: %s -> %s", url, resolved_ip)
                return False
        except (socket.gaierror, ValueError, OSError):
            # If we cannot resolve the hostname, block it to be safe
            return False

        return True

    def _fetch_page_metadata(self, url: str) -> tuple[str, str]:
        """Fetch title and meta description from a URL.

        Args:
            url: Page URL to fetch.

        Returns:
            Tuple of (title, description). Both may be empty on failure.
        """
        # SSRF protection: validate URL before fetching
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
        except Exception as e:
            logger.debug(f"Failed to parse HTML from {url}: {e}")
            return "", ""

        # Extract title
        title = ""
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()

        # Extract meta description
        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()

        # Fallback: first 300 chars of visible body text
        if not description:
            body = soup.find("body")
            if body:
                text = body.get_text(separator=" ", strip=True)
                description = text[:300].strip()

        return title, description

    def _extract_org(self, url: str) -> str:
        """Extract organization name from URL domain.

        Uses longest-suffix matching against DOMAIN_ORG_MAP so that
        specific subdomains (e.g., tcu.gov.br) match before generic
        ones (e.g., gov.br).

        Args:
            url: Full URL.

        Returns:
            Organization name, or the raw domain if not mapped.
        """
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            return ""

        # Sort by length descending so tcu.gov.br matches before gov.br
        for mapped_domain in sorted(DOMAIN_ORG_MAP.keys(), key=len, reverse=True):
            if domain.endswith(mapped_domain):
                return DOMAIN_ORG_MAP[mapped_domain]

        return domain

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL for deduplication.

        Removes protocol, trailing slash, and www prefix.

        Args:
            url: Raw URL string.

        Returns:
            Normalized URL string.
        """
        url = url.strip().lower()
        for prefix in ("https://", "http://"):
            if url.startswith(prefix):
                url = url[len(prefix):]
        if url.startswith("www."):
            url = url[4:]
        return url.rstrip("/")
