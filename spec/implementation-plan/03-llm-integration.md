# Phase 3: LLM Integration — Gemini Flash

**Status:** Not Started
**Dependencies:** Phase 1 (Project Setup), Phase 2 (Data Layer)
**Estimated Effort:** 1-2 days
**Output Artifacts:** `llm/__init__.py`, `llm/gemini_client.py`, `tests/test_gemini_client.py`

---

## 3.0 Overview

This phase adds AI-powered enrichment to the application using Google Gemini Flash
(free tier). Three capabilities are introduced:

| Capability | Function | Purpose |
|---|---|---|
| Keyword Expansion | `expand_topic_to_keywords()` | Generate comprehensive search terms from a topic |
| Relevance Scoring | `score_relevance()` | Rate how relevant each result is to the research topic |
| Auto-Categorization | `categorize_results()` | Assign a thematic category to each result |

**Critical design constraint:** Every LLM feature must degrade gracefully when no API
key is configured. The application must be fully functional (with reduced intelligence)
without a Gemini API key. No function may ever raise an unhandled exception.

---

## 3.1 File: `llm/gemini_client.py` — Full Specification

### 3.1.1 Module Purpose

This module encapsulates all communication with the Google Gemini API. It is the
**only** module in the project that imports `google.generativeai`. All other modules
interact with Gemini exclusively through the public functions exported here.

### 3.1.2 Dependencies

| Package | PyPI Name | Import |
|---|---|---|
| Google Generative AI SDK | `google-generativeai>=0.8.0` | `import google.generativeai as genai` |
| Streamlit (optional, runtime) | `streamlit` | `import streamlit as st` |

Add to `requirements.txt`:
```
google-generativeai>=0.8.0
```

### 3.1.3 API Key Resolution

The API key is resolved once at module load time, following this priority order:

1. `st.secrets["GEMINI_API_KEY"]` (Streamlit secrets from `.streamlit/secrets.toml`)
2. `os.environ["GEMINI_API_KEY"]` (environment variable, for testing/CI)
3. Empty string `""` (no key configured — graceful degradation mode)

The Streamlit import is wrapped in a try/except because the module may be imported
outside of a Streamlit runtime (e.g., in tests, scripts, or CLI usage).

```python
import os
import re
import json
import logging
from typing import Optional

try:
    import streamlit as st
    api_key: str = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
except Exception:
    api_key: str = os.environ.get("GEMINI_API_KEY", "")

logger = logging.getLogger(__name__)
```

### 3.1.4 Client Initialization (Lazy Singleton)

The Gemini client is initialized lazily on first use. This avoids import-time API calls
and allows the module to be safely imported even when no key is available.

```python
import google.generativeai as genai

_client: Optional[genai.GenerativeModel] = None


def _get_client() -> Optional[genai.GenerativeModel]:
    """Return the singleton GenerativeModel instance, or None if unavailable.

    Configures the SDK on first call. Subsequent calls return the cached instance.
    Returns None if no API key is configured, allowing callers to fall back gracefully.
    """
    global _client
    if _client is None:
        if not api_key:
            logger.info("GEMINI_API_KEY not configured — LLM features disabled.")
            return None
        genai.configure(api_key=api_key)
        _client = genai.GenerativeModel("gemini-2.0-flash")
    return _client


def is_available() -> bool:
    """Check if Gemini API is configured and available.

    Returns:
        True if a non-empty API key was found at module load time.
    """
    return bool(api_key)
```

**Model selection:** `gemini-2.0-flash` is the latest Gemini Flash model. It provides
the best balance of speed, cost, and quality for structured-output tasks. The free tier
allows 15 requests per minute (RPM) and 1 million tokens per day.

### 3.1.5 Constants

```python
BATCH_SIZE: int = 20
"""Maximum number of results to send in a single LLM call.

Keeps prompt size under the token limit and improves response reliability.
Gemini Flash supports 1M token context, but smaller prompts produce more
accurate structured output.
"""

CATEGORIES: list[str] = [
    "Governanca de TI",
    "Seguranca da Informacao",
    "Contratacoes e Licitacoes",
    "Auditoria e Controle",
    "Protecao de Dados",
    "Transparencia e Acesso a Informacao",
    "Gestao de Riscos",
    "Software e Desenvolvimento",
    "Infraestrutura de TI",
    "Marco Legal e Regulatorio",
    "Gestao de Pessoas",
    "Orcamento e Financas",
    "Outro",
]
"""Predefined thematic categories for normativo classification.

IMPORTANT: These strings use ASCII-only characters (no accents) to avoid
encoding issues in comparisons. The display layer (UI) may render accented
versions, but internal storage and LLM validation use these exact strings.

UPDATE (implementation note): If the team prefers accented category names for
display consistency, use the following list instead and ensure UTF-8 handling
throughout:
"""

CATEGORIES_DISPLAY: list[str] = [
    "Governanca de TI",
    "Seguranca da Informacao",
    "Contratacoes e Licitacoes",
    "Auditoria e Controle",
    "Protecao de Dados",
    "Transparencia e Acesso a Informacao",
    "Gestao de Riscos",
    "Software e Desenvolvimento",
    "Infraestrutura de TI",
    "Marco Legal e Regulatorio",
    "Gestao de Pessoas",
    "Orcamento e Financas",
    "Outro",
]
```

