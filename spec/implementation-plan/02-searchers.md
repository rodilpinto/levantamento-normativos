# Phase 2: Search Engine — Searchers Module

## Overview

This phase implements the three search backends that query Brazilian legislation sources and return normalized `NormativoResult` objects. Each searcher follows a common abstract interface (`BaseSearcher`) and can be used independently or orchestrated by the search engine (Phase 3).

The searchers module lives at `searchers/` in the project root and contains:

```
searchers/
    __init__.py
    base.py
    lexml_searcher.py
    tcu_searcher.py
    google_searcher.py
```

### Dependencies on Phase 1

This phase depends on the `NormativoResult` dataclass defined in `models.py` (Phase 1). The dataclass has these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique identifier (generated per source) |
| `nome` | `str` | Human-readable name of the normativo |
| `tipo` | `str` | Type classification (Lei, Decreto, Acordao TCU, etc.) |
| `numero` | `str` | Official number |
| `data` | `str` | Publication date in DD/MM/YYYY format |
| `orgao_emissor` | `str` | Issuing body |
| `ementa` | `str` | Summary/abstract text |
| `link` | `str` | URL to the official source |
| `categoria` | `str` | Thematic category |
| `situacao` | `str` | Current status (vigente, revogado, etc.) |
| `relevancia` | `float` | Relevance score from 0.0 to 1.0 |
| `source` | `str` | Which searcher found it ("lexml", "tcu", "google") |
| `found_by` | `str` | Which keyword(s) matched this result |
| `raw_data` | `dict` | Original API response data preserved for debugging |

### Relationship to Existing Codebase

The existing `dou_clipping.py` uses `progress_callback(current, total, message)` for reporting progress during `search_all_terms()`. All searchers in this module follow the same callback signature for UI integration consistency.

Rate limiting patterns (random jitter added to base delay) are also inherited from `dou_clipping.py` (line 428: `time.sleep(1.0 + random() * 1.5)`).

---

## 2.1 searchers/base.py — Abstract Base Class

### Purpose

Define the contract that all searchers must implement. Provide shared utilities for rate limiting and text normalization.

### Complete Implementation

```python
"""
Abstract base class for all normativo searchers.

Every search backend (LexML, TCU, Google) inherits from BaseSearcher
and implements search() and source_name(). Shared logic for rate
limiting and text normalization lives here.
"""

from abc import ABC, abstractmethod
import logging
import re
import time
import unicodedata
from random import random
from typing import Callable, Optional

# Type alias for the progress callback used across all searchers.
# Signature: callback(current_step: int, total_steps: int, message: str)
ProgressCallback = Optional[Callable[[int, int, str], None]]

logger = logging.getLogger(__name__)


class BaseSearcher(ABC):
    """Abstract base class for normativo search backends."""

    # Seconds to wait between consecutive API requests.
    # Subclasses may override for APIs with stricter limits.
    RATE_LIMIT_DELAY: float = 1.0

    # Maximum random jitter (seconds) added to the base delay.
    RATE_LIMIT_JITTER: float = 0.5

    @abstractmethod
    def search(
        self,
        keywords: list[str],
        max_results: int = 50,
        progress_callback: ProgressCallback = None,
    ) -> list:
        """Search for normativos matching the given keywords.

        Args:
            keywords: List of search terms (e.g. ["governanca de TI", "COBIT"]).
                      Each keyword is searched independently and results are merged.
            max_results: Maximum total results to return across all keywords.
            progress_callback: Optional callback invoked after each keyword is
                               processed. Signature: callback(current, total, message).
                               - current: 0-based step index
                               - total: total number of steps
                               - message: human-readable status string

        Returns:
            List of NormativoResult objects, deduplicated by id.

        Raises:
            No exceptions should propagate. All API/network errors are caught
            internally, logged, and the searcher returns partial results.
        """
        ...

    @abstractmethod
    def source_name(self) -> str:
        """Return a human-readable name for this search source.

        Used in UI labels (e.g. "LexML Brasil", "TCU Dados Abertos", "Google").
        """
        ...

    def _rate_limit(self) -> None:
        """Sleep to respect API rate limits.

        Adds random jitter to the base delay to avoid thundering-herd
        patterns when multiple searchers run concurrently.
        """
        delay = self.RATE_LIMIT_DELAY + random() * self.RATE_LIMIT_JITTER
        logger.debug(f"Rate limit: sleeping {delay:.2f}s")
        time.sleep(delay)

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for comparison.

        Applies: strip, lowercase, remove diacritics (accents),
        collapse multiple whitespace into single space.

        Used for deduplication and keyword matching where accent-insensitive
        comparison is needed.

        Args:
            text: Raw text string.

        Returns:
            Normalized string suitable for comparison.
        """
        if not text:
            return ""
        text = text.strip().lower()
        # Decompose Unicode characters, then remove combining marks (accents)
        nfd = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _safe_date_format(date_str: str) -> str:
        """Attempt to parse a date string and return DD/MM/YYYY format.

        Tries common formats: YYYY-MM-DD, YYYY, DD/MM/YYYY, DD-MM-YYYY.
        Returns the original string if no format matches.

        Args:
            date_str: Date string from an API response.

        Returns:
            Date in DD/MM/YYYY format, or the original string if unparseable.
        """
        if not date_str:
            return ""
        from datetime import datetime

        formats = [
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y",
            "%d-%m-%Y",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip()[:19], fmt)
                return dt.strftime("%d/%m/%Y")
            except ValueError:
                continue

        # If only a year was provided
        if re.match(r"^\d{4}$", date_str.strip()):
            return f"01/01/{date_str.strip()}"

        return date_str
```

