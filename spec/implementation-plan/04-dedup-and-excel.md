# Phase 4: Deduplication & Excel Export

This phase implements two independent modules that sit between the search pipeline and the user-facing UI: a deduplication engine that merges overlapping results from multiple sources, and an Excel exporter that produces a formatted `.xlsx` workbook ready for stakeholder distribution.

---

## 4.1 deduplicator.py -- Cross-Source Deduplication

### Problem Statement

Results from LexML, TCU Dados Abertos, and Google Custom Search frequently reference the same normativo. A single "Instrucao Normativa" may appear in all three sources with slightly different metadata (different ementa lengths, different date formats, different link domains). Presenting duplicates wastes the reviewer's time and inflates result counts, undermining trust in the tool.

### Module Location

```
levantamento-normativos/
  deduplicator.py
```

### Dependencies

- `difflib` (standard library) -- SequenceMatcher for fuzzy text comparison
- `unicodedata` (standard library) -- accent normalization
- `re` (standard library) -- whitespace normalization
- `models.py` -- NormativoResult dataclass

### Deduplication Strategy (Priority Order)

The deduplicator applies three matching strategies in sequence. When a match is found at any level, the duplicate is merged into the existing record and processing moves to the next input item.

#### Strategy 1: Exact ID Match

`NormativoResult.id` is a SHA-256 hex digest of `f"{tipo}|{numero}|{data}"`. Two results with the same `id` are definitively the same normativo regardless of source.

- **Lookup:** O(1) via dictionary keyed on `id`.
- **When it fires:** Same normativo indexed by multiple sources with consistent metadata extraction.

#### Strategy 2: Tipo + Numero Match

If `tipo` and `numero` are both non-empty and match case-insensitively, the records refer to the same normativo even if dates differ slightly (e.g., publication date vs. signature date).

- **Lookup:** O(1) via dictionary keyed on `(tipo.lower().strip(), numero.lower().strip())`.
- **Edge case:** If either `tipo` or `numero` is empty string, skip this strategy for that record to avoid false positives (e.g., two records with empty tipo and empty numero would incorrectly match).

#### Strategy 3: Fuzzy Ementa Match

When neither ID nor tipo+numero match, compare the normalized ementa text of the incoming record against every record already in the output list using `difflib.SequenceMatcher`. If the similarity ratio is >= 0.85, treat as duplicate.

- **Lookup:** O(n) scan of the output list per incoming record, making the overall fuzzy phase O(n^2).
- **When it fires:** Catches cases where metadata extraction produced different tipo/numero strings but the ementa is substantially the same (common with Google snippet results vs. LexML full text).

### Normalization Function

Before fuzzy comparison, ementas are normalized to maximize match quality:

```python
import re
import unicodedata


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
    text = re.sub(r"\s+", " ", text)

    # Remove punctuation that varies between sources (periods, commas, semicolons,
    # dashes, parentheses, quotes). Keep alphanumeric and spaces only.
    text = re.sub(r"[^\w\s]", "", text)

    return text
```

### Merge Function

When a duplicate is detected, the existing record in the output list is updated with the best data from both records:

```python
# Source authority ranking: lower index = higher authority
_SOURCE_PRIORITY = {"lexml": 0, "tcu": 1, "google": 2}


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

    # Link: prefer more authoritative source
    existing_priority = _SOURCE_PRIORITY.get(existing.source.split(",")[0].strip(), 99)
    incoming_priority = _SOURCE_PRIORITY.get(incoming.source.split(",")[0].strip(), 99)
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
```

### Main Deduplication Function