**Implementation decision:** Use the accented versions as the canonical category names
throughout the application, since all data is UTF-8:

```python
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
```

---

## 3.2 Function 1: `expand_topic_to_keywords`

### 3.2.1 Signature

```python
def expand_topic_to_keywords(topic: str) -> list[str]:
    """Generate search keywords from a natural language topic description.

    Uses Gemini Flash to expand a topic into a comprehensive list of search
    keywords for Brazilian legislation databases. If the LLM is unavailable,
    returns an empty list — the caller should then prompt the user to enter
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
```

### 3.2.2 Prompt

The prompt must be sent **exactly** as specified below (including whitespace). The only
dynamic part is `{topic}`, which is the `topic` argument interpolated via f-string.

```
Você é um especialista em legislação brasileira e auditoria governamental de TI.

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

Gere entre 15 e 30 palavras-chave.
```

### 3.2.3 Model Configuration

| Parameter | Value | Rationale |
|---|---|---|
| `temperature` | `0.3` | Low but nonzero to allow some creative keyword variation |
| `max_output_tokens` | `1024` | Generous limit for a JSON array of 30 short strings |

Pass as `generation_config` parameter:
```python
response = client.generate_content(
    prompt,
    generation_config=genai.types.GenerationConfig(
        temperature=0.3,
        max_output_tokens=1024,
    ),
)
```

### 3.2.4 Response Parsing Logic

The LLM may return clean JSON or wrap it in markdown code fences. The parsing pipeline
handles both cases.

```python
def _parse_json_array(text: str) -> Optional[list]:
    """Extract and parse a JSON array from LLM response text.

    Handles common LLM output quirks:
    - Markdown code fences (```json ... ``` or ``` ... ```)
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
```

Validation after parsing (specific to this function):
```python
parsed = _parse_json_array(response.text)
if parsed is None:
    logger.warning("Failed to parse keyword list from Gemini response.")
    return []

# Validate: must be a list of strings
keywords = [str(item) for item in parsed if isinstance(item, str) or isinstance(item, (int, float))]
if not keywords:
    logger.warning("Gemini returned empty or non-string keyword list.")
    return []

return keywords
```

### 3.2.5 Complete Implementation

```python
def expand_topic_to_keywords(topic: str) -> list[str]:
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

        parsed = _parse_json_array(response.text)
        if parsed is None:
            logger.warning("Failed to parse keyword list from Gemini response.")
            return []

        keywords = [str(item) for item in parsed if isinstance(item, (str, int, float))]
        if not keywords:
            logger.warning("Gemini returned empty or non-string keyword list.")
            return []

        logger.info("Gemini expanded topic into %d keywords.", len(keywords))
        return keywords

    except Exception as e:
        logger.warning("Gemini API error during keyword expansion: %s", e)
        return []
```

### 3.2.6 Error Handling Summary

| Scenario | Behavior | Return Value |
|---|---|---|
| No API key configured | Log info, return immediately | `[]` |
| API rate limit (429) | Caught by outer except, log warning | `[]` |
| Network error | Caught by outer except, log warning | `[]` |
| Invalid JSON response | Log warning after parse attempts | `[]` |
| Empty list in response | Log warning | `[]` |
| API returns non-list JSON | Caught by validation, log warning | `[]` |

---

## 3.3 Function 2: `score_relevance`

### 3.3.1 Signature

```python
def score_relevance(topic: str, results: list[dict]) -> list[float]:
    """Score how relevant each search result is to the research topic.

    Processes results in batches of 20 to stay within token limits. When the
    LLM is unavailable, falls back to a keyword-matching heuristic.

    Args:
        topic: The original research topic in natural language.
        results: List of dicts, each containing at minimum:
                 - "nome" (str): Name/identifier of the normativo
                 - "ementa" (str): Summary/description text
                 Additional keys are ignored.

    Returns:
        List of float scores in [0.0, 1.0], same length and order as `results`.
        Higher scores indicate greater relevance to the topic.

    Example:
        >>> score_relevance("LGPD", [{"nome": "Lei 13.709", "ementa": "Proteção de dados..."}])
        [0.95]
    """
```

### 3.3.2 Input Contract

The `results` parameter is a list of dicts. The caller is responsible for converting
domain objects (e.g., `NormativoResult` dataclass instances) into dicts before calling
this function. Required keys:

| Key | Type | Description |
|---|---|---|
| `nome` | `str` | Display name of the normativo (e.g., "Lei 13.709/2018") |
| `ementa` | `str` | Summary text (ementa) of the normativo |

If a dict is missing `nome` or `ementa`, use empty string `""` as default.

### 3.3.3 Batch Processing

Results are split into batches of `BATCH_SIZE` (20) items. Each batch makes one API
call. Scores from all batches are concatenated into the final result list.

```python
def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into chunks of at most chunk_size items."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
```

### 3.3.4 Prompt

For each batch, the prompt is built as follows:

```
Você é um especialista em legislação brasileira e auditoria de TI.

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
Exemplo: [0.9, 0.3, 0.7, 0.1]
```

Where `{formatted_list}` is constructed per batch:
```python
formatted_list = "\n".join(
    f"{i+1}. {r.get('nome', '')}: {r.get('ementa', '')[:200]}"
    for i, r in enumerate(batch)
)
```

**Note:** The ementa is truncated to 200 characters per result to keep the prompt size
manageable. This is sufficient for relevance assessment since ementas are typically
concise summaries.

### 3.3.5 Model Configuration

| Parameter | Value | Rationale |
|---|---|---|
| `temperature` | `0.0` | Deterministic scoring — same input should produce same scores |
| `max_output_tokens` | `512` | Sufficient for a JSON array of 20 floats |

### 3.3.6 Response Parsing and Validation

```python
parsed = _parse_json_array(response.text)
if parsed is None or len(parsed) != len(batch):
    logger.warning(
        "Gemini relevance scores: expected %d items, parse returned %s. Using fallback.",
        len(batch),
        len(parsed) if parsed else "None",
    )
    return [0.5] * len(batch)

# Validate and clamp each score
scores = []
for val in parsed:
    try:
        score = float(val)
        score = max(0.0, min(1.0, score))  # Clamp to [0.0, 1.0]
    except (TypeError, ValueError):
        score = 0.5  # Default for unparseable individual scores
    scores.append(score)

return scores
```

### 3.3.7 Keyword-Based Fallback Heuristic

When the LLM is unavailable, relevance is estimated using keyword matching. This
function is also used as the fallback from the UI integration layer.

```python
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
```

**Important:** `_keyword_relevance` is a module-level private function. It is not
exported in `__init__.py`, but the UI integration code in the Streamlit app needs access
to it for the no-API-key fallback path. Two options:

- **Option A (recommended):** Export it as `keyword_relevance` (without underscore) in
  `__init__.py` so the UI layer can import it.
- **Option B:** Keep it private and have `score_relevance` itself use the fallback
  internally when the client is None, accepting a `keywords` parameter for the fallback.

**Decision: Use Option B.** Modify the signature to accept optional keywords:

```python
def score_relevance(
    topic: str,
    results: list[dict],
    keywords: Optional[list[str]] = None,
) -> list[float]:
```

When the LLM is unavailable and `keywords` is provided, use the keyword heuristic.
When neither LLM nor keywords are available, return `[0.5] * len(results)`.

### 3.3.8 Complete Implementation

```python
def score_relevance(
    topic: str,
    results: list[dict],
    keywords: Optional[list[str]] = None,
) -> list[float]:
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

            parsed = _parse_json_array(response.text)
            if parsed is not None and len(parsed) == len(batch):
                batch_scores = []
                for val in parsed:
                    try:
                        score = float(val)
                        score = max(0.0, min(1.0, score))
                    except (TypeError, ValueError):
                        score = 0.5
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

    return all_scores
```

### 3.3.9 Rate Limit Consideration

With 15 RPM on the free tier and batches of 20, this function can score:
- 20 results: 1 API call
- 100 results: 5 API calls
- 300 results: 15 API calls (hits rate limit)

If more than ~300 results need scoring, add a delay between batches:

```python
import time

# Inside the batch loop, after each API call:
if batch_idx < len(batches) - 1:
    time.sleep(4.0)  # ~15 RPM = 1 call every 4 seconds
```

**Decision:** Only add the sleep if `len(batches) > 10` to avoid unnecessary delays
for typical workloads. For small result sets (under 200 items), no delay is needed.

```python
if len(batches) > 10 and batch_idx < len(batches) - 1:
    time.sleep(4.0)
```

---

## 3.4 Function 3: `categorize_results`

### 3.4.1 Signature

```python
def categorize_results(topic: str, results: list[dict]) -> list[str]:
    """Assign a thematic category to each search result.

    Each result is assigned exactly one category from the CATEGORIES list.
    Processes results in batches of 20. When the LLM is unavailable, returns
    "Não categorizado" for all results.

    Args:
        topic: The original research topic (provides context for categorization).
        results: List of dicts with "nome" and "ementa" keys.

    Returns:
        List of category strings, same length and order as `results`.
        Each string is guaranteed to be from CATEGORIES or "Não categorizado".

    Example:
        >>> categorize_results("LGPD", [{"nome": "Lei 13.709", "ementa": "Proteção de dados..."}])
        ["Proteção de Dados"]
    """
```

