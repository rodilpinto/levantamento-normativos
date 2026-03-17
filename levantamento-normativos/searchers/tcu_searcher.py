"""
TCU (Tribunal de Contas da Uniao) searcher using the Open Data API.

Searches two endpoints:
1. Acordaos -- court decisions with binding/recommendatory effect
2. Atos normativos -- normative acts (instructions, resolutions, etc.)

API documentation: https://dados-abertos.apps.tcu.gov.br/
"""

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
        # Total steps: 2 (one per endpoint)
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
            nome=f"Acordao {numero}/{ano} - TCU - {colegiado}",
            tipo="Acordao TCU",
            numero=f"{numero}/{ano}",
            data=date_str,
            orgao_emissor=f"TCU - {colegiado}",
            ementa=item.get("ementa", ""),
            link=self._build_acordao_link(numero, ano),
            source="tcu",
            found_by=found_by,
            relevancia=0.5,
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

        # Parse date
        date_raw = item.get("dataPublicacao") or item.get("data", "")
        date_str = self._safe_date_format(date_raw)

        # Link: use provided link/url, or empty
        link = item.get("link") or item.get("url", "")

        return NormativoResult(
            nome=f"{tipo} TCU n. {numero}",
            tipo=tipo,
            numero=numero,
            data=date_str,
            orgao_emissor="TCU",
            ementa=item.get("ementa", ""),
            link=link,
            source="tcu",
            found_by=found_by,
            relevancia=0.5,
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