```python
import difflib
from models import NormativoResult


# Threshold for fuzzy ementa matching. Two ementas with a SequenceMatcher
# ratio at or above this value are considered duplicates. Tuned empirically:
# 0.85 catches reformatted ementas while avoiding false merges of distinct
# normativos that share boilerplate language.
_FUZZY_THRESHOLD = 0.85

# Maximum result set size for fuzzy matching. Beyond this count, fuzzy
# matching is skipped to avoid O(n^2) performance degradation, and only
# ID and tipo+numero matching are applied.
_FUZZY_MAX_ITEMS = 1000


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

    skip_fuzzy = len(results) > _FUZZY_MAX_ITEMS

    for result in results:
        # --- Strategy 1: Exact ID match ---
        if result.id and result.id in seen_ids:
            _merge(output[seen_ids[result.id]], result)
            continue

        # --- Strategy 2: Tipo + Numero match ---
        tipo_lower = (result.tipo or "").lower().strip()
        numero_lower = (result.numero or "").lower().strip()
        key = (tipo_lower, numero_lower)

        if tipo_lower and numero_lower and key in seen_tipo_num:
            _merge(output[seen_tipo_num[key]], result)
            continue

        # --- Strategy 3: Fuzzy ementa match ---
        merged = False
        if not skip_fuzzy and result.ementa:
            normalized_incoming = _normalize(result.ementa)
            if normalized_incoming:  # skip if ementa normalizes to empty
                for i, existing in enumerate(output):
                    if not existing.ementa:
                        continue
                    normalized_existing = _normalize(existing.ementa)
                    if not normalized_existing:
                        continue

                    ratio = difflib.SequenceMatcher(
                        None, normalized_existing, normalized_incoming
                    ).ratio()

                    if ratio >= _FUZZY_THRESHOLD:
                        _merge(existing, result)
                        merged = True
                        break

        if not merged:
            idx = len(output)
            output.append(result)

            # Index for future lookups
            if result.id:
                seen_ids[result.id] = idx
            if tipo_lower and numero_lower:
                seen_tipo_num[key] = idx

    return output
```

### Performance Characteristics

| Result Count | ID/Tipo+Num Phase | Fuzzy Phase   | Total Expected |
|-------------|-------------------|---------------|----------------|
| 50          | < 1ms             | < 10ms        | < 15ms         |
| 200         | < 1ms             | < 100ms       | < 110ms        |
| 500         | < 1ms             | < 500ms       | < 510ms        |
| 1000        | < 1ms             | SKIPPED       | < 5ms          |

The O(n^2) fuzzy phase uses `SequenceMatcher` which is implemented in C in CPython, so constant factors are small. The 1000-item cutoff is a safety net; in practice, searches rarely return more than 300 combined results.

### Edge Cases

- **Empty results list:** Return empty list immediately.
- **All duplicates:** Return single merged record.
- **Empty ementa on one side:** Fuzzy match skipped for that pair; rely on ID/tipo+numero.
- **Empty tipo AND numero:** Skip tipo+numero strategy for that record (key `("", "")` is never stored).
- **Unicode in ementa:** Handled by `_normalize()` which strips accents.
- **None fields:** All field accesses use `or ""` guards to handle None gracefully.

---

## 4.2 excel_export.py -- Excel Generation with openpyxl

### Module Location

```
levantamento-normativos/
  excel_export.py
```

### Dependencies

- `openpyxl` (third-party, in requirements.txt) -- Excel workbook creation
- `io.BytesIO` (standard library) -- in-memory file buffer
- `models.py` -- NormativoResult dataclass

### Constants and Style Definitions

