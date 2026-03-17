# Phase 1: Foundation & Project Setup

## Overview

This document specifies Phase 1 of the "Levantamento de Normativos" Streamlit application. Phase 1 establishes the project skeleton: directory structure, dependency manifest, theme configuration, data models, and a navigable wizard scaffold with placeholder steps.

After completing this phase, running `streamlit run levantamento-normativos/app.py` from the repository root must display a fully themed, five-step wizard with working navigation, the Camara dos Deputados green/gold visual identity, and importable data models.

---

## 1.1 Directory Structure

Create the following tree under the repository root (`C:/Users/Rodrigo/Documents/projeto-nuati-normativos-levantamento/`). Every file listed must exist. Files marked **(empty placeholder)** should contain only the minimum content shown.

```
levantamento-normativos/
    .streamlit/
        config.toml
        secrets.toml.example
    searchers/
        __init__.py
        base.py
        lexml_searcher.py
        tcu_searcher.py
        google_searcher.py
    llm/
        __init__.py
        gemini_client.py
    app.py
    models.py
    deduplicator.py
    excel_export.py
    requirements.txt
```

### Placeholder file contents

Files that are not fully specified in this phase must still be valid Python so that imports do not break.

**`searchers/__init__.py`**
```python
"""Search engine integrations for Levantamento de Normativos."""
```

**`searchers/base.py`**
```python
"""Base class for all searchers. Implemented in Phase 3."""
```

**`searchers/lexml_searcher.py`**
```python
"""LexML SRU/CQL searcher. Implemented in Phase 3."""
```

**`searchers/tcu_searcher.py`**
```python
"""TCU Jurisprudencia searcher. Implemented in Phase 3."""
```

**`searchers/google_searcher.py`**
```python
"""Google Search searcher. Implemented in Phase 3."""
```

**`llm/__init__.py`**
```python
"""LLM integration layer."""
```

**`llm/gemini_client.py`**
```python
"""Gemini Flash client for keyword generation and categorization. Implemented in Phase 2."""
```

**`deduplicator.py`**
```python
"""Result deduplication logic. Implemented in Phase 4."""
```

**`excel_export.py`**
```python
"""Excel/XLSX export via openpyxl. Implemented in Phase 5."""
```

---

## 1.2 requirements.txt

Create `levantamento-normativos/requirements.txt` with this exact content (no trailing whitespace, single trailing newline):

```
streamlit>=1.33.0
requests
openpyxl
google-generativeai
googlesearch-python
```

### Rationale

| Package                | Purpose                                                    |
|------------------------|------------------------------------------------------------|
| `streamlit>=1.33.0`   | UI framework. 1.33+ required for `st.fragment` and stable `st.status`. |
| `requests`             | HTTP client for LexML SRU and TCU API calls.               |
| `openpyxl`             | Write `.xlsx` exports with formatting.                     |
| `google-generativeai`  | Official Google Gemini SDK (keyword generation, categorization). |
| `googlesearch-python`  | Lightweight Google Search scraper (no API key required).   |

---

## 1.3 .streamlit/config.toml

Create `levantamento-normativos/.streamlit/config.toml` with this exact content:

```toml
[theme]
primaryColor = "#4CAF50"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#f4f8f4"
textColor = "#1a1a1a"
font = "sans serif"
```

This mirrors the lighter variant of the Camara identity used in the existing DOU clipping app's `config.toml`.

---

## 1.4 .streamlit/secrets.toml.example

Create `levantamento-normativos/.streamlit/secrets.toml.example`:

```toml
GEMINI_API_KEY = "your-gemini-flash-api-key-here"
# Optional: for Google Custom Search API (alternative to googlesearch-python)
# GOOGLE_API_KEY = ""
# GOOGLE_CSE_ID = ""
```

**Important:** Do NOT create an actual `secrets.toml` file. The `.example` suffix signals that the developer must copy and fill in real keys. A `.gitignore` entry for `secrets.toml` should be added at the project root (or inside `levantamento-normativos/`) to prevent accidental commits of real secrets.