### Key Design Decisions

1. **No exceptions propagate** — Every searcher catches all errors internally and returns partial results. This ensures one failing API does not block the others.

2. **`_normalize_text` is a static method** — It has no state dependency and is useful outside the class (e.g., in deduplication logic).

3. **`_safe_date_format` helper** — Each API returns dates in different formats. This centralizes date normalization.

4. **`ProgressCallback` type alias** — Defined at module level for reuse in type hints.

---

## 2.2 searchers/lexml_searcher.py — LexML Brasil SRU API

### API Reference

| Property | Value |
|----------|-------|
| Protocol | SRU (Search/Retrieve via URL) v1.1 |
| Primary URL | `https://www.lexml.gov.br/busca/SRU` |
| Fallback URL | `https://www.lexml.gov.br/sru/SRU` |
| Query language | CQL (Contextual Query Language) |
| Response format | XML (SRU namespace) |
| Authentication | None required |
| Rate limit | Not documented; 1.0-1.5s between requests is safe |

### SRU Request Parameters

All parameters are sent as URL query params via GET:

| Parameter | Type | Value | Description |
|-----------|------|-------|-------------|
| `operation` | str | `"searchRetrieve"` | Fixed operation name |
| `version` | str | `"1.1"` | SRU protocol version |
| `query` | str | CQL query | Search expression |
| `startRecord` | int | 1-based | Pagination offset |
| `maximumRecords` | int | `20` | Records per page (max 20 recommended) |

### CQL Query Construction

For a single keyword, the CQL query searches across description, subject, and title fields:

```
dc.description any "governanca de TI" OR dc.subject any "governanca de TI" OR dc.title any "governanca de TI"
```

**Important:** LexML has a CQL query length limit. Do NOT combine multiple keywords into one query. Instead, search each keyword separately and merge results.

For each keyword, build exactly this query:

```python
cql_query = (
    f'dc.description any "{keyword}" '
    f'OR dc.subject any "{keyword}" '
    f'OR dc.title any "{keyword}"'
)
```

### XML Response Structure

The response uses two XML namespaces:

| Prefix | URI | Usage |
|--------|-----|-------|
| `srw` | `http://www.loc.gov/zing/srw/` | SRU envelope |
| `dc` | `http://purl.org/dc/elements/1.1/` | Dublin Core metadata inside records |

Key XML elements:

```xml
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
  <srw:numberOfRecords>142</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <!-- Dublin Core metadata -->
        <dc:title>Lei nº 13.709, de 14 de agosto de 2018</dc:title>
        <dc:description>Dispõe sobre a proteção de dados pessoais...</dc:description>
        <dc:date>2018-08-14</dc:date>
        <dc:creator>Brasil. Presidência da República</dc:creator>
        <dc:type>Legislação</dc:type>
        <dc:identifier>urn:lex:br:federal:lei:2018-08-14;13709</dc:identifier>
      </srw:recordData>
    </srw:record>
    <!-- more records... -->
  </srw:records>
</srw:searchRetrieveResponse>
```

Namespace dict for `xml.etree.ElementTree` parsing:

```python
NAMESPACES = {
    "srw": "http://www.loc.gov/zing/srw/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
```

### LexML URN Parsing

The `dc:identifier` element contains a LexML URN that encodes the normativo type, date, and number. Extract these with regex:

```python
URN_PATTERN = re.compile(
    r"urn:lex:br[^:]*:([^:]+):(\d{4}(?:-\d{2}-\d{2})?);?(\d*)"
)
```

Capture groups:
- Group 1: tipo slug (e.g., `"lei"`, `"instrucao.normativa"`)
- Group 2: date portion (e.g., `"2018-08-14"` or `"2019"`)
- Group 3: number (e.g., `"13709"`) — may be empty

### URN Tipo Mapping

Map the URN tipo slug to a display-friendly name:

```python
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
```

If the tipo slug is not in the map, title-case it and replace dots with spaces (e.g., `"norma.tecnica"` becomes `"Norma Tecnica"`).

### Link Construction

From the URN identifier, construct the canonical LexML link:

```python
link = f"https://www.lexml.gov.br/urn/{urn_value}"
```

Where `urn_value` is the full URN string (e.g., `urn:lex:br:federal:lei:2018-08-14;13709`).

### ID Generation

Generate a stable, unique ID for each LexML result:

```python
# Use the URN as ID (guaranteed unique within LexML)
result_id = urn_value  # e.g., "urn:lex:br:federal:lei:2018-08-14;13709"
# Fallback if no URN: hash of nome + data
```

### Complete Implementation