```python
from openpyxl.styles import (
    Alignment,
    Border,
    Font,
    PatternFill,
    Side,
    numbers,
)
from openpyxl.utils import get_column_letter

# -- Camara dos Deputados institutional green --
HEADER_FILL = PatternFill(start_color="4A8C4A", end_color="4A8C4A", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11, name="Calibri")
HEADER_ALIGNMENT = Alignment(
    horizontal="center", vertical="center", wrap_text=True
)

# -- Title row (merged across all columns) --
TITLE_FONT = Font(bold=True, size=14, color="2E7D32", name="Calibri")
TITLE_ALIGNMENT = Alignment(horizontal="center", vertical="center")
TITLE_FILL = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")

# -- Data row styles --
DATA_FONT = Font(size=10, name="Calibri")
DATA_ALIGNMENT = Alignment(vertical="top", wrap_text=False)
EMENTA_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
LINK_FONT = Font(color="2E7D32", underline="single", size=10, name="Calibri")

# -- Relevancia conditional fills --
RELEVANCIA_HIGH_FILL = PatternFill(
    start_color="C8E6C9", end_color="C8E6C9", fill_type="solid"
)  # green, >= 70%
RELEVANCIA_MED_FILL = PatternFill(
    start_color="FFF9C4", end_color="FFF9C4", fill_type="solid"
)  # yellow, >= 40%

# -- Thin border for all cells --
THIN_BORDER = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)

# -- Column definitions: (header_text, width, source_field) --
COLUMNS = [
    ("Nome do Normativo", 50, "nome"),
    ("Tipo", 20, "tipo"),
    ("Numero", 15, "numero"),
    ("Data", 14, "data"),
    ("Orgao Emissor", 30, "orgao_emissor"),
    ("Ementa", 60, "ementa"),
    ("Link", 40, "link"),
    ("Categoria/Tema", 25, "categoria"),
    ("Situacao", 15, "situacao"),
    ("Relevancia", 12, "relevancia"),
]

# Maximum ementa length in Excel cells. Longer values are truncated to
# prevent workbook bloat and cell rendering issues in older Excel versions.
_MAX_EMENTA_LENGTH = 5000
```

### Main Export Function

```python
from io import BytesIO
from openpyxl import Workbook
from models import NormativoResult


def generate_excel(results: list[NormativoResult], topic: str) -> BytesIO:
    """Generate a formatted Excel workbook from selected normativos.

    The workbook contains a single sheet named 'Normativos' with:
    - Row 1: merged title row showing the search topic
    - Row 2: green header row with white bold text
    - Rows 3+: data rows with formatting per column type
    - Freeze panes on row 3 (headers always visible)
    - Auto-filter on the header row
    - Hyperlinks in the Link column
    - Conditional formatting on the Relevancia column

    Args:
        results: List of NormativoResult objects to export. Must not be empty;
                 caller should validate before calling.
        topic: The search topic string, displayed in the title row and used
               to contextualize the workbook for stakeholders.

    Returns:
        BytesIO buffer containing the complete .xlsx file. The buffer is
        seeked to position 0, ready for direct use with
        st.download_button(data=buffer).

    Raises:
        ValueError: If results list is empty.
    """
    if not results:
        raise ValueError(
            "Cannot generate Excel from empty results list. "
            "Validate selection before calling generate_excel."
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "Normativos"

    num_columns = len(COLUMNS)

    # ----------------------------------------------------------------
    # Row 1: Title row (merged across all columns)
    # ----------------------------------------------------------------
    _write_title_row(ws, topic, num_columns)

    # ----------------------------------------------------------------
    # Row 2: Header row
    # ----------------------------------------------------------------
    _write_header_row(ws, num_columns)

    # ----------------------------------------------------------------
    # Rows 3+: Data rows
    # ----------------------------------------------------------------
    for row_idx, item in enumerate(results, start=3):
        _write_data_row(ws, row_idx, item)

    # ----------------------------------------------------------------
    # Column widths
    # ----------------------------------------------------------------
    for col_idx, (_, width, _) in enumerate(COLUMNS, start=1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width

    # ----------------------------------------------------------------
    # Freeze panes: freeze below row 2 (title + header always visible)
    # ----------------------------------------------------------------
    ws.freeze_panes = "A3"

    # ----------------------------------------------------------------
    # Auto-filter on header row (row 2)
    # ----------------------------------------------------------------
    last_col_letter = get_column_letter(num_columns)
    last_data_row = len(results) + 2  # +2 for title and header rows
    ws.auto_filter.ref = f"A2:{last_col_letter}{last_data_row}"

    # ----------------------------------------------------------------
    # Print settings for physical printing
    # ----------------------------------------------------------------
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0  # 0 = as many pages as needed vertically

    # ----------------------------------------------------------------
    # Write to BytesIO buffer
    # ----------------------------------------------------------------
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
```

