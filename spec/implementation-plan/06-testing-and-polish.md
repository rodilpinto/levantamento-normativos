# Phase 6: Testing, Error Handling & Polish

This phase covers the defensive engineering layer that transforms the prototype into a production-ready tool. It specifies error handling requirements for every module, defines all user-facing messages, provides a manual testing script, establishes performance baselines, and lists the final delivery checklist.

---

## 6.1 Error Handling Checklist

Every module must handle failure scenarios gracefully. The guiding principles are:

- **Never crash the Streamlit app.** All exceptions must be caught at the appropriate level and translated into user-friendly messages.
- **Partial results are better than no results.** If one source fails, show results from the others with a warning.
- **Log errors for debugging.** Use Python's `logging` module at WARNING or ERROR level for all caught exceptions.
- **Fail fast on configuration errors.** Missing required files or invalid project structure should produce clear error messages at startup.

### 6.1.1 Searchers (lexml_searcher.py, tcu_searcher.py, google_searcher.py)

Each searcher inherits from `BaseSearcher` and must handle network failures identically:

| Scenario | Detection | Response | User Impact |
|----------|-----------|----------|-------------|
| HTTP timeout (15s) | `requests.Timeout` exception | Log WARNING with URL and keyword. Skip this keyword. Continue with remaining keywords. | Partial results. Progress bar advances past failed keyword. |
| HTTP 4xx client error | `response.status_code` in 400-499 | Log WARNING with status code and URL. No retry (client errors are deterministic). Skip keyword. | Partial results. |
| HTTP 5xx server error | `response.status_code` in 500-599 | Log WARNING. Retry ONCE after 3-second delay. If retry also fails, skip keyword. | Slight delay on transient errors. Partial results on persistent errors. |
| Connection refused | `requests.ConnectionError` | Log ERROR. Abort this entire source (not just keyword). Return whatever results were collected so far. | Warning banner in UI. Results from other sources still shown. |
| DNS resolution failure | `requests.ConnectionError` (subtype) | Same as connection refused. | Same as above. |
| Invalid response body (not JSON/XML) | `json.JSONDecodeError`, `xml.etree.ElementTree.ParseError` | Log ERROR with first 200 chars of response body. Skip keyword. | Partial results. |
| Empty results from API | Empty list/no matches in response | Return empty list. This is not an error. | No special handling needed. |
| SSL certificate error | `requests.exceptions.SSLError` | Log ERROR. Abort source. Do NOT disable SSL verification. | Warning banner. Results from other sources. |

**Implementation pattern for all searchers:**

```python
import logging
import time
import requests

logger = logging.getLogger(__name__)

# Maximum time to wait for a single HTTP response
_REQUEST_TIMEOUT_SECONDS = 15

# Delay before retrying a failed 5xx request
_RETRY_DELAY_SECONDS = 3


def _safe_request(
    url: str,
    params: dict = None,
    keyword: str = "",
) -> requests.Response | None:
    """Execute an HTTP GET with timeout, retry, and error handling.

    Args:
        url: The URL to request.
        params: Optional query parameters.
        keyword: The keyword being searched (for log context).

    Returns:
        Response object if successful, None if the request failed
        after all retry attempts.
    """
    for attempt in range(2):  # At most 2 attempts (original + 1 retry)
        try:
            response = requests.get(
                url,
                params=params,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 200:
                return response

            if 400 <= response.status_code < 500:
                logger.warning(
                    "HTTP %d for keyword '%s' at %s. Skipping (client error).",
                    response.status_code,
                    keyword,
                    url,
                )
                return None

            if 500 <= response.status_code < 600:
                if attempt == 0:
                    logger.warning(
                        "HTTP %d for keyword '%s' at %s. Retrying in %ds...",
                        response.status_code,
                        keyword,
                        url,
                        _RETRY_DELAY_SECONDS,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                else:
                    logger.warning(
                        "HTTP %d for keyword '%s' at %s. Retry also failed.",
                        response.status_code,
                        keyword,
                        url,
                    )
                    return None

            # Unexpected status code
            logger.warning(
                "Unexpected HTTP %d for keyword '%s' at %s.",
                response.status_code,
                keyword,
                url,
            )
            return None

        except requests.Timeout:
            logger.warning(
                "Timeout (%ds) for keyword '%s' at %s.",
                _REQUEST_TIMEOUT_SECONDS,
                keyword,
                url,
            )
            return None

        except requests.ConnectionError as exc:
            logger.error(
                "Connection error for keyword '%s' at %s: %s",
                keyword,
                url,
                str(exc)[:200],
            )
            raise  # Re-raise to abort this source entirely

        except requests.exceptions.SSLError as exc:
            logger.error(
                "SSL error for keyword '%s' at %s: %s",
                keyword,
                url,
                str(exc)[:200],
            )
            raise  # Re-raise to abort this source entirely

    return None
```

