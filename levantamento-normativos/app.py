"""
Levantamento de Normativos - NUATI
Aplicacao Streamlit para busca e consolidacao de normativos brasileiros.

Wizard de 5 passos:
1. Definir Tema — descricao do tema ou palavras-chave manuais
2. Revisar Palavras-chave — edicao das keywords geradas pela IA
3. Fontes e Busca — selecao de fontes e execucao da busca
4. Revisar Resultados — filtragem, ordenacao e selecao
5. Exportar — geracao e download do arquivo Excel
"""

import html as html_module
import logging
import re as _re
from collections import Counter

import pandas as pd
import streamlit as st

from models import NormativoResult
from searchers import LexMLSearcher, TCUSearcher, GoogleSearcher
from llm import gemini_client
from llm.gemini_client import is_available as llm_available
from deduplicator import deduplicate
from excel_export import generate_excel

# ---------------------------------------------------------------------------
# Logging configuration (must run before any logger is used)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Reduce noise from third-party libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

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
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS: dict = {
    "wizard_step": 1,
    "topic": "",
    "input_mode": "topic",
    # Step 1 (keywords mode)
    "raw_keywords": [],
    # Step 2 (topic mode)
    "llm_keywords": [],
    "edited_keywords": [],
    "keywords_generated": False,
    # Step 3
    "search_done": False,
    "results": [],
    # Step 5
    "excel_buffer": None,
}

for _key, _val in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val

# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------


def go_to_step(n: int) -> None:
    """Pula diretamente para o passo ``n`` (clamped entre 1 e 5)."""
    st.session_state["wizard_step"] = max(1, min(n, 5))


def get_current_keywords() -> list[str]:
    """Return the active keyword list regardless of input mode.

    Returns:
        The keyword list from Step 2 edits (topic mode) or
        Step 1 manual entry (keywords mode). Empty list if
        no keywords have been set yet.
    """
    if st.session_state.get("input_mode") == "keywords":
        return st.session_state.get("raw_keywords", [])
    return st.session_state.get("edited_keywords", [])


# ---------------------------------------------------------------------------
# Tipo badge colors for Step 4
# ---------------------------------------------------------------------------

TIPO_COLORS = {
    "Lei": "#1976D2",
    "Lei Complementar": "#1565C0",
    "Decreto": "#7B1FA2",
    "Instrucao Normativa": "#F57C00",
    "Portaria": "#0097A7",
    "Acordao TCU": "#D32F2F",
    "Resolucao": "#388E3C",
    "Framework/Padrao": "#5D4037",
    "Medida Provisoria": "#C62828",
    "Norma Complementar": "#00695C",
    "Outro": "#757575",
}


def _get_tipo_color(tipo: str) -> str:
    """Get the badge background color for a normativo type."""
    return TIPO_COLORS.get(tipo, "#757575")


# ---------------------------------------------------------------------------
# Filename slug helper for Step 5
# ---------------------------------------------------------------------------


def _make_filename_slug(topic: str) -> str:
    """Convert a topic string into a filesystem-safe filename slug.

    Transforms to lowercase, removes special characters, replaces whitespace
    with underscores, and truncates to 60 characters for Windows path safety.
    """
    slug = topic.lower().strip()
    slug = _re.sub(r"[^\w\s]", "", slug)
    slug = _re.sub(r"\s+", "_", slug)
    return slug[:60]


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


# ===========================================================================
# Step 1: Definir Tema
# ===========================================================================