### Helper: Title Row

```python
from openpyxl.utils import get_column_letter


def _write_title_row(ws, topic: str, num_columns: int) -> None:
    """Write the merged title row at row 1.

    Args:
        ws: Active worksheet.
        topic: Search topic to display in the title.
        num_columns: Total number of columns to merge across.
    """
    last_col = get_column_letter(num_columns)
    ws.merge_cells(f"A1:{last_col}1")

    title_cell = ws.cell(row=1, column=1)
    title_cell.value = f"Levantamento de Normativos: {topic}"
    title_cell.font = TITLE_FONT
    title_cell.alignment = TITLE_ALIGNMENT
    title_cell.fill = TITLE_FILL

    ws.row_dimensions[1].height = 40
```

### Helper: Header Row

```python
def _write_header_row(ws, num_columns: int) -> None:
    """Write the formatted header row at row 2.

    Args:
        ws: Active worksheet.
        num_columns: Total number of columns.
    """
    for col_idx, (header_text, _, _) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=2, column=col_idx)
        cell.value = header_text
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER

    ws.row_dimensions[2].height = 30
```

### Helper: Data Row

```python
def _write_data_row(ws, row_idx: int, item: NormativoResult) -> None:
    """Write a single data row for one NormativoResult.

    Handles per-column formatting including:
    - Date formatting (DD/MM/YYYY)
    - Ementa truncation and text wrapping
    - Hyperlink creation for the Link column
    - Percentage formatting and conditional fill for Relevancia

    Args:
        ws: Active worksheet.
        row_idx: The 1-based row number to write to.
        item: The NormativoResult to render.
    """
    for col_idx, (_, _, field_name) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=row_idx, column=col_idx)
        value = getattr(item, field_name, "") or ""

        # -- Per-field formatting --

        if field_name == "data":
            # Format date as DD/MM/YYYY string. The data field is already
            # a string in the model; we just ensure consistent display.
            cell.value = _format_date(value)
            cell.font = DATA_FONT
            cell.alignment = DATA_ALIGNMENT

        elif field_name == "ementa":
            # Truncate very long ementas to prevent workbook bloat
            if len(value) > _MAX_EMENTA_LENGTH:
                value = value[:_MAX_EMENTA_LENGTH] + "..."
            cell.value = value
            cell.font = DATA_FONT
            cell.alignment = EMENTA_ALIGNMENT
            # Set row height for rows with substantial ementas
            if len(value) > 100:
                ws.row_dimensions[row_idx].height = 60

        elif field_name == "link":
            # Create clickable hyperlink
            cell.value = value
            if value and value.startswith(("http://", "https://")):
                cell.hyperlink = value
            cell.font = LINK_FONT
            cell.alignment = DATA_ALIGNMENT

        elif field_name == "relevancia":
            # Format as percentage with conditional coloring
            numeric_value = float(value) if value else 0.0
            cell.value = numeric_value
            cell.number_format = "0%"
            cell.font = DATA_FONT
            cell.alignment = Alignment(horizontal="center", vertical="top")

            # Conditional fill based on relevance score
            if numeric_value >= 0.7:
                cell.fill = RELEVANCIA_HIGH_FILL
            elif numeric_value >= 0.4:
                cell.fill = RELEVANCIA_MED_FILL

        elif field_name == "nome":
            cell.value = value
            cell.font = Font(bold=True, size=10, name="Calibri")
            cell.alignment = Alignment(vertical="top", wrap_text=True)

        else:
            # Default: plain text cell
            cell.value = value
            cell.font = DATA_FONT
            cell.alignment = DATA_ALIGNMENT

        cell.border = THIN_BORDER
```