The `search()` method in each searcher wraps the keyword loop in a try/except for `ConnectionError` and `SSLError`:

```python
def search(self, keywords, max_results=50, progress_callback=None):
    results = []
    try:
        for i, keyword in enumerate(keywords):
            if progress_callback:
                progress_callback(i, len(keywords), f"Buscando '{keyword}'...")

            response = _safe_request(self._build_url(keyword), keyword=keyword)
            if response is None:
                continue

            try:
                parsed = self._parse_response(response)
                results.extend(parsed)
            except (json.JSONDecodeError, ET.ParseError) as exc:
                logger.error(
                    "Failed to parse response for '%s': %s. Body: %.200s",
                    keyword,
                    exc,
                    response.text,
                )
                continue

            if len(results) >= max_results:
                results = results[:max_results]
                break

    except (requests.ConnectionError, requests.exceptions.SSLError):
        # Source is unreachable. Return whatever we have so far.
        pass

    if progress_callback:
        progress_callback(len(keywords), len(keywords), "Concluido.")

    return results
```

### 6.1.2 LLM Client (gemini_client.py)

The Gemini client must be fully optional. Every public function must work without an API key.

| Scenario | Detection | Response | Fallback Value |
|----------|-----------|----------|----------------|
| No API key configured | `GEMINI_API_KEY` not in secrets or empty | Return fallback immediately. No API call attempted. No warning logged (this is a normal operating mode). | Per-function defaults (see below) |
| Rate limit (HTTP 429) | `response.status_code == 429` or API exception with "RESOURCE_EXHAUSTED" | Log WARNING. Return fallback values for current batch. Do not retry (let user try again later). | Per-function defaults |
| Invalid response JSON | `json.JSONDecodeError` when parsing LLM output | Log WARNING with raw response text (truncated to 500 chars). Return fallback values. | Per-function defaults |
| API timeout (30s) | `google.api_core.exceptions.DeadlineExceeded` or `requests.Timeout` | Log WARNING. Return fallback values. | Per-function defaults |
| API quota exceeded | API exception with "QUOTA_EXCEEDED" | Log WARNING. Disable LLM for remainder of session by setting internal flag. Return fallback values. | Per-function defaults |
| Malformed LLM output (valid JSON but wrong structure) | KeyError, IndexError when extracting fields | Log WARNING with the actual structure received. Return fallback values. | Per-function defaults |

**Fallback values by function:**

| Function | Fallback Return Value |
|----------|----------------------|
| `expand_topic_to_keywords(topic)` | `[topic]` (return the topic itself as a single-item keyword list) |
| `score_relevance(result, topic, keywords)` | `_keyword_match_score(result, keywords)` (simple keyword overlap ratio) |
| `score_relevance_batch(results, topic, keywords)` | Apply `_keyword_match_score` to each result |
| `categorize(result)` | `"Nao categorizado"` |
| `categorize_batch(results)` | Set all categories to `"Nao categorizado"` |

**Keyword-match fallback scorer:**

```python
def _keyword_match_score(result: NormativoResult, keywords: list[str]) -> float:
    """Calculate relevance score based on keyword overlap.

    Used as fallback when the LLM is unavailable. Computes what fraction
    of the provided keywords appear (case-insensitive, accent-insensitive)
    in the result's ementa and nome fields combined.

    Args:
        result: The normativo to score.
        keywords: List of search keywords.

    Returns:
        Float between 0.0 and 1.0 representing keyword match ratio.
    """
    if not keywords:
        return 0.0

    searchable_text = _normalize(
        (result.nome or "") + " " + (result.ementa or "")
    )

    matches = sum(
        1
        for kw in keywords
        if _normalize(kw) in searchable_text
    )

    return matches / len(keywords)
```

**`is_available()` guard pattern:**