def render_step1() -> None:
    """Passo 1: Definir Tema do Levantamento.

    Two input modes:
    - Topic mode: user describes a broad topic; LLM expands to keywords in Step 2.
    - Keywords mode: user provides explicit keywords; Step 2 is skipped.
    """
    st.header("Passo 1 - Definir Tema do Levantamento")
    st.markdown(
        "Descreva o tema de pesquisa ou insira palavras-chave diretamente. "
        "O sistema buscara normativos relacionados em multiplas fontes."
    )

    input_mode = st.radio(
        "Modo de entrada:",
        [
            "Descrever tema (IA expande palavras-chave)",
            "Inserir palavras-chave manualmente",
        ],
        index=0,
        key="input_mode_radio",
        horizontal=True,
    )

    if "Descrever tema" in input_mode:
        st.session_state["input_mode"] = "topic"

        topic = st.text_input(
            "Descreva o tema:",
            value=st.session_state.get("topic", ""),
            placeholder="Ex: Governanca de TI no setor publico federal",
            key="topic_input",
        )

        st.caption(
            "A IA ira expandir o tema em palavras-chave de busca no proximo passo."
        )

        # Warn if LLM is not configured
        if not llm_available():
            st.info(
                "Chave da API Gemini nao configurada. O modo de tema requer a IA. "
                "Use o modo manual ou configure GEMINI_API_KEY em "
                ".streamlit/secrets.toml"
            )

        can_proceed = bool(topic and topic.strip())
        st.session_state["topic"] = topic.strip() if topic else ""

    else:
        st.session_state["input_mode"] = "keywords"

        keywords_text = st.text_area(
            "Palavras-chave (uma por linha):",
            height=200,
            placeholder=(
                "governanca de TI\n"
                "seguranca da informacao\n"
                "LGPD\n"
                "protecao de dados\n"
                "COBIT"
            ),
            key="manual_keywords_input",
        )

        # Parse keywords: split by newline, strip whitespace, remove empty lines
        parsed = [
            kw.strip()
            for kw in (keywords_text or "").split("\n")
            if kw.strip()
        ]

        st.caption(f"{len(parsed)} palavras-chave inseridas")
        can_proceed = len(parsed) > 0
        st.session_state["raw_keywords"] = parsed

        # In keywords mode, set a descriptive topic for Excel export
        if parsed:
            st.session_state["topic"] = parsed[0]

    # Navigation
    st.divider()
    col_spacer, col_next = st.columns([4, 1])
    with col_next:
        if st.button(
            "Proximo >>",
            disabled=not can_proceed,
            type="primary",
            use_container_width=True,
        ):
            if st.session_state["input_mode"] == "keywords":
                # Skip Step 2 (keyword review) — go directly to source selection
                go_to_step(3)
            else:
                go_to_step(2)
            st.rerun()


# ===========================================================================
# Step 2: Revisar Palavras-chave
# ===========================================================================


def _generate_keywords_if_needed() -> None:
    """Call LLM to expand topic into keywords, but only on first visit.

    Uses session_state['keywords_generated'] as a guard to prevent
    re-calling the LLM on every Streamlit rerun.
    """
    if st.session_state.get("keywords_generated"):
        return  # Already generated, skip

    topic = st.session_state.get("topic", "")
    if not topic:
        return

    if not llm_available():
        st.session_state["llm_keywords"] = []
        st.session_state["edited_keywords"] = []
        st.session_state["keywords_generated"] = True
        return

    with st.spinner("Gerando palavras-chave com IA..."):
        keywords = gemini_client.expand_topic_to_keywords(topic)

    st.session_state["llm_keywords"] = keywords[:]  # Store copy for restore
    st.session_state["edited_keywords"] = keywords[:]
    st.session_state["keywords_generated"] = True