### Helper: Date Formatting

```python
import re as _re


def _format_date(date_str: str) -> str:
    """Normalize a date string to DD/MM/YYYY format.

    Handles common input formats:
    - YYYY-MM-DD (ISO format from APIs)
    - DD/MM/YYYY (already correct)
    - YYYY-MM-DDT... (ISO datetime, truncated to date)
    - Empty/None -> empty string

    Args:
        date_str: Raw date string from NormativoResult.data.

    Returns:
        Date formatted as DD/MM/YYYY, or the original string if parsing fails,
        or empty string if input is empty.
    """
    if not date_str:
        return ""

    date_str = date_str.strip()

    # Already in DD/MM/YYYY format
    if _re.match(r"^\d{2}/\d{2}/\d{4}$", date_str):
        return date_str

    # ISO format: YYYY-MM-DD or YYYY-MM-DDT...
    iso_match = _re.match(r"^(\d{4})-(\d{2})-(\d{2})", date_str)
    if iso_match:
        year, month, day = iso_match.groups()
        return f"{day}/{month}/{year}"

    # Unrecognized format: return as-is rather than losing data
    return date_str
```

### File Name Generation

The caller (Streamlit UI) generates the download filename using the topic:

```python
import re


def _make_filename_slug(topic: str) -> str:
    """Convert a topic string into a safe filename slug.

    Example: 'Governanca de TI no setor publico' -> 'governanca_de_ti_no_setor_publico'

    Args:
        topic: Raw topic string.

    Returns:
        Lowercase slug with only alphanumeric chars and underscores.
    """
    slug = topic.lower().strip()
    slug = re.sub(r"[^\w\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug)
    # Truncate to avoid filesystem path length issues
    return slug[:60]
```

Usage in Streamlit:

```python
filename = f"normativos_{_make_filename_slug(topic)}.xlsx"
```

### Acceptance Criteria

1. Generated `.xlsx` opens correctly in Microsoft Excel 2016+ and LibreOffice Calc 7+.
2. All 10 columns are populated with correct data from NormativoResult fields.
3. Hyperlinks in column G are clickable and navigate to the correct URL.
4. Header row (row 2) has green background (#4A8C4A) with white bold text.
5. Title row (row 1) is merged across all columns and displays the search topic.
6. Relevancia column shows values as percentages (e.g., "85%") with conditional coloring:
   - Green (#C8E6C9) for values >= 70%
   - Yellow (#FFF9C4) for values >= 40%
   - No fill for values < 40%
7. Sheet has freeze panes at A3 (title and headers always visible when scrolling).
8. Auto-filter dropdowns are enabled on the header row.
9. Ementa column has text wrapping enabled; rows with long ementas have height 60.
10. Ementas longer than 5000 characters are truncated with "..." suffix.
11. Dates display in DD/MM/YYYY format regardless of input format.
12. The BytesIO buffer is seeked to position 0 and can be passed directly to `st.download_button`.
13. Empty results list raises `ValueError` with descriptive message (caller must validate).
14. UTF-8 characters (Portuguese accents, special symbols) render correctly.
15. Print layout is set to landscape, fit-to-width for physical printing.

### Testing Notes

Unit tests should cover:

- `_normalize()` with accented text, mixed whitespace, empty string, None-like input.
- `_merge()` with all combinations of empty/non-empty fields.
- `deduplicate()` with exact ID matches, tipo+numero matches, fuzzy matches, and no matches.
- `deduplicate()` with more than 1000 items to verify fuzzy matching is skipped.
- `generate_excel()` with typical results, verifying cell values, styles, and hyperlinks.
- `generate_excel()` with empty list (should raise ValueError).
- `_format_date()` with ISO, DD/MM/YYYY, datetime, and empty input.
- Round-trip: generate Excel, read it back with openpyxl, verify cell contents match input.