```python
class GeminiClient:
    def __init__(self):
        self._api_key = _load_api_key()
        self._disabled = False  # Set to True on quota exhaustion

    def is_available(self) -> bool:
        """Check if the Gemini client can make API calls.

        Returns:
            True if API key is configured and client has not been
            disabled due to quota exhaustion.
        """
        return bool(self._api_key) and not self._disabled
```

### 6.1.3 Excel Export (excel_export.py)

| Scenario | Detection | Response |
|----------|-----------|----------|
| Empty results list | `len(results) == 0` | Raise `ValueError`. Caller (Step 5 UI) must validate before calling. |
| Very long ementa (>5000 chars) | `len(ementa) > 5000` | Truncate to 5000 chars + "..." in the Excel cell. Log INFO. |
| Special characters in ementa | N/A | openpyxl handles UTF-8 natively. No special handling needed. |
| openpyxl not installed | `ImportError` | Fatal error at import time. Covered by `requirements.txt` validation. |
| Disk full / write error | `IOError` when saving to BytesIO | Should not happen with BytesIO (in-memory). If it does, let exception propagate to Streamlit error handler. |
| Excel cell character limit (32,767 chars) | `len(value) > 32767` | The 5000-char truncation prevents this. Defensive check anyway. |

### 6.1.4 Deduplicator (deduplicator.py)

| Scenario | Detection | Response |
|----------|-----------|----------|
| Empty results list | `len(results) == 0` | Return empty list immediately. |
| Result with all-None fields | NormativoResult with empty strings | Skip tipo+numero matching (key would be `("", "")`). Skip fuzzy matching if ementa is empty. Include in output without merging. |
| Very large result set (>1000) | `len(results) > _FUZZY_MAX_ITEMS` | Skip fuzzy ementa matching to avoid O(n^2) performance. Rely on ID and tipo+numero matching only. Log INFO explaining why fuzzy was skipped. |
| SequenceMatcher error | Should never happen with string inputs | Defensive try/except around ratio() call. Log WARNING and skip fuzzy check for that pair. |

### 6.1.5 Streamlit UI (app.py)

| Scenario | Detection | Response |
|----------|-----------|----------|
| No sources selected in Step 3 | All checkboxes unchecked | Disable "Iniciar Busca" button. |
| Empty keyword list | `len(keywords) == 0` | Disable "Proximo" in Step 1. Disable "Iniciar Busca" in Step 3. |
| Search returns 0 results | `len(results) == 0` after search | Show st.info: "Nenhum normativo encontrado para as palavras-chave informadas." Offer button to go back to Step 3. |
| No items selected in Step 4 | `_count_selected() == 0` | Disable "Proximo" button. Show caption explaining at least one item must be selected. |
| No items selected in Step 5 | `len(selected) == 0` | Show st.warning. Offer button to go back to Step 4. |
| Browser refresh / new tab | `st.session_state` is empty | Wizard resets to Step 1. This is expected behavior. |
| Concurrent users | Multiple browser sessions | Each session has independent `st.session_state`. No shared state issues. |

---

## 6.2 User-Facing Messages

All messages displayed to the user must be in Brazilian Portuguese. The following is the complete catalog of user-facing strings organized by context.

### 6.2.1 Search Progress Messages

These appear inside the `st.status` container during search execution:

```python
MESSAGES = {
    # Search lifecycle
    "search_starting": "Buscando normativos...",
    "search_keyword": "Buscando '{keyword}'...",  # .format(keyword=kw)
    "search_source_done": "{source} -- Concluido. {count} resultados.",
    "search_dedup": "Removendo duplicatas...",
    "search_scoring": "Avaliando relevancia com IA...",
    "search_categorizing": "Categorizando normativos...",
    "search_complete": "Busca concluida -- {count} normativos encontrados",

    # Source-specific errors
    "error_lexml": "Erro ao acessar LexML. Resultados podem estar incompletos.",
    "error_tcu": (
        "Erro ao acessar TCU. Servico pode estar indisponivel "
        "(manutencao diaria 20h-21h)."
    ),
    "error_google": "Erro ao acessar Google Search. Resultados podem estar incompletos.",
    "error_source_generic": "{source} -- Erro: {message}. Continuando com demais fontes...",

    # LLM messages
    "llm_generating_keywords": "Gerando palavras-chave com IA...",
    "llm_unavailable": (
        "IA nao disponivel. Relevancia calculada por correspondencia "
        "de palavras-chave."
    ),
    "llm_keywords_generated": "IA gerou {count} palavras-chave a partir do tema.",
    "llm_no_key": (
        "Chave da API Gemini nao configurada. O modo de tema requer a IA. "
        "Use o modo manual ou configure GEMINI_API_KEY em .streamlit/secrets.toml"
    ),

    # Results
    "no_results": (
        "Nenhum normativo encontrado para as palavras-chave informadas. "
        "Tente ampliar as palavras-chave ou selecionar fontes adicionais."
    ),
    "no_results_filter": (
        "Nenhum normativo corresponde aos filtros selecionados. "
        "Ajuste os filtros acima."
    ),
    "no_selection": (
        "Nenhum normativo selecionado. Volte ao passo anterior "
        "e selecione pelo menos um item."
    ),

    # Export
    "excel_generating": "Gerando arquivo Excel...",
    "excel_success": "Excel gerado com sucesso!",
    "excel_download_label": "Baixar Excel (.xlsx)",

    # Empty keyword warning
    "empty_keywords": "Insira pelo menos uma palavra-chave para continuar.",
}
```