---

## 1.5 models.py -- Full Specification

Create `levantamento-normativos/models.py`. This file defines the two core data classes used throughout the application.

### Complete source code

```python
"""
Modelos de dados para o Levantamento de Normativos.

Define as dataclasses centrais usadas em todo o pipeline:
- NormativoResult: um normativo encontrado por qualquer fonte de busca.
- SearchConfig: parametros de configuracao de uma sessao de busca.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class NormativoResult:
    """Representa um normativo ou ato encontrado durante a busca.

    O campo ``id`` e gerado automaticamente em ``__post_init__`` como o
    hash SHA-256 da concatenacao ``tipo|numero|data``.  Esse hash e usado
    para deduplicacao de resultados vindos de fontes diferentes.

    Attributes:
        id: SHA-256 hex digest de ``f"{tipo}|{numero}|{data}"``.
             Gerado automaticamente -- nao passar no construtor.
        nome: Nome completo do normativo.
              Ex: "Lei n. 13.709, de 14 de agosto de 2018 (LGPD)".
        tipo: Categoria do ato.  Valores esperados:
              "Lei", "Decreto", "Instrucao Normativa", "Portaria",
              "Acordao TCU", "Resolucao", "Framework/Padrao", "Outro".
        numero: Numero do ato (ex: "13.709").  String vazia se nao aplicavel.
        data: Data no formato DD/MM/AAAA, ou ``None`` se desconhecida.
        orgao_emissor: Orgao emissor.
              Ex: "Presidencia da Republica", "TCU", "ISACA".
        ementa: Resumo ou descricao do conteudo.
        link: URL para o documento original.
        categoria: Tema ou categoria atribuida. Default "Nao categorizado".
        situacao: Vigencia do normativo.
              "Vigente", "Revogado" ou "Nao identificado" (default).
        relevancia: Score de relevancia entre 0.0 e 1.0. Default 0.0.
        source: Identificador da fonte de busca.
              "lexml", "tcu" ou "google".
        found_by: Palavra-chave que originou o resultado.
        raw_data: Resposta bruta da API de origem. Default ``{}``.
    """

    # Campos obrigatorios (sem default) --------------------------------
    nome: str
    tipo: str
    numero: str
    data: str | None
    orgao_emissor: str
    ementa: str
    link: str
    source: str
    found_by: str

    # Campos com default ------------------------------------------------
    id: str = field(default="", init=False)
    categoria: str = "Nao categorizado"
    situacao: str = "Nao identificado"
    relevancia: float = 0.0
    raw_data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Gera o ``id`` a partir de tipo, numero e data."""
        raw = f"{self.tipo}|{self.numero}|{self.data}"
        self.id = hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class SearchConfig:
    """Configuracao de uma sessao de busca.

    Attributes:
        topic: Descricao em linguagem natural do tema de pesquisa.
        keywords: Lista final de palavras-chave para busca.
        sources: Fontes selecionadas.  Default ``["lexml", "tcu", "google"]``.
        max_results_per_source: Limite de resultados por fonte. Default 50.
    """

    topic: str = ""
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=lambda: ["lexml", "tcu", "google"])
    max_results_per_source: int = 50
```

### Design decisions

1. **`id` is `init=False`**: Callers must never pass `id` explicitly. It is always derived from `tipo`, `numero`, and `data`. This guarantees that the same normativo found via different sources produces the same hash, enabling deduplication.

2. **`data` is `str | None`**: Dates are kept as display strings (`DD/MM/AAAA`) rather than `datetime` objects because the sources return heterogeneous formats and some results have no date. Parsing is deferred to export time.

3. **`raw_data` uses `field(default_factory=dict)`**: Avoids the mutable default argument pitfall.

4. **`from __future__ import annotations`**: Enables `str | None` and `list[str]` syntax on Python 3.9, which some users may still have.

### Verification

After implementation, the following must work without errors in a Python 3.10+ REPL:

```python
from models import NormativoResult, SearchConfig

r = NormativoResult(
    nome="Lei n. 13.709/2018 (LGPD)",
    tipo="Lei",
    numero="13.709",
    data="14/08/2018",
    orgao_emissor="Presidencia da Republica",
    ementa="Dispoe sobre a protecao de dados pessoais.",
    link="https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm",
    source="lexml",
    found_by="LGPD",
)
assert len(r.id) == 64  # SHA-256 hex digest
assert r.categoria == "Nao categorizado"
assert r.situacao == "Nao identificado"
assert r.relevancia == 0.0
assert r.raw_data == {}

# Two results with the same tipo|numero|data produce the same id
r2 = NormativoResult(
    nome="LGPD",
    tipo="Lei",
    numero="13.709",
    data="14/08/2018",
    orgao_emissor="Congresso Nacional",
    ementa="Lei Geral de Protecao de Dados.",
    link="https://example.com",
    source="google",
    found_by="protecao de dados",
)
assert r.id == r2.id

cfg = SearchConfig(topic="governanca de TI", keywords=["COBIT", "ITIL"])
assert cfg.sources == ["lexml", "tcu", "google"]
assert cfg.max_results_per_source == 50
```

---

## 1.6 app.py -- Full Specification

Create `levantamento-normativos/app.py`. This is the Streamlit entry point. It renders a five-step wizard with a themed sidebar, step navigation, and placeholder content for each step.

### 1.6.1 Page configuration

Must be the very first Streamlit call in the file (before any other `st.*`):

```python
st.set_page_config(
    page_title="Levantamento de Normativos - NUATI",
    layout="wide",
    page_icon="https://www2.camara.leg.br/favicon.ico",
)
```

### 1.6.2 CSS block

Inject a single `st.markdown(..., unsafe_allow_html=True)` call immediately after page config. The CSS applies the Camara dos Deputados green/gold identity derived from the existing DOU clipping app (`app_camara.py`), adapted for a lighter main area (white background) with a dark green sidebar.

```python
st.markdown("""
<style>
    /* ----- Header: verde escuro com faixa dourada na base ----- */
    header[data-testid="stHeader"] {
        background: linear-gradient(
            to bottom,
            #4a8c4a 0%, #4a8c4a 92%,
            #c8a415 92%, #c8a415 100%
        ) !important;
    }

    /* ----- Sidebar: fundo verde claro, borda dourada ----- */
    section[data-testid="stSidebar"] {
        background-color: #e8f0e8 !important;
        border-right: 3px solid #c8a415 !important;
    }

    /* ----- Botao primario ----- */
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="stBaseButton-primary"] {
        background-color: #4CAF50 !important;
        border-color: #4CAF50 !important;
        color: white !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="stBaseButton-primary"]:hover {
        background-color: #388E3C !important;
        border-color: #388E3C !important;
    }

    /* ----- Botoes secundarios ----- */
    .stButton > button:not([kind="primary"]):not([data-testid="stBaseButton-primary"]) {
        border-color: #4CAF50 !important;
        color: #2e7d32 !important;
    }

    /* ----- Links ----- */
    a { color: #2e7d32 !important; }
    a:hover { color: #1b5e20 !important; }

    /* ----- Expanders ----- */
    details[data-testid="stExpander"] {
        border-color: #c8a41566 !important;
    }

    /* ----- Dividers ----- */
    .stDivider { border-color: #c8a41544 !important; }
</style>
""", unsafe_allow_html=True)
```

### 1.6.3 Session state initialization

Immediately after the CSS block, initialize session state with safe defaults. Use a dict-based loop so that adding new keys later is trivial:

```python
_DEFAULTS: dict = {
    "wizard_step": 1,
    "topic": "",
    "input_mode": "topic",        # "topic" or "keywords"
    "keywords": [],
    "selected_sources": ["lexml", "tcu", "google"],
    "max_results": 50,
    "results": [],                 # list[NormativoResult]
    "search_done": False,
    "excel_buffer": None,
}

for _key, _val in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val
```