### 3.4.2 Prompt

```
Você é um especialista em legislação brasileira e auditoria de TI.

Categorize cada normativo abaixo em UMA das seguintes categorias:
{categories_list}

Normativos:
{formatted_list}

Retorne APENAS um JSON array de strings com a categoria de cada normativo, na mesma ordem.
Exemplo: ["Governança de TI", "Segurança da Informação", "Outro"]
```

Where:
```python
categories_list = "\n".join(f"- {cat}" for cat in CATEGORIES)

formatted_list = "\n".join(
    f"{i+1}. {r.get('nome', '')}: {r.get('ementa', '')[:200]}"
    for i, r in enumerate(batch)
)
```

### 3.4.3 Model Configuration

| Parameter | Value | Rationale |
|---|---|---|
| `temperature` | `0.0` | Deterministic categorization |
| `max_output_tokens` | `512` | Sufficient for 20 category strings |

### 3.4.4 Response Parsing and Validation

```python
parsed = _parse_json_array(response.text)
if parsed is None or len(parsed) != len(batch):
    logger.warning(
        "Batch %d: expected %d categories, got %s. Using fallback.",
        batch_idx, len(batch),
        len(parsed) if parsed else "None",
    )
    return ["Não categorizado"] * len(batch)

# Validate each category against the allowed list
categories = []
for val in parsed:
    val_str = str(val).strip()
    if val_str in CATEGORIES:
        categories.append(val_str)
    else:
        # Attempt fuzzy match: check if the LLM returned a close variant
        matched = _fuzzy_match_category(val_str)
        categories.append(matched if matched else "Outro")

return categories
```

### 3.4.5 Fuzzy Category Matching

The LLM may return slight variations of category names (e.g., missing accents, extra
whitespace, different capitalization). A simple fuzzy matcher handles this:

```python
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
    import unicodedata

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
```

### 3.4.6 Complete Implementation

```python
def categorize_results(topic: str, results: list[dict]) -> list[str]:
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

            parsed = _parse_json_array(response.text)
            if parsed is not None and len(parsed) == len(batch):
                for val in parsed:
                    val_str = str(val).strip()
                    if val_str in CATEGORIES:
                        all_categories.append(val_str)
                    else:
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

        # Rate limiting for large result sets
        if len(batches) > 10 and batch_idx < len(batches) - 1:
            time.sleep(4.0)

    return all_categories
```

---

## 3.5 File: `llm/__init__.py`

```python
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
    is_available,
    expand_topic_to_keywords,
    score_relevance,
    categorize_results,
    CATEGORIES,
)

__all__ = [
    "is_available",
    "expand_topic_to_keywords",
    "score_relevance",
    "categorize_results",
    "CATEGORIES",
]
```

---

## 3.6 Integration Points with the Streamlit App

This section describes how the three LLM functions connect to the application's UI
workflow. The app follows a multi-step wizard pattern. LLM calls happen at specific
steps.

### 3.6.1 Step 2: Keyword Review (Topic Mode)

When the user chooses "topic" input mode, the app uses `expand_topic_to_keywords` to
generate an initial keyword list that the user can then review and edit.

```python
from llm import gemini_client

# In Step 2 handler — only when input_mode is "topic"
if st.session_state["input_mode"] == "topic" and gemini_client.is_available():
    with st.spinner("Expandindo tema com IA..."):
        keywords = gemini_client.expand_topic_to_keywords(st.session_state["topic"])
        st.session_state["keywords"] = keywords
elif st.session_state["input_mode"] == "topic":
    # No API key — show info message and let user enter keywords manually
    st.info(
        "Chave da API Gemini não configurada. "
        "Insira as palavras-chave manualmente abaixo."
    )
    st.session_state["keywords"] = []
```

**UI behavior after keyword generation:**
- Display the generated keywords as editable chips or a text area
- User can add, remove, or modify keywords before proceeding
- The edited list is stored in `st.session_state["keywords"]` for the search step

### 3.6.2 Step 3: Post-Search Enrichment

After the search engine returns raw results, the app enriches them with relevance
scores and categories using the LLM.

```python
from llm import gemini_client

# After search results are obtained
results = search_engine.search(keywords)  # Returns list of NormativoResult objects

if gemini_client.is_available():
    with st.spinner("Avaliando relevância com IA..."):
        results_dicts = [{"nome": r.nome, "ementa": r.ementa} for r in results]
        scores = gemini_client.score_relevance(
            st.session_state["topic"],
            results_dicts,
        )
        categories = gemini_client.categorize_results(
            st.session_state["topic"],
            results_dicts,
        )
        for i, r in enumerate(results):
            r.relevancia = scores[i]
            r.categoria = categories[i]
else:
    # Fallback: keyword-based relevance, no categorization
    for r in results:
        r.relevancia = gemini_client._keyword_relevance(
            st.session_state["keywords"],
            r.ementa,
        )
        r.categoria = "Não categorizado"
```