def render_step2() -> None:
    """Passo 2: Revisar e editar palavras-chave geradas pela IA."""

    st.header("Passo 2 - Revisar Palavras-chave")

    topic = st.session_state.get("topic", "")
    st.caption(f"Tema: {topic}")

    # Generate keywords on first visit
    _generate_keywords_if_needed()

    # Status message
    keywords = st.session_state.get("edited_keywords", [])
    if llm_available() and keywords:
        llm_count = len(st.session_state.get("llm_keywords", []))
        st.success(f"IA gerou {llm_count} palavras-chave a partir do tema.")
    elif not llm_available():
        st.warning(
            "IA nao disponivel. Insira palavras-chave manualmente abaixo."
        )
    elif not keywords:
        st.warning("Nenhuma palavra-chave gerada. Insira manualmente abaixo.")

    # Editable keyword text area
    current_text = "\n".join(keywords)
    edited_text = st.text_area(
        "Editar palavras-chave (uma por linha):",
        value=current_text,
        height=300,
        key="kw_editor",
    )

    # Action buttons row
    col1, col2, col3 = st.columns(3)

    with col1:
        regen_disabled = not llm_available()
        if st.button(
            "Regenerar com IA",
            disabled=regen_disabled,
            use_container_width=True,
            help="Chamar a IA novamente para gerar novas palavras-chave",
        ):
            st.session_state["keywords_generated"] = False
            st.rerun()

    with col2:
        if st.button("Limpar", use_container_width=True):
            st.session_state["edited_keywords"] = []
            st.rerun()

    with col3:
        has_original = bool(st.session_state.get("llm_keywords"))
        if st.button(
            "Restaurar",
            disabled=not has_original,
            use_container_width=True,
            help="Restaurar as palavras-chave originais geradas pela IA",
        ):
            st.session_state["edited_keywords"] = (
                st.session_state["llm_keywords"][:]
            )
            st.rerun()

    # Parse current editor content
    current_keywords = [
        kw.strip() for kw in edited_text.split("\n") if kw.strip()
    ]

    st.metric("Total de palavras-chave", len(current_keywords))

    # Navigation
    st.divider()
    col_prev, col_spacer, col_next = st.columns([1, 3, 1])

    with col_prev:
        if st.button("<< Anterior", use_container_width=True):
            go_to_step(1)
            st.rerun()

    with col_next:
        if st.button(
            "Proximo >>",
            disabled=len(current_keywords) == 0,
            type="primary",
            use_container_width=True,
        ):
            st.session_state["edited_keywords"] = current_keywords
            go_to_step(3)
            st.rerun()


# ===========================================================================
# Step 3: Fontes e Busca
# ===========================================================================


def _get_selected_searchers(selected_sources: list[str]) -> list:
    """Instantiate searcher objects for the selected sources.

    Args:
        selected_sources: List of source names ("LexML", "TCU", "Google").

    Returns:
        List of BaseSearcher instances in the order they should be queried.
    """
    searchers = []
    if "LexML" in selected_sources:
        searchers.append(LexMLSearcher())
    if "TCU" in selected_sources:
        searchers.append(TCUSearcher())
    if "Google" in selected_sources:
        searchers.append(GoogleSearcher())
    return searchers


def _execute_search(
    keywords: list[str],
    selected_sources: list[str],
    max_results: int,
) -> None:
    """Execute the search across all selected sources with progress tracking.

    Uses st.status for an expandable progress container with a progress bar
    that advances proportionally across sources and keywords.

    Args:
        keywords: List of search keywords.
        selected_sources: List of source name strings ("LexML", "TCU", "Google").
        max_results: Maximum results per source.
    """
    logger.info(
        "Starting search: %d keywords, sources=%s, max_results=%d",
        len(keywords), selected_sources, max_results,
    )

    with st.status("Buscando normativos...", expanded=True) as status:
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        all_results: list[NormativoResult] = []
        searchers = _get_selected_searchers(selected_sources)
        total_sources = len(searchers)

        for src_idx, searcher in enumerate(searchers):
            source_name = searcher.source_name()

            # Create a progress callback closure for this source.
            # The callback maps per-keyword progress to the global progress bar.
            def make_progress_callback(idx, total, name):
                def callback(current, total_kw, message):
                    source_fraction = idx / total
                    keyword_fraction = (
                        (current / total_kw) / total if total_kw > 0 else 0
                    )
                    combined = min(source_fraction + keyword_fraction, 0.99)
                    progress_bar.progress(combined)
                    status_text.write(f"**{name}** - {message}")
                return callback

            cb = make_progress_callback(src_idx, total_sources, source_name)

            try:
                results = searcher.search(
                    keywords,
                    max_results=max_results,
                    progress_callback=cb,
                )
                all_results.extend(results)
            except Exception as e:
                # Log the error but continue with other sources
                logger.error(
                    "Search error for source '%s': %s", source_name, e
                )
                status_text.write(
                    f"**{source_name}** - Erro ao buscar. "
                    f"Continuando com demais fontes..."
                )

        # Deduplication
        status_text.write("Removendo duplicatas...")
        all_results = deduplicate(all_results)

        # LLM enrichment (relevance scoring + categorization)
        if llm_available() and all_results:
            status_text.write("Avaliando relevancia com IA...")
            topic = st.session_state.get("topic", "")

            # Convert NormativoResult objects to dicts for the LLM functions
            result_dicts = [
                {"nome": r.nome, "ementa": r.ementa}
                for r in all_results
            ]

            # Score relevance
            scores = gemini_client.score_relevance(
                topic, result_dicts, keywords
            )
            for i, score in enumerate(scores):
                if i < len(all_results):
                    all_results[i].relevancia = score

            # Categorize
            status_text.write("Categorizando normativos...")
            categories = gemini_client.categorize_results(
                topic, result_dicts
            )
            for i, cat in enumerate(categories):
                if i < len(all_results):
                    all_results[i].categoria = cat

        progress_bar.progress(1.0)
        status.update(
            label=f"Busca concluida - {len(all_results)} normativos encontrados",
            state="complete",
        )

    # Store results and advance to Step 4
    st.session_state["results"] = all_results
    st.session_state["search_done"] = True

    # Initialize checkboxes for all results (default: selected)
    for i in range(len(all_results)):
        if f"sel_{i}" not in st.session_state:
            st.session_state[f"sel_{i}"] = True

    go_to_step(4)
    st.rerun()