**Important:** The loop checks `if _key not in st.session_state` so that user-modified values survive Streamlit reruns. Do NOT unconditionally overwrite.

### 1.6.4 Navigation helpers

Define these three functions before the sidebar/main-area code. They are used as `on_click` callbacks on navigation buttons.

```python
def next_step() -> None:
    """Avanca para o proximo passo do wizard (max 5)."""
    st.session_state["wizard_step"] = min(st.session_state["wizard_step"] + 1, 5)


def prev_step() -> None:
    """Volta para o passo anterior do wizard (min 1)."""
    st.session_state["wizard_step"] = max(st.session_state["wizard_step"] - 1, 1)


def go_to_step(n: int) -> None:
    """Pula diretamente para o passo ``n`` (clamped entre 1 e 5)."""
    st.session_state["wizard_step"] = max(1, min(n, 5))
```

### 1.6.5 Sidebar

The sidebar contains: (a) a branded title block, and (b) a step-progress indicator.

```python
# -----------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------
_STEPS: list[str] = [
    "Definir Tema",
    "Palavras-chave",
    "Fontes e Busca",
    "Revisar Resultados",
    "Exportar",
]

with st.sidebar:
    # --- Branding ---
    st.markdown(
        '<h2 style="color:#2e7d32;margin-bottom:0;">Levantamento de Normativos</h2>'
        '<p style="color:#c8a415;font-size:14px;margin-top:0;">'
        'NUATI &middot; C&acirc;mara dos Deputados</p>',
        unsafe_allow_html=True,
    )

    st.divider()

    # --- Step progress ---
    current = st.session_state["wizard_step"]
    lines: list[str] = []
    for i, label in enumerate(_STEPS, start=1):
        if i == current:
            # Current step: green filled circle, bold text
            lines.append(
                f'<p style="margin:4px 0;font-size:15px;">'
                f'<span style="color:#4CAF50;font-size:18px;">&#9679;</span> '
                f'<b>Passo {i}:</b> {label}</p>'
            )
        elif i < current:
            # Completed step: green check
            lines.append(
                f'<p style="margin:4px 0;font-size:14px;color:#888;">'
                f'<span style="color:#4CAF50;">&#10003;</span> '
                f'Passo {i}: {label}</p>'
            )
        else:
            # Future step: dimmed circle
            lines.append(
                f'<p style="margin:4px 0;font-size:14px;color:#aaa;">'
                f'<span style="font-size:18px;">&#9675;</span> '
                f'Passo {i}: {label}</p>'
            )

    st.markdown("".join(lines), unsafe_allow_html=True)
```

#### Step indicator rendering rules