```python
"""
LexML Brasil searcher using the SRU (Search/Retrieve via URL) API.

LexML is a federated portal of Brazilian legislation maintained by the
Federal Senate. It aggregates laws, decrees, normative instructions,
and other legal acts from all levels of government.

API documentation: https://www.lexml.gov.br/
SRU protocol: http://www.loc.gov/standards/sru/
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from models import NormativoResult
from searchers.base import BaseSearcher, ProgressCallback

logger = logging.getLogger(__name__)

# SRU endpoint URLs. The primary URL is tried first; if it fails with
# a 404 or connection error, the fallback is used.
PRIMARY_SRU_URL = "https://www.lexml.gov.br/busca/SRU"
FALLBACK_SRU_URL = "https://www.lexml.gov.br/sru/SRU"

# XML namespaces used in SRU responses
NAMESPACES = {
    "srw": "http://www.loc.gov/zing/srw/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Regex to extract tipo, date, and number from LexML URN identifiers
URN_PATTERN = re.compile(
    r"urn:lex:br[^:]*:([^:]+):(\d{4}(?:-\d{2}-\d{2})?);?(\d*)"
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
        by ID (URN). If the same normativo is found by multiple keywords,
        the found_by field accumulates all matching keywords.

        Args:
            keywords: Search terms to query against LexML.
            max_results: Maximum total results to return.
            progress_callback: Optional callback(current, total, message).

        Returns:
            Deduplicated list of NormativoResult objects.
        """
        results_by_id: dict[str, NormativoResult] = {}
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
            keyword_results = self._search_keyword(keyword, max_results=remaining)

            # Merge into results_by_id, deduplicating by ID
            for result in keyword_results:
                if result.id in results_by_id:
                    # Same normativo found by a different keyword — append to found_by
                    existing = results_by_id[result.id]
                    if keyword not in existing.found_by:
                        existing.found_by += f", {keyword}"
                else:
                    results_by_id[result.id] = result

            if idx < total_keywords - 1:
                self._rate_limit()

        # Final progress callback
        if progress_callback:
            progress_callback(
                total_keywords, total_keywords,
                f"LexML: {len(results_by_id)} resultados encontrados"
            )

        logger.info(f"LexML: total {len(results_by_id)} resultados unicos")
        return list(results_by_id.values())

    def _search_keyword(
        self, keyword: str, max_results: int = 50
    ) -> list[NormativoResult]:
        """Search LexML for a single keyword with pagination.

        Args:
            keyword: Single search term.
            max_results: Maximum results for this keyword.

        Returns:
            List of NormativoResult objects (may be empty on error).
        """
        cql_query = (
            f'dc.description any "{keyword}" '
            f'OR dc.subject any "{keyword}" '
            f'OR dc.title any "{keyword}"'
        )

        all_results: list[NormativoResult] = []
        start_record = 1

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
                break  # Network/API error; return what we have

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

        # Primary failed — try fallback
        logger.warning(
            f"LexML primary URL failed, trying fallback: {FALLBACK_SRU_URL}"
        )
        result = self._try_fetch(FALLBACK_SRU_URL, params)
        if result is not None:
            self._sru_url = FALLBACK_SRU_URL
            return result

        logger.error("LexML: both SRU endpoints failed")
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
                    import time
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
        result_id = ""

        if identifier and identifier.startswith("urn:lex:"):
            result_id = identifier
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

        # Fallback ID if no URN available
        if not result_id:
            hash_input = f"{title}|{date_raw}|{creator}"
            result_id = f"lexml:{hashlib.md5(hash_input.encode()).hexdigest()[:12]}"

        # Determine the best date: prefer URN date, then dc:date
        date_str = ""
        if urn_date:
            date_str = self._safe_date_format(urn_date)
        elif date_raw:
            date_str = self._safe_date_format(date_raw)

        # Build the nome (display name)
        nome = title if title else f"{tipo} nº {numero}" if tipo and numero else identifier

        return NormativoResult(
            id=result_id,
            nome=nome,
            tipo=tipo,
            numero=numero,
            data=date_str,
            orgao_emissor=creator,
            ementa=description,
            link=link,
            categoria="",       # Not available from LexML
            situacao="",        # Not available from LexML
            relevancia=0.5,     # Default; can be refined by the engine later
            source="lexml",
            found_by=keyword,
            raw_data={
                "dc_title": title,
                "dc_description": description,
                "dc_date": date_raw,
                "dc_creator": creator,
                "dc_type": dc_type,
                "dc_identifier": identifier,
            },
        )
```

### Error Handling Summary

| Scenario | Behavior |
|----------|----------|
| HTTP timeout (15s) | Log warning, skip keyword, continue |
| XML parse error | Log warning, skip keyword, return `([], 0)` |
| Primary SRU URL returns 404 | Try fallback URL; cache whichever works |
| Both SRU URLs fail | Log error, return empty list (no crash) |
| Connection error | Retry once after 3s, then skip |

### Unit Test Scenarios

1. **Happy path:** Mock SRU response with 3 records, verify 3 NormativoResult objects with correct field mapping.
2. **URN parsing:** Test all entries in `URN_TIPO_MAP` with sample URNs.
3. **Fallback URL:** Mock primary returning 404, verify fallback is used and cached.
4. **Pagination:** Mock response with `numberOfRecords=45`, verify 3 pages are fetched (20+20+5).
5. **Deduplication:** Two keywords find the same URN; verify single result with both keywords in `found_by`.
6. **Timeout:** Mock timeout exception; verify empty list returned, no crash.
7. **Malformed XML:** Pass invalid XML; verify empty list returned.
8. **Date parsing:** Test URN dates (`2018-08-14`), year-only (`2019`), and dc:date formats.