**Important implementation note:** The fallback path accesses `_keyword_relevance`
directly. Since `score_relevance` already has internal fallback logic, the caller can
alternatively just call `score_relevance` with the `keywords` parameter and let the
function handle the fallback:

```python
# Cleaner approach — let score_relevance handle its own fallback
results_dicts = [{"nome": r.nome, "ementa": r.ementa} for r in results]
scores = gemini_client.score_relevance(
    st.session_state.get("topic", ""),
    results_dicts,
    keywords=st.session_state.get("keywords"),
)
categories = gemini_client.categorize_results(
    st.session_state.get("topic", ""),
    results_dicts,
)
for i, r in enumerate(results):
    r.relevancia = scores[i]
    r.categoria = categories[i]
```

### 3.6.3 UI Indicators for LLM Status

The app should display the LLM availability status to the user:

```python
# In sidebar or header
if gemini_client.is_available():
    st.sidebar.success("IA Gemini: Ativa", icon="🤖")
else:
    st.sidebar.warning("IA Gemini: Inativa (sem chave API)", icon="⚠️")
```

---

## 3.7 Shared Utility: `_parse_json_array`

This is a private helper used by all three public functions. It is defined once at
module level in `gemini_client.py`.

**Full implementation:** See Section 3.2.4 above.

**Key properties:**
- Idempotent and side-effect-free
- Handles markdown code fences (`\`\`\`json ... \`\`\``)
- Falls back to regex extraction if direct parse fails
- Returns `None` on complete failure (never raises)

---

## 3.8 Shared Utility: `_chunk_list`

```python
def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into consecutive chunks of at most chunk_size elements.

    Args:
        items: The list to split.
        chunk_size: Maximum number of elements per chunk. Must be > 0.

    Returns:
        List of sublists. The last sublist may have fewer than chunk_size elements.

    Example:
        >>> _chunk_list([1, 2, 3, 4, 5], 2)
        [[1, 2], [3, 4], [5]]
    """
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
```

---

## 3.9 Complete File Layout

After implementing this phase, the `llm/` directory structure is:

```
llm/
  __init__.py          # Public API exports
  gemini_client.py     # All Gemini integration logic
```

### 3.9.1 `gemini_client.py` — Full Symbol Table

| Symbol | Kind | Visibility | Description |
|---|---|---|---|
| `api_key` | `str` | Module-level | Resolved API key (may be empty) |
| `logger` | `Logger` | Module-level | Logger for this module |
| `_client` | `GenerativeModel` or `None` | Private | Cached Gemini client |
| `BATCH_SIZE` | `int` | Public constant | `20` |
| `CATEGORIES` | `list[str]` | Public constant | 13 predefined category strings |
| `_get_client()` | Function | Private | Lazy client initializer |
| `is_available()` | Function | Public | Check if API key is configured |
| `expand_topic_to_keywords()` | Function | Public | Topic to keywords |
| `score_relevance()` | Function | Public | Relevance scoring |
| `categorize_results()` | Function | Public | Category assignment |
| `_parse_json_array()` | Function | Private | JSON array parser |
| `_chunk_list()` | Function | Private | List chunking utility |
| `_keyword_relevance()` | Function | Private | Fallback keyword heuristic |
| `_fuzzy_match_category()` | Function | Private | Fuzzy category matcher |

### 3.9.2 Import Order in `gemini_client.py`

```python
"""Gemini Flash integration for keyword expansion, relevance scoring, and categorization."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from typing import Optional

import google.generativeai as genai

try:
    import streamlit as st
    api_key: str = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
except Exception:
    api_key: str = os.environ.get("GEMINI_API_KEY", "")

logger = logging.getLogger(__name__)
```

---

## 3.10 Configuration via `.streamlit/secrets.toml`

The API key is stored in the Streamlit secrets file, which is **never committed to
version control**.

### 3.10.1 File Location

```
.streamlit/secrets.toml
```

### 3.10.2 File Contents

```toml
GEMINI_API_KEY = "your-api-key-here"
```

### 3.10.3 `.gitignore` Entry

Ensure `.streamlit/secrets.toml` is in `.gitignore`:

```
.streamlit/secrets.toml
```

### 3.10.4 Getting a Free API Key

1. Go to https://aistudio.google.com/apikey
2. Sign in with a Google account
3. Click "Create API Key"
4. Copy the key into `.streamlit/secrets.toml`