### 6.2.2 Logging Messages (Internal, Not User-Facing)

These are written to the Python logger and are in English for consistency with standard logging practices:

```python
# Searcher logs
"HTTP %d for keyword '%s' at %s. Skipping."
"HTTP %d for keyword '%s' at %s. Retrying in %ds..."
"Timeout (%ds) for keyword '%s' at %s."
"Connection error for keyword '%s' at %s: %s"
"SSL error for keyword '%s' at %s: %s"
"Failed to parse response for '%s': %s. Body: %.200s"

# LLM logs
"Gemini API rate limited (429). Returning fallback values."
"Gemini API timeout. Returning fallback values."
"Gemini response is not valid JSON: %.500s"
"Gemini quota exceeded. Disabling LLM for this session."
"Gemini response has unexpected structure: %s"

# Deduplicator logs
"Fuzzy matching skipped: result set size (%d) exceeds threshold (%d)."
"Merged duplicate: '%s' into '%s' (strategy: %s, ratio: %.2f)"
```

---

## 6.3 Logging Configuration

Configure logging in the main `app.py` entrypoint:

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Reduce noise from third-party libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
```

---

## 6.4 Manual Testing Script

These manual test scenarios validate the complete application end-to-end. Execute them in order. Each test builds on the assumption that the previous tests passed.

### Test 1: Basic Flow with LLM (Happy Path)

**Preconditions:** GEMINI_API_KEY is configured in `.streamlit/secrets.toml`. Internet connection is available.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1.1 | Start app: `streamlit run levantamento-normativos/app.py` | App loads. Step 1 is displayed. Sidebar shows step indicator with step 1 highlighted. |
| 1.2 | Select "Descrever tema" radio button | Topic text input appears. |
| 1.3 | Enter "Governanca de TI no setor publico" in topic field | "Proximo" button becomes enabled. |
| 1.4 | Click "Proximo" | App advances to Step 2. Spinner shows "Gerando palavras-chave com IA..." |
| 1.5 | Wait for LLM response | Success banner shows "IA gerou N palavras-chave". Text area populated with 15-30 keywords. Metric shows count. |
| 1.6 | Edit one keyword (change text in text area) | Metric updates to reflect current keyword count. |
| 1.7 | Click "Proximo" | App advances to Step 3. Keywords stored in session state. |
| 1.8 | Verify all three source checkboxes are checked by default | LexML, TCU, and Google checkboxes are all checked. |
| 1.9 | Click "Iniciar Busca" | Progress bar appears. Status text shows current source and keyword. |
| 1.10 | Wait for search to complete | Status changes to "Busca concluida -- N normativos encontrados". App auto-advances to Step 4. |
| 1.11 | Verify results display | Result cards appear with tipo badges (colored), ementas (truncated), metadata, and checkboxes (all checked by default). |
| 1.12 | Filter by tipo: select "Lei" from dropdown | Only results with tipo "Lei" are shown. Other results hidden but checkboxes preserved. |
| 1.13 | Sort by "Relevancia (descendente)" | Results reorder by relevance score, highest first. |
| 1.14 | Click "Ver detalhes" on one result | Expander opens showing full ementa, link, categoria, situacao, found_by. |
| 1.15 | Deselect 2 items by unchecking their checkboxes | Selected count decreases by 2. |
| 1.16 | Click "Proximo" | App advances to Step 5. |
| 1.17 | Verify export summary | Metric shows correct count. Breakdown by tipo and source is accurate. |
| 1.18 | Click "Gerar Excel" | Spinner shows. Success message appears. "Baixar Excel" button appears. |
| 1.19 | Click "Baixar Excel" | Browser downloads .xlsx file. |
| 1.20 | Open downloaded file in Excel or LibreOffice | Title row shows topic. Header row is green with white text. All 10 columns populated. Hyperlinks clickable. Relevancia shows as percentage with conditional coloring. Freeze panes active. Auto-filter enabled. |

### Test 2: Manual Keywords Mode (No LLM Required)

**Preconditions:** Remove or comment out GEMINI_API_KEY in `.streamlit/secrets.toml`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 2.1 | Start app (or refresh browser) | Step 1 displayed. |
| 2.2 | Select "Inserir palavras-chave manualmente" | Text area appears with placeholder. |
| 2.3 | Enter keywords (one per line): "governanca de TI", "LGPD", "seguranca da informacao" | Caption shows "3 palavras-chave inseridas". "Proximo" enabled. |
| 2.4 | Click "Proximo" | App skips Step 2 entirely, goes directly to Step 3. |
| 2.5 | Click "Iniciar Busca" | Search executes. Relevancia uses keyword-match fallback (no LLM). |
| 2.6 | Complete remaining steps (4 and 5) | Same as Test 1 steps 1.11-1.20. Relevancia scores may be lower (keyword match is less precise than LLM). |

### Test 3: Partial API Failure

**Preconditions:** GEMINI_API_KEY configured. Internet available.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 3.1 | Start app. Enter topic. Generate keywords. | Steps 1-2 complete normally. |
| 3.2 | In Step 3, uncheck TCU and Google. Keep only LexML. | Only LexML checkbox checked. |
| 3.3 | Click "Iniciar Busca" | Search runs against LexML only. Progress bar advances. |
| 3.4 | Verify results | Results appear from LexML source only. |
| 3.5 | Go back to Step 3. Check all sources. Click "Iniciar Busca" again. | Search runs against all three sources. |
| 3.6 | (If possible) Simulate network failure during search by disconnecting Wi-Fi mid-search | App shows partial results with warning message. App does not crash. |

### Test 4: Edge Cases

| Test Case | Action | Expected Result |
|-----------|--------|-----------------|
| 4.1 Empty topic | Clear topic field in Step 1 | "Proximo" button is disabled. |
| 4.2 Empty keywords | Clear text area in manual mode | "Proximo" button is disabled. Caption shows "0 palavras-chave". |
| 4.3 Single keyword | Enter one keyword in manual mode | Search works normally with one keyword. |
| 4.4 Many keywords (50+) | Enter 50 keywords in manual mode | Search works (may be slow). Progress bar advances per keyword. |
| 4.5 Zero search results | Use a very obscure topic unlikely to match anything (e.g., "xyzzy12345 norma") | Step 4 shows info message: "Nenhum normativo encontrado..." with back button. |
| 4.6 Browser refresh | Refresh browser at any step | App resets to Step 1. All session state cleared. |
| 4.7 Select all / Deselect all | Click "Selecionar todos" then "Desmarcar todos" | All checkboxes toggle correctly. Count updates. |
| 4.8 Filter then navigate | Apply tipo filter in Step 4, go to Step 5, come back | Filter state preserved. Checkbox selections preserved. |

### Test 5: Excel Validation

Open the generated Excel file and verify each of the following:

| Check | Expected |
|-------|----------|
| 5.1 File opens without errors | No corruption warnings. |
| 5.2 Row 1 (title) | Merged across all columns. Shows "Levantamento de Normativos: {topic}". Font size 14, bold, green text on light green background. |
| 5.3 Row 2 (headers) | Green (#4A8C4A) background. White bold text. All 10 column headers present. |
| 5.4 Column A (Nome) | Width ~50. Text values present. Bold font. |
| 5.5 Column D (Data) | Dates in DD/MM/YYYY format. |
| 5.6 Column F (Ementa) | Text wrapped. Row height expanded for long ementas. |
| 5.7 Column G (Link) | Clickable hyperlinks. Green text with underline. |
| 5.8 Column J (Relevancia) | Shows as percentage (e.g., "85%"). Green fill >= 70%. Yellow fill >= 40%. No fill < 40%. |
| 5.9 Freeze panes | Scroll down. Rows 1-2 remain visible. |
| 5.10 Auto-filter | Click dropdown arrow on any header. Filter options appear. |
| 5.11 UTF-8 characters | Portuguese accents (a, e, i, o, u, c) render correctly. |

---

## 6.5 Performance Expectations

These baselines assume a standard broadband connection (10+ Mbps) and a machine with 4+ GB RAM. Times are approximate and depend heavily on API response times.

| Operation | Expected Duration | Primary Bottleneck | Notes |
|-----------|------------------|-------------------|-------|
| LLM keyword expansion | 2-5 seconds | Gemini API network latency | Single API call. Response parsing is instant. |
| LexML search (20 keywords) | 30-60 seconds | Intentional rate limiting (1 request/second between keywords) | ~20 HTTP requests. Each takes 1-3s including rate limit delay. |
| TCU search (20 keywords) | 10-20 seconds | API response time | Fewer requests (API supports broader queries). No rate limiting needed. |
| Google search (5 keywords) | 20-30 seconds | Intentional rate limiting (3 seconds between queries) | Limited to ~5 queries to avoid abuse. 3s delay between each. |
| LLM relevance scoring (100 results) | 10-20 seconds | Gemini API latency x batch count | Results sent in batches of 20. ~5 API calls. |
| LLM categorization (100 results) | 5-10 seconds | Gemini API latency x batch count | Can be combined with scoring in same prompt to halve API calls. |
| Deduplication (300 results) | < 1 second | CPU (fuzzy matching) | O(n^2) but n is small. SequenceMatcher is C-optimized. |
| Excel generation (100 results) | < 2 seconds | CPU (openpyxl workbook construction) | In-memory operation. No disk I/O until download. |
| **Total end-to-end (typical)** | **~2-3 minutes** | **Network I/O** | Dominated by searcher rate limiting. |
| **Total end-to-end (worst case)** | **~5 minutes** | **Network I/O** | All sources slow. LLM retries. 50 keywords. |

### Optimization Opportunities (Future)

If performance becomes a concern, these optimizations can be applied in priority order:

1. **Parallel source queries:** Run LexML, TCU, and Google searches concurrently using `concurrent.futures.ThreadPoolExecutor`. Could reduce total time by ~40% since network I/O is the bottleneck. Requires careful progress bar coordination.

2. **Combined LLM prompts:** Send relevance scoring and categorization in a single prompt per batch instead of separate calls. Halves LLM API call count.

3. **Result caching:** Cache search results in `st.session_state` keyed by keyword hash. If user re-searches with same keywords, return cached results instantly. Clear cache on keyword edit.

4. **Streaming LLM responses:** Use Gemini streaming API to show partial keyword lists as they are generated, improving perceived latency in Step 2.

---

## 6.6 secrets.toml Configuration

### File Location

```
levantamento-normativos/
  .streamlit/
    secrets.toml          # Actual secrets (git-ignored)
    secrets.toml.example  # Template for developers (committed)