---

## 2.3 searchers/tcu_searcher.py — TCU Open Data API

### API Reference

| Property | Value |
|----------|-------|
| Base URL | `https://dados-abertos.apps.tcu.gov.br/api` |
| Protocol | REST/JSON |
| Authentication | None required |
| Rate limit | More sensitive than LexML; use 1.5s base delay |
| Maintenance window | Daily 20:00-21:00 BRT (returns 503) |

### Endpoints

#### 1. Acordaos (Court Decisions)

| Property | Value |
|----------|-------|
| Path | `/acordao/recupera-acordaos` |
| Method | GET |
| Pagination params | `inicio` (0-based offset), `quantidade` (page size) |

Response fields per item:

| Field | Type | Maps to |
|-------|------|---------|
| `numero` | str | NormativoResult.numero (with ano) |
| `ano` | str | Used in numero and nome |
| `colegiado` | str | "Plenario", "1a Camara", "2a Camara" |
| `relator` | str | Stored in raw_data |
| `ementa` | str | NormativoResult.ementa |
| `dataAta` or `dataSessao` | str | NormativoResult.data |

Field mapping to NormativoResult:

```python
NormativoResult(
    id=f"tcu:acordao:{numero}/{ano}",
    nome=f"Acordao {numero}/{ano} - TCU - {colegiado}",
    tipo="Acordao TCU",
    numero=f"{numero}/{ano}",
    data=safe_date_format(item.get("dataAta") or item.get("dataSessao", "")),
    orgao_emissor=f"TCU - {colegiado}",
    ementa=item.get("ementa", ""),
    link=build_acordao_link(numero, ano),
    source="tcu",
    found_by=keyword,
    raw_data=item,  # Preserve full API response
)
```

Acordao link construction:

```python
def _build_acordao_link(self, numero: str, ano: str) -> str:
    """Build search URL for a specific TCU acordao."""
    return (
        f"https://pesquisa.apps.tcu.gov.br/documento/acordao-completo/"
        f"*/NUMACORDAO%253A{numero}%2520ANOACORDAO%253A{ano}"
    )
```

#### 2. Atos Normativos (Normative Acts)

| Property | Value |
|----------|-------|
| Path | `/atonormativo/recupera-atos-normativos` |
| Method | GET |
| Pagination params | `inicio` (0-based offset), `quantidade` (page size) |

Response fields per item:

| Field | Type | Maps to |
|-------|------|---------|
| `tipo` | str | NormativoResult.tipo |
| `numero` | str | NormativoResult.numero |
| `dataPublicacao` or `data` | str | NormativoResult.data |
| `ementa` | str | NormativoResult.ementa |
| `link` or `url` | str | NormativoResult.link |

Field mapping to NormativoResult:

```python
NormativoResult(
    id=f"tcu:ato:{tipo_slug}:{numero}",
    nome=f"{tipo} TCU n. {numero}",
    tipo=tipo,                         # "Instrucao Normativa", "Resolucao", etc.
    numero=numero,
    data=safe_date_format(item.get("dataPublicacao") or item.get("data", "")),
    orgao_emissor="TCU",
    ementa=item.get("ementa", ""),
    link=item.get("link") or item.get("url", ""),
    source="tcu",
    found_by=keyword,
    raw_data=item,
)
```

### Search Strategy

The TCU API may not support full-text keyword search on all endpoints. Use this hybrid approach:

1. **Try server-side search first:** If the API supports a `palavraChave` or query parameter, use it.
2. **Fall back to client-side filtering:** Fetch paginated results and filter locally using `_normalize_text` for accent-insensitive keyword matching.
3. **Pagination limits:** Fetch at most 500 records per endpoint (25 pages of 20), then filter. This avoids excessive API calls while covering recent normativos.

Client-side matching logic:

```python
def _matches_keyword(self, text: str, keyword: str) -> bool:
    """Check if a keyword appears in the text (accent/case insensitive).

    Args:
        text: The text to search in (e.g., ementa).
        keyword: The keyword to look for.

    Returns:
        True if the keyword is found in the normalized text.
    """
    return self._normalize_text(keyword) in self._normalize_text(text)
```

### Complete Implementation

