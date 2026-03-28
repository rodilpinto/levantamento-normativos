"""
LexML Brasil searcher using the SRU (Search/Retrieve via URL) API.

LexML is a federated portal of Brazilian legislation maintained by the
Federal Senate. It aggregates laws, decrees, normative instructions,
and other legal acts from all levels of government.

API documentation: https://www.lexml.gov.br/
SRU protocol: http://www.loc.gov/standards/sru/
"""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from models import KeywordStatus, NormativoResult
from searchers.base import BaseSearcher, ProgressCallback

logger = logging.getLogger(__name__)

# SRU endpoint URLs. The primary URL is tried first; if it fails with
# a 404 or connection error, the fallback is used.
PRIMARY_SRU_URL = "https://www.lexml.gov.br/busca/SRU"
FALLBACK_SRU_URL = "https://www.lexml.gov.br/sru/SRU"
FALLBACK_SRU_URL_2 = "https://www.lexml.gov.br/srw/SRU"

# XML namespaces used in SRU responses
NAMESPACES = {
    "srw": "http://www.loc.gov/zing/srw/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Regex to extract tipo, date, and number from LexML URN identifiers.
# URN format: urn:lex:br:ESFERA:TIPO:DATA;NUMERO
# The (?:[^:]*:)+ group matches the variable-length path segments
# between "br" and the tipo (e.g., "br:federal:", "br;sao.paulo:").
URN_PATTERN = re.compile(
    r"urn:lex:br(?:[^:]*:)+([^:]+):(\d{4}(?:-\d{2}-\d{2})?);?(\d*)"
)

# Map from URN tipo slug to display name
URN_TIPO_MAP = {
    "lei": "Lei",
    "lei.complementar": "Lei Complementar",
    "lei.ordinaria": "Lei Ordinária",
    "decreto": "Decreto",
    "decreto.lei": "Decreto-Lei",
    "instrucao.normativa": "Instrução Normativa",
    "portaria": "Portaria",
    "resolucao": "Resolução",
    "medida.provisoria": "Medida Provisória",
    "emenda.constitucional": "Emenda Constitucional",
    "portaria.normativa": "Portaria Normativa",
    "deliberacao": "Deliberação",
    "parecer": "Parecer",
    "sumula": "Súmula",
    "ato": "Ato",
}

# Records per SRU page request
RECORDS_PER_PAGE = 20

# HTTP timeout for SRU requests (seconds)
REQUEST_TIMEOUT = 15


class LexMLSearcher(BaseSearcher):
    """Search Brazilian legislation via the LexML SRU API."""

    RATE_LIMIT_DELAY = 1.0
    RATE_LIMIT_JITTER = 0.5

    def __init__(self):
        self._sru_url: Optional[str] = None  # Resolved after first request

    def source_name(self) -> str:
        return "LexML Brasil"

    def search(
        self,
        keywords: list[str],
        max_results: int = 50,
        progress_callback: ProgressCallback = None,
    ) -> list[NormativoResult]:
        """Search LexML for each keyword independently and merge results.

        Each keyword generates a separate SRU query. Results are deduplicated
        by ID (hash of tipo|numero|data). If the same normativo is found by
        multiple keywords, the found_by field accumulates all matching keywords.

        Keywords that fail due to API errors are retried once after all other
        keywords have been processed. The keyword_statuses attribute is
        populated with per-keyword diagnostic information.

        Args:
            keywords: Search terms to query against LexML.
            max_results: Maximum total results to return.
            progress_callback: Optional callback(current, total, message).

        Returns:
            Deduplicated list of NormativoResult objects.
        """
        results_by_id: dict[str, NormativoResult] = {}
        self.keyword_statuses: list[KeywordStatus] = []
        failed_keywords: list[str] = []
        total_keywords = len(keywords)

        for idx, keyword in enumerate(keywords):
            if len(results_by_id) >= max_results:
                logger.info(
                    f"LexML: reached max_results ({max_results}), "
                    f"stopping after {idx}/{total_keywords} keywords"
                )
                break

            if progress_callback:
                progress_callback(idx, total_keywords, f"LexML: buscando '{keyword}'")

            logger.info(f"LexML [{idx+1}/{total_keywords}]: buscando '{keyword}'")

            remaining = max_results - len(results_by_id)
            keyword_results, error_msg = self._search_keyword_safe(keyword, max_results=remaining)

            if error_msg:
                self.keyword_statuses.append(KeywordStatus(
                    keyword=keyword, source="lexml", result_count=0,
                    status="error", error_message=error_msg,
                ))
                failed_keywords.append(keyword)
            elif len(keyword_results) == 0:
                self.keyword_statuses.append(KeywordStatus(
                    keyword=keyword, source="lexml", result_count=0,
                    status="empty",
                ))
            else:
                # Merge into results_by_id, deduplicating by ID
                count_before = len(results_by_id)
                for result in keyword_results:
                    if result.id in results_by_id:
                        existing = results_by_id[result.id]
                        if keyword not in existing.found_by:
                            existing.found_by += f", {keyword}"
                    else:
                        results_by_id[result.id] = result
                new_count = len(results_by_id) - count_before
                self.keyword_statuses.append(KeywordStatus(
                    keyword=keyword, source="lexml",
                    result_count=new_count, status="ok",
                ))

            if idx < total_keywords - 1:
                self._rate_limit()

        # --- Retry failed keywords (max 3 attempts, bail if API is down) ---
        MAX_RETRIES = 3
        if failed_keywords:
            retry_count = min(len(failed_keywords), MAX_RETRIES)
            logger.info(
                f"LexML: retrying {retry_count} of {len(failed_keywords)} "
                f"failed keywords (max {MAX_RETRIES})"
            )
            if progress_callback:
                progress_callback(
                    total_keywords, total_keywords,
                    f"LexML: retentando {retry_count} palavras-chave com erro..."
                )

            import time
            time.sleep(3)

            # Only retry a few — if the first retry also fails, the API is
            # likely down and retrying more keywords would waste time.
            api_still_down = False
            for keyword in failed_keywords[:MAX_RETRIES]:
                if len(results_by_id) >= max_results:
                    break
                if api_still_down:
                    # Mark remaining as retried-but-failed without calling API
                    for st in self.keyword_statuses:
                        if st.keyword == keyword and st.source == "lexml" and st.status == "error":
                            st.retried = True
                            st.error_message = "API indisponivel (retry skipped)"
                            break
                    continue

                remaining = max_results - len(results_by_id)
                keyword_results, error_msg = self._search_keyword_safe(keyword, max_results=remaining)

                for st in self.keyword_statuses:
                    if st.keyword == keyword and st.source == "lexml" and st.status == "error":
                        st.retried = True
                        if error_msg:
                            st.error_message = f"Retry failed: {error_msg}"
                            api_still_down = True  # Stop retrying
                        elif len(keyword_results) == 0:
                            st.status = "empty"
                            st.error_message = ""
                        else:
                            st.status = "ok"
                            st.error_message = ""
                            for result in keyword_results:
                                if result.id in results_by_id:
                                    existing = results_by_id[result.id]
                                    if keyword not in existing.found_by:
                                        existing.found_by += f", {keyword}"
                                else:
                                    results_by_id[result.id] = result
                            st.result_count = len(keyword_results)
                        break

                if not api_still_down:
                    self._rate_limit()

        # Final progress callback
        if progress_callback:
            progress_callback(
                total_keywords, total_keywords,
                f"LexML: {len(results_by_id)} resultados encontrados"
            )

        logger.info(f"LexML: total {len(results_by_id)} resultados unicos")
        return list(results_by_id.values())

    def _search_keyword_safe(
        self, keyword: str, max_results: int = 50
    ) -> tuple[list[NormativoResult], str]:
        """Search for a keyword, returning (results, error_message).

        Returns:
            Tuple of (results_list, error_string). error_string is empty on success.
        """
        try:
            results = self._search_keyword(keyword, max_results=max_results)
            return results, ""
        except Exception as e:
            logger.warning(f"LexML: error searching keyword '{keyword}': {e}")
            return [], str(e)

    def _search_keyword(
        self, keyword: str, max_results: int = 50
    ) -> list[NormativoResult]:
        """Search LexML for a single keyword with pagination.

        Args:
            keyword: Single search term.
            max_results: Maximum results for this keyword.

        Returns:
            List of NormativoResult objects (may be empty on error).

        Raises:
            ConnectionError: If the SRU API is unreachable (all endpoints failed).
        """
        # Sanitize keyword to prevent CQL injection
        safe_kw = keyword.replace('"', '').replace('\\', '').strip()
        if not safe_kw:
            return []
        cql_query = (
            f'dc.description any "{safe_kw}" '
            f'OR dc.subject any "{safe_kw}" '
            f'OR dc.title any "{safe_kw}"'
        )

        all_results: list[NormativoResult] = []
        start_record = 1
        first_page = True

        while len(all_results) < max_results:
            params = {
                "operation": "searchRetrieve",
                "version": "1.1",
                "query": cql_query,
                "startRecord": start_record,
                "maximumRecords": RECORDS_PER_PAGE,
            }

            xml_text = self._fetch_sru(params)
            if xml_text is None:
                if first_page:
                    # First page failed — API is unreachable, raise so caller
                    # can distinguish from "found 0 results"
                    raise ConnectionError(
                        "LexML SRU API indisponivel (todos os endpoints falharam)"
                    )
                break  # Subsequent page failed; return what we have

            first_page = False
            records, total_count = self._parse_sru_response(xml_text, keyword)
            all_results.extend(records)

            # Check if there are more pages
            next_start = start_record + RECORDS_PER_PAGE
            if next_start > total_count or len(records) == 0:
                break  # No more pages

            start_record = next_start

            # Rate limit between pagination requests
            self._rate_limit()

        return all_results[:max_results]

    def _fetch_sru(self, params: dict) -> Optional[str]:
        """Send an SRU GET request, with fallback URL logic.

        On the first call, tries PRIMARY_SRU_URL. If it gets a 404 or
        connection error, tries FALLBACK_SRU_URL. The working URL is
        cached in self._sru_url for subsequent calls.

        Args:
            params: SRU query parameters dict.

        Returns:
            Response body as string, or None on failure.
        """
        # If we already know which URL works, use it directly
        if self._sru_url:
            return self._try_fetch(self._sru_url, params)

        # Try primary URL first
        result = self._try_fetch(PRIMARY_SRU_URL, params)
        if result is not None:
            self._sru_url = PRIMARY_SRU_URL
            return result

        # Primary failed -- try fallback URLs in order
        for fallback_url in (FALLBACK_SRU_URL, FALLBACK_SRU_URL_2):
            logger.warning(
                f"LexML: previous URL failed, trying fallback: {fallback_url}"
            )
            result = self._try_fetch(fallback_url, params)
            if result is not None:
                self._sru_url = fallback_url
                return result

        logger.error("LexML: all SRU endpoints failed")
        return None

    def _try_fetch(self, url: str, params: dict) -> Optional[str]:
        """Attempt a single GET request to the given SRU URL.

        Retries once on connection error after a 3-second delay.

        Args:
            url: SRU endpoint URL.
            params: Query parameters.

        Returns:
            Response text on success, None on failure.
        """
        import time

        for attempt in range(2):  # Max 2 attempts (initial + 1 retry)
            try:
                response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
                if response.status_code == 404:
                    logger.warning(f"LexML: 404 from {url}")
                    return None
                response.raise_for_status()
                return response.text
            except requests.exceptions.ConnectionError as e:
                if attempt == 0:
                    logger.warning(f"LexML connection error: {e}. Retrying in 3s...")
                    time.sleep(3)
                else:
                    logger.error(f"LexML connection error after retry: {e}")
                    return None
            except requests.exceptions.Timeout:
                logger.warning(f"LexML timeout ({REQUEST_TIMEOUT}s) for {url}")
                return None
            except requests.exceptions.RequestException as e:
                logger.error(f"LexML request error: {e}")
                return None

        return None

    def _parse_sru_response(
        self, xml_text: str, keyword: str
    ) -> tuple[list[NormativoResult], int]:
        """Parse an SRU XML response into NormativoResult objects.

        Args:
            xml_text: Raw XML response body.
            keyword: The keyword that produced this response (for found_by).

        Returns:
            Tuple of (list of NormativoResult, total number of records reported
            by the server). Returns ([], 0) on parse error.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"LexML XML parse error: {e}")
            return [], 0

        # Total records reported by the server
        total_el = root.find("srw:numberOfRecords", NAMESPACES)
        total_count = int(total_el.text) if total_el is not None and total_el.text else 0

        results: list[NormativoResult] = []
        records = root.findall("srw:records/srw:record", NAMESPACES)

        for record in records:
            record_data = record.find("srw:recordData", NAMESPACES)
            if record_data is None:
                continue

            result = self._parse_record(record_data, keyword)
            if result is not None:
                results.append(result)

        return results, total_count

    def _parse_record(
        self, record_data: ET.Element, keyword: str
    ) -> Optional[NormativoResult]:
        """Parse a single SRU record into a NormativoResult.

        Args:
            record_data: The <srw:recordData> XML element.
            keyword: The keyword that found this record.

        Returns:
            NormativoResult or None if the record cannot be parsed.
        """
        # Helper to extract text from a Dublin Core element.
        # Some records nest DC elements inside an additional wrapper;
        # search recursively to handle both cases.
        def dc_text(tag: str) -> str:
            # Try direct child first
            el = record_data.find(f"dc:{tag}", NAMESPACES)
            if el is None:
                # Try recursive search (for nested record formats)
                el = record_data.find(f".//dc:{tag}", NAMESPACES)
            return el.text.strip() if el is not None and el.text else ""

        title = dc_text("title")
        description = dc_text("description")
        date_raw = dc_text("date")
        creator = dc_text("creator")
        dc_type = dc_text("type")
        identifier = dc_text("identifier")

        # Parse URN to extract tipo, date, and number
        tipo = ""
        numero = ""
        urn_date = ""
        link = ""

        if identifier and identifier.startswith("urn:lex:"):
            link = f"https://www.lexml.gov.br/urn/{identifier}"

            match = URN_PATTERN.search(identifier)
            if match:
                tipo_slug = match.group(1)
                urn_date = match.group(2)
                numero = match.group(3)

                # Map tipo slug to display name
                tipo = URN_TIPO_MAP.get(
                    tipo_slug,
                    tipo_slug.replace(".", " ").title()
                )

        # Determine the best date: prefer URN date, then dc:date
        date_str: Optional[str] = None
        if urn_date:
            date_str = self._safe_date_format(urn_date)
        elif date_raw:
            date_str = self._safe_date_format(date_raw)

        # Build the nome (display name)
        nome = title if title else f"{tipo} n. {numero}" if tipo and numero else identifier

        try:
            return NormativoResult(
                nome=nome,
                tipo=tipo,
                numero=numero,
                data=date_str,
                orgao_emissor=creator,
                ementa=description,
                link=link,
                source="lexml",
                found_by=keyword,
                relevancia=0.5,
                raw_data={
                    "dc_title": title,
                    "dc_description": description,
                    "dc_date": date_raw,
                    "dc_creator": creator,
                    "dc_type": dc_type,
                    "dc_identifier": identifier,
                },
            )
        except Exception as e:
            logger.warning(f"LexML: failed to create NormativoResult: {e}")
            return None