Free tier limits (as of the model `gemini-2.0-flash`):
- 15 requests per minute (RPM)
- 1,000,000 tokens per day
- 32,768 tokens per request (output)

---

## 3.11 Testing Specification

### 3.11.1 File: `tests/test_gemini_client.py`

All tests must work **without** a real API key. Use `unittest.mock.patch` to mock the
Gemini client.

### 3.11.2 Unit Tests

```python
"""Tests for llm/gemini_client.py"""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestIsAvailable:
    """Tests for is_available()."""

    def test_returns_true_when_key_set(self):
        """is_available() returns True when api_key is non-empty."""
        with patch("llm.gemini_client.api_key", "test-key"):
            from llm.gemini_client import is_available
            assert is_available() is True

    def test_returns_false_when_key_empty(self):
        """is_available() returns False when api_key is empty."""
        with patch("llm.gemini_client.api_key", ""):
            from llm.gemini_client import is_available
            assert is_available() is False


class TestParseJsonArray:
    """Tests for _parse_json_array()."""

    def test_parses_clean_json(self):
        from llm.gemini_client import _parse_json_array
        assert _parse_json_array('["a", "b", "c"]') == ["a", "b", "c"]

    def test_parses_json_with_code_fences(self):
        from llm.gemini_client import _parse_json_array
        text = '```json\n["a", "b"]\n```'
        assert _parse_json_array(text) == ["a", "b"]

    def test_parses_json_embedded_in_text(self):
        from llm.gemini_client import _parse_json_array
        text = 'Here are the results:\n["a", "b"]\nHope this helps!'
        assert _parse_json_array(text) == ["a", "b"]

    def test_returns_none_for_invalid_json(self):
        from llm.gemini_client import _parse_json_array
        assert _parse_json_array("not json at all") is None

    def test_parses_numeric_array(self):
        from llm.gemini_client import _parse_json_array
        assert _parse_json_array("[0.9, 0.3, 0.7]") == [0.9, 0.3, 0.7]


class TestExpandTopicToKeywords:
    """Tests for expand_topic_to_keywords()."""

    def test_returns_empty_list_when_unavailable(self):
        with patch("llm.gemini_client._get_client", return_value=None):
            from llm.gemini_client import expand_topic_to_keywords
            assert expand_topic_to_keywords("any topic") == []

    def test_returns_keywords_on_success(self):
        mock_response = MagicMock()
        mock_response.text = '["governança", "PDTIC", "SISP"]'

        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_response

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import expand_topic_to_keywords
            result = expand_topic_to_keywords("governança de TI")
            assert result == ["governança", "PDTIC", "SISP"]

    def test_returns_empty_list_on_api_error(self):
        mock_client = MagicMock()
        mock_client.generate_content.side_effect = Exception("API error")

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import expand_topic_to_keywords
            assert expand_topic_to_keywords("any topic") == []

    def test_returns_empty_list_on_invalid_response(self):
        mock_response = MagicMock()
        mock_response.text = "This is not JSON"

        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_response

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import expand_topic_to_keywords
            assert expand_topic_to_keywords("any topic") == []


class TestScoreRelevance:
    """Tests for score_relevance()."""

    def test_returns_empty_list_for_empty_input(self):
        from llm.gemini_client import score_relevance
        assert score_relevance("topic", []) == []

    def test_returns_default_scores_when_unavailable_no_keywords(self):
        with patch("llm.gemini_client._get_client", return_value=None):
            from llm.gemini_client import score_relevance
            results = [{"nome": "Lei X", "ementa": "text"}]
            assert score_relevance("topic", results) == [0.5]

    def test_uses_keyword_fallback_when_unavailable(self):
        with patch("llm.gemini_client._get_client", return_value=None):
            from llm.gemini_client import score_relevance
            results = [{"nome": "Lei X", "ementa": "governança de TI no setor público"}]
            scores = score_relevance("topic", results, keywords=["governança", "TI"])
            assert len(scores) == 1
            assert scores[0] > 0.0  # Both keywords match

    def test_clamps_scores_to_valid_range(self):
        mock_response = MagicMock()
        mock_response.text = "[1.5, -0.3, 0.7]"

        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_response

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import score_relevance
            results = [
                {"nome": "A", "ementa": "x"},
                {"nome": "B", "ementa": "y"},
                {"nome": "C", "ementa": "z"},
            ]
            scores = score_relevance("topic", results)
            assert scores == [1.0, 0.0, 0.7]

    def test_handles_batch_processing(self):
        """Verify that 25 results are split into 2 batches (20 + 5)."""
        mock_response_1 = MagicMock()
        mock_response_1.text = json.dumps([0.5] * 20)

        mock_response_2 = MagicMock()
        mock_response_2.text = json.dumps([0.8] * 5)

        mock_client = MagicMock()
        mock_client.generate_content.side_effect = [mock_response_1, mock_response_2]

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import score_relevance
            results = [{"nome": f"Lei {i}", "ementa": f"text {i}"} for i in range(25)]
            scores = score_relevance("topic", results)
            assert len(scores) == 25
            assert scores[:20] == [0.5] * 20
            assert scores[20:] == [0.8] * 5


class TestCategorizeResults:
    """Tests for categorize_results()."""

    def test_returns_empty_list_for_empty_input(self):
        from llm.gemini_client import categorize_results
        assert categorize_results("topic", []) == []

    def test_returns_uncategorized_when_unavailable(self):
        with patch("llm.gemini_client._get_client", return_value=None):
            from llm.gemini_client import categorize_results
            results = [{"nome": "Lei X", "ementa": "text"}]
            assert categorize_results("topic", results) == ["Não categorizado"]

    def test_maps_invalid_category_to_outro(self):
        mock_response = MagicMock()
        mock_response.text = '["Invalid Category"]'

        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_response

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import categorize_results
            results = [{"nome": "Lei X", "ementa": "text"}]
            cats = categorize_results("topic", results)
            assert cats == ["Outro"]

    def test_fuzzy_matches_category_without_accents(self):
        mock_response = MagicMock()
        mock_response.text = '["Governanca de TI"]'  # Missing accent

        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_response

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import categorize_results
            results = [{"nome": "Lei X", "ementa": "text"}]
            cats = categorize_results("topic", results)
            assert cats == ["Governança de TI"]

    def test_returns_valid_categories_on_success(self):
        mock_response = MagicMock()
        mock_response.text = '["Segurança da Informação", "Proteção de Dados"]'

        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_response

        with patch("llm.gemini_client._get_client", return_value=mock_client):
            from llm.gemini_client import categorize_results
            results = [
                {"nome": "A", "ementa": "x"},
                {"nome": "B", "ementa": "y"},
            ]
            cats = categorize_results("topic", results)
            assert cats == ["Segurança da Informação", "Proteção de Dados"]


class TestKeywordRelevance:
    """Tests for _keyword_relevance()."""

    def test_returns_zero_for_empty_keywords(self):
        from llm.gemini_client import _keyword_relevance
        assert _keyword_relevance([], "some text") == 0.0

    def test_returns_zero_for_empty_ementa(self):
        from llm.gemini_client import _keyword_relevance
        assert _keyword_relevance(["keyword"], "") == 0.0

    def test_returns_one_when_all_keywords_match(self):
        from llm.gemini_client import _keyword_relevance
        assert _keyword_relevance(["lei", "decreto"], "lei e decreto sobre TI") == 1.0

    def test_partial_match(self):
        from llm.gemini_client import _keyword_relevance
        score = _keyword_relevance(["lei", "decreto", "portaria"], "lei sobre TI")
        assert abs(score - 1 / 3) < 0.01

    def test_case_insensitive(self):
        from llm.gemini_client import _keyword_relevance
        assert _keyword_relevance(["LEI"], "lei federal") == 1.0
```