def render_step3() -> None:
    """Passo 3: Selecionar fontes de pesquisa e executar busca."""

    st.header("Passo 3 - Selecionar Fontes e Iniciar Busca")

    # If search already completed, show summary and allow re-search or advance
    if st.session_state.get("search_done"):
        results = st.session_state.get("results", [])
        st.success(f"Busca concluida - {len(results)} normativos encontrados.")

        col1, col2, col3 = st.columns([1, 1, 3])
        with col1:
            if st.button("Refazer Busca", use_container_width=True):
                st.session_state["search_done"] = False
                st.session_state["results"] = []
                # Clear stale selection checkboxes from previous search
                for key in list(st.session_state.keys()):
                    if key.startswith("sel_"):
                        del st.session_state[key]
                st.rerun()
        with col2:
            if st.button(
                "Ver Resultados >>",
                type="primary",
                use_container_width=True,
            ):
                go_to_step(4)
                st.rerun()
        return

    keywords = get_current_keywords()

    # -- Source selection --
    st.subheader("Fontes de pesquisa")

    col1, col2, col3 = st.columns(3)
    with col1:
        use_lexml = st.checkbox(
            "LexML Brasil",
            value=True,
            key="source_lexml",
            help=(
                "Base completa de legislacao brasileira. Abrange leis, decretos, "
                "instrucoes normativas e outros atos de todos os poderes."
            ),
        )
    with col2:
        use_tcu = st.checkbox(
            "TCU Dados Abertos",
            value=True,
            key="source_tcu",
            help=(
                "Acordaos e atos normativos do Tribunal de Contas da Uniao. "
                "Indisponivel diariamente das 20h as 21h (manutencao)."
            ),
        )
    with col3:
        use_google = st.checkbox(
            "Google Search",
            value=True,
            key="source_google",
            help=(
                "Busca por padroes e frameworks internacionais: COBIT, ISO, COSO, "
                "ITIL, NIST e similares."
            ),
        )

    any_source_selected = use_lexml or use_tcu or use_google

    max_results = st.number_input(
        "Maximo de resultados por fonte:",
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        key="max_results_per_source",
    )

    # -- Search summary --
    st.divider()
    st.subheader("Resumo da busca")

    topic = st.session_state.get("topic", "")
    st.write(f"**Tema:** {topic}")
    st.write(f"**Palavras-chave:** {len(keywords)}")

    # Show first 10 keywords as a preview
    preview_keywords = keywords[:10]
    keyword_tags = " | ".join(f"`{kw}`" for kw in preview_keywords)
    if len(keywords) > 10:
        keyword_tags += f" | ... (+{len(keywords) - 10} mais)"
    if keyword_tags:
        st.markdown(keyword_tags)

    selected_sources = []
    if use_lexml:
        selected_sources.append("LexML")
    if use_tcu:
        selected_sources.append("TCU")
    if use_google:
        selected_sources.append("Google")
    st.write(
        f"**Fontes selecionadas:** {', '.join(selected_sources) or 'Nenhuma'}"
    )

    # -- Navigation and search trigger --
    st.divider()
    col_prev, col_spacer, col_search = st.columns([1, 2, 2])

    with col_prev:
        if st.button("<< Anterior", use_container_width=True):
            if st.session_state.get("input_mode") == "keywords":
                go_to_step(1)  # Skip step 2 going back too
            else:
                go_to_step(2)
            st.rerun()

    with col_search:
        if st.button(
            "Iniciar Busca",
            disabled=not any_source_selected or len(keywords) == 0,
            type="primary",
            use_container_width=True,
        ):
            _execute_search(keywords, selected_sources, max_results)