```

### secrets.toml.example Content

```toml
# ============================================================================
# Levantamento de Normativos -- Configuracao
# ============================================================================
# Copie este arquivo para secrets.toml e preencha os valores.
# O arquivo secrets.toml NAO deve ser commitado no repositorio.
# ============================================================================

# ---------------------------------------------------------------------------
# Gemini API (obrigatorio para funcionalidades de IA)
# ---------------------------------------------------------------------------
# Usado para: expansao de palavras-chave, avaliacao de relevancia,
# categorizacao automatica de normativos.
#
# Obtenha sua chave gratuita em: https://aistudio.google.com/apikey
# Modelo utilizado: gemini-2.0-flash (gratuito, 15 RPM, 1M tokens/min)
#
# Se nao configurado, o app funciona em modo degradado:
# - Palavras-chave devem ser inseridas manualmente
# - Relevancia calculada por correspondencia textual simples
# - Categorizacao nao disponivel
GEMINI_API_KEY = "your-gemini-flash-api-key-here"

# ---------------------------------------------------------------------------
# Google Custom Search (opcional)
# ---------------------------------------------------------------------------
# Usado para: busca de frameworks e padroes internacionais (COBIT, ISO, etc.)
#
# Se nao configurado, a fonte Google Search fica indisponivel.
# As fontes LexML e TCU continuam funcionando normalmente.
#
# Obtenha em: https://console.cloud.google.com/apis/credentials
# GOOGLE_API_KEY = ""
# GOOGLE_CSE_ID = ""
```

### .gitignore Entry

Ensure the following line is present in the project `.gitignore`:

```
.streamlit/secrets.toml
```

---

## 6.7 Final Delivery Checklist

This checklist must be completed before the tool is delivered to the NUATI team. Each item has a verification method.

### Code Completeness

- [ ] All files created per directory structure defined in Phase 1
  - Verify: `ls -la levantamento-normativos/` shows all expected files
- [ ] `requirements.txt` lists all dependencies with pinned versions
  - Verify: `pip install -r requirements.txt` succeeds in a clean virtualenv
- [ ] `secrets.toml.example` present with documentation
  - Verify: file exists at `.streamlit/secrets.toml.example`

### Application Startup

- [ ] `streamlit run levantamento-normativos/app.py` starts without errors
  - Verify: no tracebacks in terminal output
- [ ] App loads in browser at `http://localhost:8501`
  - Verify: Step 1 renders correctly