```python
"""
TCU (Tribunal de Contas da Uniao) searcher using the Open Data API.

Searches two endpoints:
1. Acordaos — court decisions with binding/recommendatory effect
2. Atos normativos — normative acts (instructions, resolutions, etc.)

API documentation: https://dados-abertos.apps.tcu.gov.br/
"""

import hashlib
import logging
import time
from typing import Optional

import requests

from models import NormativoResult
from searchers.base import BaseSearcher, ProgressCallback

logger = logging.getLogger(__name__)

API_BASE_URL = "https://dados-abertos.apps.tcu.gov.br/api"
ACORDAOS_PATH = "/acordao/recupera-acordaos"
ATOS_PATH = "/atonormativo/recupera-atos-normativos"

# Pagination settings
PAGE_SIZE = 20
MAX_PAGES = 25  # Max 500 records per endpoint
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3


class TCUSearcher(BaseSearcher):
    """Search TCU acordaos and atos normativos."""

    RATE_LIMIT_DELAY = 1.5   # TCU API is more sensitive
    RATE_LIMIT_JITTER = 0.5

    def source_name(self) -> str:
        return "TCU Dados Abertos"

    def search(
        self,
        keywords: list[str],
        max_results: int = 50,
        progress_callback: ProgressCallback = None,
    ) -> list[NormativoResult]:
        """Search TCU for acordaos and atos normativos matching keywords.

        Fetches records from both endpoints, then filters client-side
        for keyword matches in the ementa field.

        Args:
            keywords: Search terms.
            max_results: Maximum total results.
            progress_callback: Optional callback(current, total, message).

        Returns:
            Deduplicated list of NormativoResult objects.
        """
        # Total steps: 2 (one per endpoint) * keyword processing
        total_steps = 2
        results_by_id: dict[str, NormativoResult] = {}

        # --- Step 1: Acordaos ---
        if progress_callback:
            progress_callback(0, total_steps, "TCU: buscando acordaos")

        logger.info("TCU: fetching acordaos")
        acordao_items = self._fetch_all_pages(
            f"{API_BASE_URL}{ACORDAOS_PATH}"
        )
        logger.info(f"TCU: {len(acordao_items)} acordaos fetched, filtering by keywords")

        for item in acordao_items:
            if len(results_by_id) >= max_results:
                break
            ementa = item.get("ementa", "")
            matched_keywords = [kw for kw in keywords if self._matches_keyword(ementa, kw)]
            if matched_keywords:
                result = self._map_acordao(item, ", ".join(matched_keywords))
                if result.id not in results_by_id:
                    results_by_id[result.id] = result

        # --- Step 2: Atos Normativos ---
        if progress_callback:
            progress_callback(1, total_steps, "TCU: buscando atos normativos")

        logger.info("TCU: fetching atos normativos")
        atos_items = self._fetch_all_pages(
            f"{API_BASE_URL}{ATOS_PATH}"
        )
        logger.info(f"TCU: {len(atos_items)} atos normativos fetched, filtering by keywords")

        for item in atos_items:
            if len(results_by_id) >= max_results:
                break
            ementa = item.get("ementa", "")
            matched_keywords = [kw for kw in keywords if self._matches_keyword(ementa, kw)]
            if matched_keywords:
                result = self._map_ato_normativo(item, ", ".join(matched_keywords))
                if result.id not in results_by_id:
                    results_by_id[result.id] = result

        # Final callback
        if progress_callback:
            progress_callback(
                total_steps, total_steps,
                f"TCU: {len(results_by_id)} resultados encontrados"
            )

        logger.info(f"TCU: total {len(results_by_id)} resultados unicos")
        return list(results_by_id.values())

    def _matches_keyword(self, text: str, keyword: str) -> bool:
        """Check if keyword appears in text, accent/case insensitive."""
        return self._normalize_text(keyword) in self._normalize_text(text)

    def _fetch_all_pages(self, url: str) -> list[dict]:
        """Fetch all pages from a paginated TCU API endpoint.

        Stops at MAX_PAGES * PAGE_SIZE records to avoid excessive requests.

        Args:
            url: Full endpoint URL.

        Returns:
            List of raw JSON items. Empty list on total failure.
        """
        all_items: list[dict] = []
        offset = 0

        for page in range(MAX_PAGES):
            params = {
                "inicio": offset,
                "quantidade": PAGE_SIZE,
            }

            data = self._request_with_retry(url, params)
            if data is None:
                break  # API error; return what we have

            # The response may be a list directly or wrapped in an object.
            # Handle both cases.
            items = data if isinstance(data, list) else data.get("items", data.get("data", []))
            if not isinstance(items, list):
                logger.warning(f"TCU: unexpected response format from {url}")
                break

            all_items.extend(items)

            # If we got fewer items than PAGE_SIZE, no more pages
            if len(items) < PAGE_SIZE:
                break

            offset += PAGE_SIZE
            self._rate_limit()

        return all_items

    def _request_with_retry(
        self, url: str, params: dict
    ) -> Optional[dict | list]:
        """Send GET request with exponential backoff retry.

        Handles the TCU maintenance window (503 between 20:00-21:00 BRT)
        with a user-friendly log message.

        Args:
            url: Request URL.
            params: Query parameters.

        Returns:
            Parsed JSON (dict or list), or None on failure.
        """
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

                # Handle TCU maintenance window
                if response.status_code == 503:
                    logger.warning(
                        "TCU API retornou 503 (indisponivel). "
                        "A API do TCU fica indisponivel diariamente das 20h as 21h "
                        "(horario de Brasilia). Tente novamente mais tarde."
                    )
                    return None

                response.raise_for_status()
                return response.json()

            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    delay = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(
                        f"TCU API error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"TCU API failed after {MAX_RETRIES} attempts: {e}"
                    )
                    return None

        return None

    def _map_acordao(self, item: dict, found_by: str) -> NormativoResult:
        """Map a raw acordao JSON item to a NormativoResult.

        Args:
            item: Raw API response item.
            found_by: Comma-separated keywords that matched.

        Returns:
            NormativoResult with tipo="Acordao TCU".
        """
        numero = str(item.get("numero", ""))
        ano = str(item.get("ano", ""))
        colegiado = item.get("colegiado", "")

        # Parse date from dataAta or dataSessao
        date_raw = item.get("dataAta") or item.get("dataSessao", "")
        date_str = self._safe_date_format(date_raw)

        return NormativoResult(
            id=f"tcu:acordao:{numero}/{ano}",
            nome=f"Acordao {numero}/{ano} - TCU - {colegiado}",
            tipo="Acordao TCU",
            numero=f"{numero}/{ano}",
            data=date_str,
            orgao_emissor=f"TCU - {colegiado}",
            ementa=item.get("ementa", ""),
            link=self._build_acordao_link(numero, ano),
            categoria="",
            situacao="",
            relevancia=0.5,
            source="tcu",
            found_by=found_by,
            raw_data=item,
        )

    def _map_ato_normativo(self, item: dict, found_by: str) -> NormativoResult:
        """Map a raw ato normativo JSON item to a NormativoResult.

        Args:
            item: Raw API response item.
            found_by: Comma-separated keywords that matched.

        Returns:
            NormativoResult with the tipo from the API response.
        """
        tipo = item.get("tipo", "Ato Normativo")
        numero = str(item.get("numero", ""))

        # Stable ID: slugify the tipo for consistency
        tipo_slug = self._normalize_text(tipo).replace(" ", "_")

        # Parse date
        date_raw = item.get("dataPublicacao") or item.get("data", "")
        date_str = self._safe_date_format(date_raw)

        # Link: use provided link/url, or empty
        link = item.get("link") or item.get("url", "")

        return NormativoResult(
            id=f"tcu:ato:{tipo_slug}:{numero}",
            nome=f"{tipo} TCU n. {numero}",
            tipo=tipo,
            numero=numero,
            data=date_str,
            orgao_emissor="TCU",
            ementa=item.get("ementa", ""),
            link=link,
            categoria="",
            situacao="",
            relevancia=0.5,
            source="tcu",
            found_by=found_by,
            raw_data=item,
        )

    @staticmethod
    def _build_acordao_link(numero: str, ano: str) -> str:
        """Build the TCU search URL for a specific acordao.

        Args:
            numero: Acordao number.
            ano: Acordao year.

        Returns:
            URL to the TCU acordao search page.
        """
        return (
            f"https://pesquisa.apps.tcu.gov.br/documento/acordao-completo/"
            f"*/NUMACORDAO%253A{numero}%2520ANOACORDAO%253A{ano}"
        )
```