### 3.11.3 Running Tests

```bash
# Run all LLM tests
pytest tests/test_gemini_client.py -v

# Run with coverage
pytest tests/test_gemini_client.py --cov=llm --cov-report=term-missing
```

---

## 3.12 Acceptance Criteria

Each criterion must be verifiable either by automated tests or manual testing.

| # | Criterion | Verification Method |
|---|---|---|
| AC-1 | `is_available()` returns `True` when `GEMINI_API_KEY` is set to a non-empty value | Unit test |
| AC-2 | `is_available()` returns `False` when `GEMINI_API_KEY` is empty or unset | Unit test |
| AC-3 | `expand_topic_to_keywords("governança de TI")` returns a list of 15-30 Portuguese keywords | Manual test with real API key |
| AC-4 | `expand_topic_to_keywords()` returns `[]` when API is unavailable | Unit test |
| AC-5 | `score_relevance()` returns a list of floats in [0.0, 1.0] for each input result | Unit test + manual test |
| AC-6 | `score_relevance()` returns keyword-based heuristic scores when API is unavailable and keywords are provided | Unit test |
| AC-7 | `score_relevance()` returns `[0.5, ...]` when API is unavailable and no keywords are provided | Unit test |
| AC-8 | `categorize_results()` returns valid category strings from the `CATEGORIES` list | Unit test + manual test |
| AC-9 | `categorize_results()` returns `["Não categorizado", ...]` when API is unavailable | Unit test |
| AC-10 | Invalid LLM categories are mapped to `"Outro"` via fuzzy matching | Unit test |
| AC-11 | No function ever raises an unhandled exception (all wrapped in try/except) | Unit tests for error scenarios |
| AC-12 | Batch processing correctly handles lists of 100+ results | Unit test (mock 5+ batches) |
| AC-13 | All three functions return results in the same order as input | Unit tests verify order preservation |
| AC-14 | Rate limiting delay activates only when more than 10 batches are needed | Code review |
| AC-15 | `.streamlit/secrets.toml` is listed in `.gitignore` | Manual verification |