| Step status  | Symbol | Color    | Text style |
|-------------|--------|----------|------------|
| Current      | `●` (&#9679;)  | `#4CAF50` | Bold, 15px |
| Completed    | `✓` (&#10003;) | `#4CAF50` | Normal, 14px, gray text |
| Future       | `○` (&#9675;)  | inherit   | Normal, 14px, light gray |

### 1.6.6 Step rendering functions

Define one function per step. In Phase 1, each is a placeholder. Later phases will replace the body of each function.

```python
def render_step1() -> None:
    """Passo 1: Definir Tema."""
    st.header("Passo 1 - Definir Tema")
    st.info("Em construcao.")
    st.button("Proximo", on_click=next_step, type="primary")


def render_step2() -> None:
    """Passo 2: Palavras-chave."""
    st.header("Passo 2 - Palavras-chave")
    st.info("Em construcao.")
    col_prev, col_next = st.columns(2)
    with col_prev:
        st.button("Voltar", on_click=prev_step)
    with col_next:
        st.button("Proximo", on_click=next_step, type="primary")


def render_step3() -> None:
    """Passo 3: Fontes e Busca."""
    st.header("Passo 3 - Fontes e Busca")
    st.info("Em construcao.")
    col_prev, col_next = st.columns(2)
    with col_prev:
        st.button("Voltar", on_click=prev_step)
    with col_next:
        st.button("Proximo", on_click=next_step, type="primary")


def render_step4() -> None:
    """Passo 4: Revisar Resultados."""
    st.header("Passo 4 - Revisar Resultados")
    st.info("Em construcao.")
    col_prev, col_next = st.columns(2)
    with col_prev:
        st.button("Voltar", on_click=prev_step)
    with col_next:
        st.button("Proximo", on_click=next_step, type="primary")


def render_step5() -> None:
    """Passo 5: Exportar."""
    st.header("Passo 5 - Exportar")
    st.info("Em construcao.")
    st.button("Voltar ao inicio", on_click=lambda: go_to_step(1))
```

**Navigation button rules:**

- Step 1: only "Proximo" (primary).
- Steps 2-4: "Voltar" (secondary, left column) and "Proximo" (primary, right column).
- Step 5: "Voltar ao inicio" (secondary) resets to step 1.

### 1.6.7 Main area: step router

After the sidebar block, render the current step:

```python
# -----------------------------------------------------------------------
# Main area: render the active wizard step
# -----------------------------------------------------------------------
step = st.session_state["wizard_step"]

if step == 1:
    render_step1()
elif step == 2:
    render_step2()
elif step == 3:
    render_step3()
elif step == 4:
    render_step4()
elif step == 5:
    render_step5()
```

Use `if/elif` rather than a dict dispatch for readability and because Streamlit widget keys must not collide across steps.

### 1.6.8 Footer

At the very end of the file, after the step router:

```python
# -----------------------------------------------------------------------
# Footer
# -----------------------------------------------------------------------
st.markdown("---")
st.markdown(
    '<p style="text-align:center;color:#888;font-size:13px;">'
    'Feito por Rodrigo Pinto &middot; NUATI &middot; '
    'C&acirc;mara dos Deputados</p>',
    unsafe_allow_html=True,
)
```

### 1.6.9 Complete file outline (for reference)

The final `app.py` must contain these sections in this order:

1. Module docstring
2. Imports (`streamlit`)
3. `st.set_page_config(...)` -- MUST be first Streamlit call
4. CSS injection via `st.markdown`
5. Session state initialization loop
6. Navigation helpers (`next_step`, `prev_step`, `go_to_step`)
7. Step constants (`_STEPS`) and sidebar block
8. Step render functions (`render_step1` through `render_step5`)
9. Main area step router
10. Footer

**Note on import order:** `models.py` is NOT imported in Phase 1's `app.py` because no step logic uses models yet. The import will be added in Phase 2 when step 1 needs `SearchConfig`.

---

## 1.7 Acceptance Criteria

Every criterion below must pass before Phase 1 is considered complete.

### AC-1: Application launches

```bash
cd C:/Users/Rodrigo/Documents/projeto-nuati-normativos-levantamento
streamlit run levantamento-normativos/app.py
```

The app must open in the browser without errors. No stack traces in the terminal.

### AC-2: All five steps render

Clicking "Proximo" from step 1 must cycle through all five steps. Each step displays its header ("Passo N - Name") and the placeholder `st.info("Em construcao.")`.

### AC-3: Sidebar step progress

The sidebar must show all five step labels. The current step has a green filled circle and bold text. Completed steps show a green checkmark. Future steps show a dimmed hollow circle.

### AC-4: Theme is applied

- The header bar has a green gradient with a gold stripe at the bottom.
- The sidebar has a light green background (`#e8f0e8`) with a gold right border.
- Primary buttons are green (`#4CAF50`).
- Secondary buttons have green border and dark green text.

### AC-5: Navigation works correctly

- Step 1: only "Proximo" button visible. Clicking it goes to step 2.
- Steps 2-4: "Voltar" and "Proximo" buttons. Both work correctly.
- Step 5: "Voltar ao inicio" button resets to step 1.
- No step goes below 1 or above 5.

### AC-6: Models importable

From the `levantamento-normativos/` directory:

```python
python -c "
from models import NormativoResult, SearchConfig
r = NormativoResult(
    nome='Test', tipo='Lei', numero='1', data='01/01/2024',
    orgao_emissor='Test', ementa='Test', link='http://test.com',
    source='lexml', found_by='test'
)
print('ID length:', len(r.id))
assert len(r.id) == 64
c = SearchConfig()
assert c.sources == ['lexml', 'tcu', 'google']
print('Models OK')
"
```

Must print `Models OK` with no errors.

### AC-7: All placeholder files exist and are importable

```bash
cd levantamento-normativos
python -c "import searchers; import llm; import deduplicator; import excel_export; print('All imports OK')"
```

### AC-8: No secrets committed

The file `levantamento-normativos/.streamlit/secrets.toml` must NOT exist. Only `secrets.toml.example` exists.

---

## Appendix A: Complete app.py Source

For absolute clarity, the complete file is provided below. The developer should create this file verbatim.

```python
"""
Levantamento de Normativos - NUATI
Aplicacao Streamlit para busca e consolidacao de normativos brasileiros.
Fase 1: Scaffold do wizard com navegacao e identidade visual.
"""

import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Levantamento de Normativos - NUATI",
    layout="wide",
    page_icon="https://www2.camara.leg.br/favicon.ico",
)

# ---------------------------------------------------------------------------
# CSS — Identidade visual da Camara dos Deputados (verde/dourado)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Header: verde com faixa dourada na base */
    header[data-testid="stHeader"] {
        background: linear-gradient(
            to bottom,
            #4a8c4a 0%, #4a8c4a 92%,
            #c8a415 92%, #c8a415 100%
        ) !important;
    }

    /* Sidebar: fundo verde claro, borda dourada */
    section[data-testid="stSidebar"] {
        background-color: #e8f0e8 !important;
        border-right: 3px solid #c8a415 !important;
    }

    /* Botao primario */
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="stBaseButton-primary"] {
        background-color: #4CAF50 !important;
        border-color: #4CAF50 !important;
        color: white !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="stBaseButton-primary"]:hover {
        background-color: #388E3C !important;
        border-color: #388E3C !important;
    }

    /* Botoes secundarios */
    .stButton > button:not([kind="primary"]):not([data-testid="stBaseButton-primary"]) {
        border-color: #4CAF50 !important;
        color: #2e7d32 !important;
    }

    /* Links */
    a { color: #2e7d32 !important; }
    a:hover { color: #1b5e20 !important; }

    /* Expanders */
    details[data-testid="stExpander"] {
        border-color: #c8a41566 !important;
    }

    /* Dividers */
    .stDivider { border-color: #c8a41544 !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
_DEFAULTS: dict = {
    "wizard_step": 1,
    "topic": "",
    "input_mode": "topic",
    "keywords": [],
    "selected_sources": ["lexml", "tcu", "google"],
    "max_results": 50,
    "results": [],
    "search_done": False,
    "excel_buffer": None,
}

for _key, _val in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val

# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def next_step() -> None:
    """Avanca para o proximo passo do wizard (max 5)."""
    st.session_state["wizard_step"] = min(st.session_state["wizard_step"] + 1, 5)


def prev_step() -> None:
    """Volta para o passo anterior do wizard (min 1)."""
    st.session_state["wizard_step"] = max(st.session_state["wizard_step"] - 1, 1)


def go_to_step(n: int) -> None:
    """Pula diretamente para o passo ``n`` (clamped entre 1 e 5)."""
    st.session_state["wizard_step"] = max(1, min(n, 5))

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
_STEPS: list[str] = [
    "Definir Tema",
    "Palavras-chave",
    "Fontes e Busca",
    "Revisar Resultados",
    "Exportar",
]

with st.sidebar:
    st.markdown(
        '<h2 style="color:#2e7d32;margin-bottom:0;">Levantamento de Normativos</h2>'
        '<p style="color:#c8a415;font-size:14px;margin-top:0;">'
        'NUATI &middot; C&acirc;mara dos Deputados</p>',
        unsafe_allow_html=True,
    )

    st.divider()

    current = st.session_state["wizard_step"]
    lines: list[str] = []
    for i, label in enumerate(_STEPS, start=1):
        if i == current:
            lines.append(
                f'<p style="margin:4px 0;font-size:15px;">'
                f'<span style="color:#4CAF50;font-size:18px;">&#9679;</span> '
                f'<b>Passo {i}:</b> {label}</p>'
            )
        elif i < current:
            lines.append(
                f'<p style="margin:4px 0;font-size:14px;color:#888;">'
                f'<span style="color:#4CAF50;">&#10003;</span> '
                f'Passo {i}: {label}</p>'
            )
        else:
            lines.append(
                f'<p style="margin:4px 0;font-size:14px;color:#aaa;">'
                f'<span style="font-size:18px;">&#9675;</span> '
                f'Passo {i}: {label}</p>'
            )

    st.markdown("".join(lines), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Step render functions
# ---------------------------------------------------------------------------

def render_step1() -> None:
    """Passo 1: Definir Tema."""
    st.header("Passo 1 - Definir Tema")
    st.info("Em construcao.")
    st.button("Proximo", on_click=next_step, type="primary")


def render_step2() -> None:
    """Passo 2: Palavras-chave."""
    st.header("Passo 2 - Palavras-chave")
    st.info("Em construcao.")
    col_prev, col_next = st.columns(2)
    with col_prev:
        st.button("Voltar", on_click=prev_step)
    with col_next:
        st.button("Proximo", on_click=next_step, type="primary")


def render_step3() -> None:
    """Passo 3: Fontes e Busca."""
    st.header("Passo 3 - Fontes e Busca")
    st.info("Em construcao.")
    col_prev, col_next = st.columns(2)
    with col_prev:
        st.button("Voltar", on_click=prev_step)
    with col_next:
        st.button("Proximo", on_click=next_step, type="primary")


def render_step4() -> None:
    """Passo 4: Revisar Resultados."""
    st.header("Passo 4 - Revisar Resultados")
    st.info("Em construcao.")
    col_prev, col_next = st.columns(2)
    with col_prev:
        st.button("Voltar", on_click=prev_step)
    with col_next:
        st.button("Proximo", on_click=next_step, type="primary")


def render_step5() -> None:
    """Passo 5: Exportar."""
    st.header("Passo 5 - Exportar")
    st.info("Em construcao.")
    st.button("Voltar ao inicio", on_click=lambda: go_to_step(1))

# ---------------------------------------------------------------------------
# Main area: render the active wizard step
# ---------------------------------------------------------------------------
step = st.session_state["wizard_step"]

if step == 1:
    render_step1()
elif step == 2:
    render_step2()
elif step == 3:
    render_step3()
elif step == 4:
    render_step4()
elif step == 5:
    render_step5()

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    '<p style="text-align:center;color:#888;font-size:13px;">'
    'Feito por Rodrigo Pinto &middot; NUATI &middot; '
    'C&acirc;mara dos Deputados</p>',
    unsafe_allow_html=True,
)
```

---

## Appendix B: Dependency Installation

Before running the app for the first time:

```bash
cd C:/Users/Rodrigo/Documents/projeto-nuati-normativos-levantamento/levantamento-normativos
pip install -r requirements.txt
```

If using a virtual environment (recommended):

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -r requirements.txt
```

---

## Appendix C: Phase Roadmap (for context only)

| Phase | Focus                          | Key deliverables                        |
|-------|--------------------------------|-----------------------------------------|
| **1** | **Foundation & Project Setup** | Directory structure, models, wizard scaffold (this document) |
| 2     | LLM Integration                | Gemini client, keyword generation/refinement |
| 3     | Search Engines                 | LexML, TCU, Google searchers            |
| 4     | Deduplication & Review         | Result merging, dedup, review UI        |
| 5     | Export                         | Excel export with formatting            |
| 6     | Polish & Deploy                | Error handling, caching, documentation  |