### Wizard Flow

- [ ] All 5 wizard steps render correctly
  - Verify: navigate forward through all steps
- [ ] Navigation works forward and backward
  - Verify: click "Anterior" on each step
- [ ] Sidebar step indicator updates correctly
  - Verify: current step is highlighted on each navigation

### Search Functionality

- [ ] Search works with at least LexML source
  - Verify: search with 3 keywords, LexML only, returns results
- [ ] Search works with all three sources enabled
  - Verify: results include items from multiple sources
- [ ] Progress bar advances smoothly during search
  - Verify: no jumps or stalls in progress bar
- [ ] Deduplication reduces result count
  - Verify: status message shows dedup step; result count may decrease

### LLM Integration

- [ ] Keyword expansion works with valid Gemini key
  - Verify: Step 2 shows generated keywords
- [ ] Relevance scoring produces reasonable scores
  - Verify: highly relevant normativos score > 0.7
- [ ] App works in degraded mode without Gemini API key
  - Verify: remove key, restart app, complete full flow with manual keywords

### Result Review (Step 4)

- [ ] Tipo filter works correctly
  - Verify: select a tipo, only matching results shown
- [ ] Source filter works correctly
  - Verify: select a source, only matching results shown
- [ ] Relevancia slider filters correctly
  - Verify: set to 0.5, only results with relevancia >= 0.5 shown