---

## 3.13 Error Handling Matrix

Complete mapping of every error scenario to its expected behavior:

| Function | Error Scenario | Log Level | Return Value |
|---|---|---|---|
| `_get_client()` | Empty API key | INFO | `None` |
| `expand_topic_to_keywords` | No client | INFO | `[]` |
| `expand_topic_to_keywords` | API exception (any) | WARNING | `[]` |
| `expand_topic_to_keywords` | JSON parse failure | WARNING | `[]` |
| `expand_topic_to_keywords` | Empty list in response | WARNING | `[]` |
| `score_relevance` | No client, no keywords | INFO | `[0.5] * len(results)` |
| `score_relevance` | No client, with keywords | INFO | Keyword heuristic scores |
| `score_relevance` | API exception (per batch) | WARNING | `[0.5] * len(batch)` |
| `score_relevance` | JSON parse failure (per batch) | WARNING | `[0.5] * len(batch)` |
| `score_relevance` | Wrong array length (per batch) | WARNING | `[0.5] * len(batch)` |
| `score_relevance` | Non-numeric value in array | N/A | Individual value becomes `0.5` |
| `categorize_results` | No client | INFO | `["Não categorizado"] * len(results)` |
| `categorize_results` | API exception (per batch) | WARNING | `["Não categorizado"] * len(batch)` |
| `categorize_results` | JSON parse failure (per batch) | WARNING | `["Não categorizado"] * len(batch)` |
| `categorize_results` | Wrong array length (per batch) | WARNING | `["Não categorizado"] * len(batch)` |
| `categorize_results` | Unknown category string | N/A | Fuzzy match or `"Outro"` |

---

## 3.14 Performance Characteristics

| Metric | Value | Notes |
|---|---|---|
| `expand_topic_to_keywords` latency | ~1-2s | Single API call |
| `score_relevance` per batch | ~1-2s | Per 20 results |
| `categorize_results` per batch | ~1-2s | Per 20 results |
| Total for 100 results | ~12-15s | 5 scoring + 5 categorization batches |
| Keyword fallback latency | <1ms | Pure string matching, no API call |

**Optimization opportunity:** `score_relevance` and `categorize_results` could be
combined into a single prompt per batch (asking the LLM to return both score and
category together). This would halve API calls but increase prompt complexity and
parsing fragility. **Recommendation:** Keep them separate for Phase 3; consider merging
in a future optimization phase if performance is a concern.

---

## 3.15 Security Considerations

1. **API key storage:** The key is stored in `.streamlit/secrets.toml`, which must be
   in `.gitignore`. Never log the API key value.
2. **Input sanitization:** The `topic` string is interpolated into prompts. While
   prompt injection is not a security risk here (the LLM output is only used to
   generate keywords/scores, not executed), avoid sending excessively long topics.
   Consider truncating `topic` to 500 characters.
3. **Output validation:** All LLM outputs are validated before use. Scores are clamped,
   categories are checked against allowlists, and keywords are filtered to strings only.
4. **No user data exfiltration:** The only data sent to Google's API is the topic text
   and normativo names/ementas (which are public government data).

---

## 3.16 Dependencies on Other Phases

| Phase | Dependency | Direction |
|---|---|---|
| Phase 2 (Data Layer) | `NormativoResult` dataclass with `relevancia` and `categoria` fields | This phase writes to those fields |
| Phase 4 (Search Engines) | Search functions return `NormativoResult` objects | This phase enriches those objects |
| Phase 5 (Streamlit UI) | UI calls LLM functions at specific workflow steps | UI depends on this phase |

---

## 3.17 Implementation Checklist

The developer should implement in this order:

- [ ] Add `google-generativeai>=0.8.0` to `requirements.txt`
- [ ] Create `llm/__init__.py` with exports
- [ ] Create `llm/gemini_client.py` with imports and constants
- [ ] Implement `_parse_json_array()` helper
- [ ] Implement `_chunk_list()` helper
- [ ] Implement `_keyword_relevance()` fallback
- [ ] Implement `_fuzzy_match_category()` helper
- [ ] Implement API key resolution and `_get_client()`
- [ ] Implement `is_available()`
- [ ] Implement `expand_topic_to_keywords()`
- [ ] Implement `score_relevance()`
- [ ] Implement `categorize_results()`
- [ ] Create `tests/test_gemini_client.py` with all unit tests
- [ ] Verify `.streamlit/secrets.toml` is in `.gitignore`
- [ ] Run tests: `pytest tests/test_gemini_client.py -v`
- [ ] Manual test with real API key (optional, requires key)