### Error Handling Summary

| Scenario | Behavior |
|----------|----------|
| HTTP timeout (15s) | Retry with exponential backoff (2s, 4s, 8s) |
| 503 (maintenance window) | Log user-friendly message about 20h-21h BRT, return None |
| Connection error | Retry up to 3 times with exponential backoff |
| Unexpected JSON format | Log warning, return empty list |
| Max pages reached (500 records) | Stop pagination, filter what was fetched |

### Unit Test Scenarios

1. **Acordao mapping:** Mock JSON item with `numero`, `ano`, `colegiado`, `ementa`; verify correct NormativoResult fields.
2. **Ato normativo mapping:** Mock JSON with `tipo="Instrucao Normativa"`, verify `nome` format.
3. **Client-side filtering:** Provide ementa text with accented characters; verify keyword matching works.
4. **Retry logic:** Mock first request raising ConnectionError, second succeeding; verify result returned.
5. **503 handling:** Mock 503 response; verify warning logged and None returned (no retry loop).
6. **Pagination:** Mock 3 pages of 20 items, last page with 5 items; verify all 45 items collected.
7. **Empty API response:** Mock empty list response; verify empty result, no crash.
8. **Deduplication across endpoints:** Same normativo in both acordaos and atos; verify dedup by ID.

---

## 2.4 searchers/google_searcher.py — Google Search for Standards and Frameworks

### Purpose

Search Google for international standards and frameworks (COBIT, ISO, COSO, ITIL) as well as Brazilian government publications. This complements the structured API searches by finding reference material not available in LexML or TCU.

### Library

Uses `googlesearch-python` (PyPI package `googlesearch-python`), which does not require an API key.

```python
from googlesearch import search as google_search
```

**Important:** This library scrapes Google Search results. It is subject to rate limiting and temporary blocks. Aggressive rate limiting (3s between queries) is essential.

### Site Restrictions

Queries are restricted to authoritative domains:

```python
SITE_RESTRICTION = (
    "site:isaca.org OR site:iso.org OR site:coso.org OR "
    "site:itsmf.org OR site:gov.br"
)
```

### Domain to Organization Mapping

```python
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
```

Domain extraction from URL:

```python
from urllib.parse import urlparse

def _extract_org(self, url: str) -> str:
    """Extract organization name from URL domain.

    Matches against DOMAIN_ORG_MAP using longest-suffix match.
    For example, tcu.gov.br matches before gov.br.

    Args:
        url: Full URL.

    Returns:
        Organization name string, or the domain itself if not mapped.
    """
    domain = urlparse(url).netloc.lower()
    # Try longest match first (e.g., tcu.gov.br before gov.br)
    for mapped_domain in sorted(DOMAIN_ORG_MAP.keys(), key=len, reverse=True):
        if domain.endswith(mapped_domain):
            return DOMAIN_ORG_MAP[mapped_domain]
    return domain
```