# ===========================================================================
# Step 4: Revisar Resultados
# ===========================================================================


def _apply_filters(
    results: list[NormativoResult],
    tipo_filter: str,
    fonte_filter: str,
    relevancia_min: int,
) -> list[NormativoResult]:
    """Filter results based on user-selected criteria.

    Args:
        results: Full result list.
        tipo_filter: Selected tipo or "Todos" for no filter.
        fonte_filter: Selected source or "Todas" for no filter.
        relevancia_min: Minimum relevance percentage (0 to 100).

    Returns:
        Filtered list (new list, original unmodified).
    """
    filtered = results

    if tipo_filter != "Todos":
        filtered = [r for r in filtered if r.tipo == tipo_filter]

    if fonte_filter != "Todas":
        filtered = [
            r for r in filtered
            if fonte_filter.lower() in r.source.lower()
        ]

    if relevancia_min > 0:
        threshold = relevancia_min / 100
        filtered = [r for r in filtered if r.relevancia >= threshold]

    return filtered


def _apply_sort(
    results: list[NormativoResult], sort_option: str
) -> list[NormativoResult]:
    """Sort results based on user-selected criterion.

    Args:
        results: Filtered result list.
        sort_option: One of the sort option strings from the selectbox.

    Returns:
        Sorted list (new list, original unmodified).
    """
    if sort_option == "Relevancia (descendente)":
        return sorted(results, key=lambda r: r.relevancia, reverse=True)
    elif sort_option == "Data (descendente)":
        return sorted(results, key=lambda r: r.data or "", reverse=True)
    elif sort_option == "Tipo":
        return sorted(results, key=lambda r: r.tipo or "")
    elif sort_option == "Nome":
        return sorted(results, key=lambda r: r.nome or "")
    return results


def _init_checkboxes(results: list[NormativoResult]) -> None:
    """Initialize selection checkboxes for all results.

    Sets default state to True (selected) for each result that does not
    already have a checkbox key in session state.
    """
    for i in range(len(results)):
        if f"sel_{i}" not in st.session_state:
            st.session_state[f"sel_{i}"] = True


def _select_all(count: int) -> None:
    """Set all checkboxes to True."""
    for i in range(count):
        st.session_state[f"sel_{i}"] = True


def _deselect_all(count: int) -> None:
    """Set all checkboxes to False."""
    for i in range(count):
        st.session_state[f"sel_{i}"] = False


def _count_selected(count: int) -> int:
    """Count how many results are currently selected."""
    return sum(
        1 for i in range(count)
        if st.session_state.get(f"sel_{i}", False)
    )