- [ ] Sort options work correctly
  - Verify: sort by relevancia descending, first result has highest score
- [ ] Checkboxes persist across filter changes
  - Verify: uncheck item, change filter, change back, item still unchecked
- [ ] Select all / Deselect all buttons work
  - Verify: click each, verify all checkboxes toggle

### Excel Export

- [ ] Excel export generates valid .xlsx file
  - Verify: download file, open in Excel/LibreOffice
- [ ] All 10 columns present with correct headers
  - Verify: column headers match specification
- [ ] Hyperlinks are clickable
  - Verify: click a link in column G, browser opens correct URL
- [ ] Formatting matches specification
  - Verify: green header, white text, percentage formatting, conditional colors
- [ ] Freeze panes enabled
  - Verify: scroll down, headers remain visible
- [ ] Auto-filter enabled
  - Verify: dropdown arrows visible on header row

### User Experience

- [ ] All user-facing text is in Brazilian Portuguese
  - Verify: no English text visible in the UI
- [ ] Camara dos Deputados green/gold theme applied
  - Verify: header colors, button styles use institutional colors
- [ ] Error messages are informative and in Portuguese
  - Verify: trigger an error scenario (e.g., no sources selected), verify message
- [ ] No hardcoded API keys or secrets in source code
  - Verify: `grep -r "AIza" levantamento-normativos/` returns no results
  - Verify: `grep -r "api_key\s*=" levantamento-normativos/*.py` returns no hardcoded values

### Security

- [ ] `.streamlit/secrets.toml` is in `.gitignore`
  - Verify: `git status` does not show secrets.toml as tracked
- [ ] No credentials logged to console
  - Verify: search log output for API key substrings
- [ ] HTTP requests use HTTPS where available
  - Verify: all URL constants in searchers use `https://`
- [ ] No `verify=False` in any requests call
  - Verify: grep source files for `verify=False`