### Page Metadata Extraction

For each URL returned by Google, fetch the page to extract:
- **Title:** From the `<title>` HTML tag.
- **Description:** From the `<meta name="description">` tag, or the first 300 characters of visible page text.

```python
def _fetch_page_metadata(self, url: str) -> tuple[str, str]:
    """Fetch page title and description from a URL.

    Args:
        url: Page URL.

    Returns:
        Tuple of (title, description). Both may be empty on failure.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException:
        return "", ""

    soup = BeautifulSoup(response.content, "html.parser")

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

    # Fallback: first 300 chars of visible text
    if not description:
        body = soup.find("body")
        if body:
            text = body.get_text(separator=" ", strip=True)
            description = text[:300].strip()

    return title, description
```

### Complete Implementation

```python
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

import hashlib
import logging
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from googlesearch import search as google_search

from models import NormativoResult
from searchers.base import BaseSearcher, ProgressCallback

logger = logging.getLogger(__name__)

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

# Delay between Google queries (seconds) — aggressive to avoid blocks
GOOGLE_QUERY_DELAY = 3.0


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

                # Generate stable ID from URL
                url_hash = hashlib.md5(normalized_url.encode()).hexdigest()[:12]
                result_id = f"google:{url_hash}"

                # Extract organization from domain
                org = self._extract_org(url)

                results.append(NormativoResult(
                    id=result_id,
                    nome=title if title else url,
                    tipo="Framework/Padrao",
                    numero="",
                    data="",               # Google results rarely have a structured date
                    orgao_emissor=org,
                    ementa=description,
                    link=url,
                    categoria="",
                    situacao="",
                    relevancia=0.3,        # Lower default: these are supplementary results
                    source="google",
                    found_by=keyword,
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

    def _fetch_page_metadata(self, url: str) -> tuple[str, str]:
        """Fetch title and meta description from a URL.

        Args:
            url: Page URL to fetch.

        Returns:
            Tuple of (title, description). Both may be empty on failure.
        """
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
```

### Error Handling Summary

| Scenario | Behavior |
|----------|----------|
| Google blocks (HTTP 429 or exception) | Log warning, skip keyword, continue with next |
| Page fetch fails (timeout, 404, etc.) | Skip that URL, continue with next |
| BeautifulSoup parse error | Return empty title/description |
| `googlesearch-python` raises any exception | Catch broadly, log, continue |

### Rate Limiting Strategy

| Point | Delay | Rationale |
|-------|-------|-----------|
| Between Google queries | 3.0s + 0-1.0s jitter | Google is aggressive about blocking scrapers |
| Max keywords | 5 | Limits total Google queries per search session |
| Results per query | 10 | Small pages reduce detection risk |

### Unit Test Scenarios

1. **URL normalization:** Test `https://www.iso.org/standard/123/` and `http://iso.org/standard/123` produce same normalized form.
2. **Domain org mapping:** Test `tcu.gov.br` maps to "TCU" (not "Governo Federal"), `isaca.org` maps to "ISACA (COBIT)".
3. **Page metadata extraction:** Mock HTML with `<title>` and `<meta name="description">`; verify extraction.
4. **Metadata fallback:** Mock HTML without `<meta description>`; verify body text used.
5. **Google blocking:** Mock `google_search` raising exception; verify empty results, no crash.
6. **Keyword limiting:** Pass 10 keywords; verify only first 5 are queried.
7. **URL deduplication:** Same URL from two keywords; verify single result.

---

## 2.5 searchers/__init__.py

### Complete Implementation

```python
"""
Searchers package — search backends for Brazilian legislation and standards.

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
```

---

## 2.6 Dependencies

Add these to the project's `requirements.txt`:

| Package | Purpose | Already in project? |
|---------|---------|---------------------|
| `requests` | HTTP requests for all APIs | Yes (used in dou_clipping.py) |
| `beautifulsoup4` | HTML parsing for Google page metadata | Yes (used in dou_clipping.py) |
| `googlesearch-python` | Google search without API key | **No — must add** |

Install command:

```bash
pip install googlesearch-python
```

**Note:** `xml.etree.ElementTree` is part of the Python standard library (no install needed).

---

## 2.7 File Structure After Implementation

```
projeto-root/
    models.py                          # Phase 1 — NormativoResult dataclass
    searchers/
        __init__.py                    # Re-exports all searchers
        base.py                        # BaseSearcher ABC + utilities
        lexml_searcher.py              # LexML SRU API searcher
        tcu_searcher.py                # TCU Open Data API searcher
        google_searcher.py             # Google search for frameworks
    dou-clipping-app/                  # Existing app (not modified)
        dou_clipping.py
        app.py
        ...
```

---

## 2.8 Acceptance Criteria

### Functional Requirements