def render_step4() -> None:
    """Passo 4: Revisar resultados com filtros, ordenacao e selecao."""

    results: list[NormativoResult] = st.session_state.get("results", [])

    if not results:
        st.header("Passo 4 - Revisar Resultados")
        st.info(
            "Nenhum normativo encontrado para as palavras-chave informadas. "
            "Tente ampliar as palavras-chave ou selecionar fontes adicionais."
        )
        if st.button("<< Voltar para Busca"):
            st.session_state["search_done"] = False
            go_to_step(3)
            st.rerun()
        return

    _init_checkboxes(results)

    st.header(f"Passo 4 - Revisar Resultados ({len(results)} normativos)")

    # --- Filter bar ---
    col_tipo, col_fonte, col_rel, col_sort = st.columns([1, 1, 1, 1])

    # Extract unique values for filter dropdowns
    unique_tipos = sorted(set(r.tipo for r in results if r.tipo))
    unique_sources = sorted(
        set(
            s.strip()
            for r in results
            for s in r.source.split(",")
            if s.strip()
        )
    )

    with col_tipo:
        tipo_filter = st.selectbox(
            "Filtrar por tipo:",
            ["Todos"] + unique_tipos,
            key="filter_tipo",
        )

    with col_fonte:
        fonte_filter = st.selectbox(
            "Filtrar por fonte:",
            ["Todas"] + unique_sources,
            key="filter_fonte",
        )

    with col_rel:
        rel_min = st.slider(
            "Relevancia minima:",
            min_value=0,
            max_value=100,
            value=0,
            step=10,
            key="filter_rel_min",
            format="%d%%",
        )

    with col_sort:
        sort_option = st.selectbox(
            "Ordenar por:",
            [
                "Relevancia (descendente)",
                "Data (descendente)",
                "Tipo",
                "Nome",
            ],
            key="sort_option",
        )

    # Apply filters and sorting
    filtered = _apply_filters(results, tipo_filter, fonte_filter, rel_min)
    filtered = _apply_sort(filtered, sort_option)

    # --- Selection buttons ---
    col_sel, col_desel, col_count = st.columns([1, 1, 4])

    with col_sel:
        if st.button("Selecionar todos", use_container_width=True):
            _select_all(len(results))
            st.rerun()

    with col_desel:
        if st.button("Desmarcar todos", use_container_width=True):
            _deselect_all(len(results))
            st.rerun()

    with col_count:
        selected_count = _count_selected(len(results))
        st.write(f"**{selected_count}** de **{len(results)}** selecionados")

    st.divider()

    # --- Result cards ---
    if not filtered:
        st.info(
            "Nenhum normativo corresponde aos filtros selecionados. "
            "Ajuste os filtros acima."
        )
    else:
        # Pre-build index map using object identity for O(1) lookups
        # instead of results.index(item) which is O(n) per call.
        idx_map = {id(r): i for i, r in enumerate(results)}

        for i, item in enumerate(filtered):
            # Find original index in full results list for checkbox key.
            original_idx = idx_map[id(item)]

            col_check, col_content = st.columns([0.05, 0.95])

            with col_check:
                st.checkbox(
                    label="sel",
                    key=f"sel_{original_idx}",
                    label_visibility="collapsed",
                )

            with col_content:
                # Summary line with tipo badge
                tipo_color = _get_tipo_color(item.tipo)
                relevancia_pct = int(item.relevancia * 100)

                # Escape ementa to prevent XSS from source data
                safe_ementa = html_module.escape(item.ementa or "")
                ementa_preview = safe_ementa[:200]
                if len(safe_ementa) > 200:
                    ementa_preview += "..."

                st.markdown(
                    f"**{html_module.escape(item.nome)}**\n\n"
                    f"<small style='color:#666'>"
                    f"<span style='background:{tipo_color};color:white;"
                    f"padding:2px 6px;border-radius:3px;font-size:11px'>"
                    f"{html_module.escape(item.tipo)}</span> &middot; "
                    f"<b>Orgao:</b> {html_module.escape(item.orgao_emissor or 'N/I')} &middot; "
                    f"<b>Data:</b> {html_module.escape(item.data or 'N/I')} &middot; "
                    f"<b>Relevancia:</b> {relevancia_pct}% &middot; "
                    f"<b>Fonte:</b> {html_module.escape(item.source)}"
                    f"</small>\n\n"
                    f"<span style='color:#444'>"
                    f"{ementa_preview}"
                    f"</span>",
                    unsafe_allow_html=True,
                )

                # Detail expander — escape all external data to prevent XSS
                with st.expander("Ver detalhes", expanded=False):
                    st.markdown(f"**Ementa completa:** {html_module.escape(item.ementa or '')}")
                    if item.link and item.link.startswith(("https://", "http://")):
                        safe_link = html_module.escape(item.link)
                        st.markdown(f"**Link:** [{safe_link}]({safe_link})")
                    else:
                        st.markdown("**Link:** N/I")
                    st.markdown(f"**Categoria:** {html_module.escape(item.categoria or 'N/I')}")
                    st.markdown(f"**Situacao:** {html_module.escape(item.situacao or 'N/I')}")
                    st.markdown(f"**Encontrado por:** `{html_module.escape(item.found_by or '')}`")
                    if item.numero:
                        st.markdown(f"**Numero:** {html_module.escape(item.numero)}")

            st.divider()

    # --- Navigation ---
    st.divider()
    col_prev, col_spacer, col_next = st.columns([1, 3, 1])

    with col_prev:
        if st.button(
            "<< Anterior", use_container_width=True, key="step4_prev"
        ):
            st.session_state["search_done"] = False
            go_to_step(3)
            st.rerun()

    with col_next:
        has_selection = _count_selected(len(results)) > 0
        if st.button(
            "Proximo >>",
            disabled=not has_selection,
            type="primary",
            use_container_width=True,
            key="step4_next",
        ):
            go_to_step(5)
            st.rerun()


# ===========================================================================
# Step 5: Exportar
# ===========================================================================


def render_step5() -> None:
    """Passo 5: Exportar resultados selecionados para Excel."""

    st.header("Passo 5 - Exportar Resultados")

    results: list[NormativoResult] = st.session_state.get("results", [])

    # Gather selected items
    selected = [
        item
        for i, item in enumerate(results)
        if st.session_state.get(f"sel_{i}", False)
    ]

    if not selected:
        st.warning(
            "Nenhum normativo selecionado. Volte ao passo anterior "
            "e selecione pelo menos um item."
        )
        if st.button("<< Voltar aos Resultados"):
            go_to_step(4)
            st.rerun()
        return

    # --- Summary and actions side by side ---
    col_summary, col_actions = st.columns([2, 1])

    with col_summary:
        st.metric(
            "Normativos selecionados",
            f"{len(selected)} de {len(results)}",
        )

        # Breakdown by tipo
        tipo_counts = Counter(item.tipo for item in selected)
        for tipo, count in sorted(tipo_counts.items()):
            st.write(f"  - **{tipo}:** {count}")

        # Breakdown by source
        source_counts = Counter(
            s.strip()
            for item in selected
            for s in item.source.split(",")
            if s.strip()
        )
        st.write("")
        st.write("**Por fonte:**")
        for source, count in sorted(source_counts.items()):
            st.write(f"  - {source}: {count}")

    with col_actions:
        topic = st.session_state.get("topic", "levantamento")
        topic_slug = _make_filename_slug(topic)

        # Generate Excel button
        if st.button(
            "Gerar Excel",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Gerando arquivo Excel..."):
                try:
                    buffer = generate_excel(selected, topic)
                    st.session_state["excel_buffer"] = buffer
                    st.success("Excel gerado com sucesso!")
                except Exception as e:
                    logger.error("Failed to generate Excel: %s", e)
                    st.error("Erro ao gerar Excel. Consulte os logs para detalhes.")
            st.rerun()

        # Download button (only visible after generation)
        if st.session_state.get("excel_buffer"):
            st.download_button(
                label="Baixar Excel (.xlsx)",
                data=st.session_state["excel_buffer"],
                file_name=f"normativos_{topic_slug}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
                use_container_width=True,
            )

    # --- Preview table ---
    st.divider()
    st.subheader("Preview dos normativos selecionados")

    df = pd.DataFrame(
        [
            {
                "Nome": item.nome,
                "Tipo": item.tipo,
                "Orgao": item.orgao_emissor or "",
                "Data": item.data or "",
                "Relevancia": f"{int(item.relevancia * 100)}%",
                "Categoria": item.categoria or "",
                "Fonte": item.source,
            }
            for item in selected
        ]
    )

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Nome": st.column_config.TextColumn("Nome", width="large"),
            "Relevancia": st.column_config.TextColumn(
                "Relevancia", width="small"
            ),
        },
    )

    # --- Navigation ---
    st.divider()
    col_prev, col_spacer = st.columns([1, 4])

    with col_prev:
        if st.button(
            "<< Voltar aos Resultados",
            use_container_width=True,
        ):
            go_to_step(4)
            st.rerun()


# ===========================================================================
# Main area: render the active wizard step
# ===========================================================================
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