| # | Criterion | Verification |
|---|-----------|-------------|
| F1 | Each searcher can be instantiated with no arguments | `LexMLSearcher()`, `TCUSearcher()`, `GoogleSearcher()` all succeed |
| F2 | `search()` returns a `list[NormativoResult]` | Type check on return value |
| F3 | LexMLSearcher populates `nome`, `tipo`, `ementa`, `link` on results | Assert non-empty strings on returned objects |
| F4 | TCUSearcher returns both acordaos and atos normativos | Check for `tipo="Acordao TCU"` and other tipos in results |
| F5 | GoogleSearcher returns results without requiring an API key | No API key in code or env vars; `source="google"` on all results |
| F6 | All results have `source` field set correctly | `"lexml"`, `"tcu"`, or `"google"` |
| F7 | All results have `found_by` field populated | Non-empty string matching a search keyword |
| F8 | Deduplication works within each searcher | Same normativo from two keywords appears once, with both keywords in `found_by` |

### Non-Functional Requirements

| # | Criterion | Verification |
|---|-----------|-------------|
| N1 | Rate limits are respected | Measure time between consecutive API calls; assert >= RATE_LIMIT_DELAY |
| N2 | No searcher propagates exceptions | Call `search()` with invalid keywords, mock API failures; verify no exception raised |
| N3 | Partial results returned on failure | Mock API timeout after 2 keywords (of 5); verify results from first 2 keywords returned |
| N4 | `progress_callback` is called correctly | Mock callback; verify called with incrementing `current`, correct `total`, non-empty `message` |
| N5 | `max_results` is respected | Request `max_results=5` with many keywords; verify <= 5 results returned |

### Integration Smoke Test

```python
"""
Smoke test — run each searcher with a single keyword and verify output shape.
Requires network access. Not for CI; run manually.
"""
from searchers import LexMLSearcher, TCUSearcher, GoogleSearcher

def test_lexml_smoke():
    searcher = LexMLSearcher()
    results = searcher.search(["governanca de TI"], max_results=5)
    assert isinstance(results, list)
    for r in results:
        assert r.source == "lexml"
        assert r.nome  # non-empty
        assert r.link  # non-empty
    print(f"LexML: {len(results)} results OK")

def test_tcu_smoke():
    searcher = TCUSearcher()
    results = searcher.search(["governanca"], max_results=5)
    assert isinstance(results, list)
    for r in results:
        assert r.source == "tcu"
    print(f"TCU: {len(results)} results OK")

def test_google_smoke():
    searcher = GoogleSearcher()
    results = searcher.search(["COBIT"], max_results=3)
    assert isinstance(results, list)
    for r in results:
        assert r.source == "google"
    print(f"Google: {len(results)} results OK")

if __name__ == "__main__":
    test_lexml_smoke()
    test_tcu_smoke()
    test_google_smoke()
    print("All smoke tests passed")
```

---

## 2.9 Implementation Notes and Caveats

### TCU API Response Format Uncertainty

The TCU Open Data API documentation is sparse. The exact response structure (whether items are in a root list, or nested under `"items"` or `"data"`) may vary by endpoint. The implementation handles all three cases:

```python
items = data if isinstance(data, list) else data.get("items", data.get("data", []))
```

At implementation time, run a quick manual test against each endpoint to confirm the actual shape:

```bash
curl -s "https://dados-abertos.apps.tcu.gov.br/api/acordao/recupera-acordaos?inicio=0&quantidade=2" | python -m json.tool | head -30
curl -s "https://dados-abertos.apps.tcu.gov.br/api/atonormativo/recupera-atos-normativos?inicio=0&quantidade=2" | python -m json.tool | head -30
```

Adjust field names in `_map_acordao` and `_map_ato_normativo` if the actual response differs from what is documented here.

### LexML XML Nesting Variations

LexML SRU responses may nest Dublin Core elements differently depending on the record type. Some records have DC elements as direct children of `<srw:recordData>`, others wrap them in an intermediate element (e.g., `<srw_dc:dc>`). The implementation uses both direct and recursive search:

```python
el = record_data.find(f"dc:{tag}", NAMESPACES)
if el is None:
    el = record_data.find(f".//dc:{tag}", NAMESPACES)
```

### Google Search Reliability

The `googlesearch-python` library works by scraping Google Search results. This is inherently fragile:
- Google may block the IP after many queries.
- The library may break if Google changes their HTML structure.
- Results may vary by geographic location.

This searcher should always be treated as "best effort". The application must function correctly even if GoogleSearcher returns zero results. The LexML and TCU searchers are the primary data sources.

### Character Encoding

All three APIs may return text with Brazilian Portuguese characters (accents, cedilla, etc.). The `requests` library handles encoding automatically via `response.encoding`. The XML parser (`ET.fromstring`) handles UTF-8 by default. No manual encoding conversion should be needed, but if encoding issues appear, check the `Content-Type` response header.

### Logging Configuration

All searchers use Python's standard `logging` module at the module level:

```python
logger = logging.getLogger(__name__)
```

This produces loggers named `searchers.lexml_searcher`, `searchers.tcu_searcher`, and `searchers.google_searcher`. The calling application (Phase 3 or the Streamlit UI) is responsible for configuring the logging level and handlers.

Recommended log levels used in the searchers:
- `DEBUG`: Rate limit sleep durations, individual URL fetches
- `INFO`: Keyword progress, result counts, pagination status
- `WARNING`: Retries, fallback URLs, skipped keywords
- `ERROR`: Total API failure after all retries
