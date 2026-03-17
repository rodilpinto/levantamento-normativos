# Phase 5: Streamlit UI -- Complete Wizard Implementation

## Context

The application uses a 5-step wizard pattern in Streamlit to guide users through the normativo research workflow. The scaffold (page structure, session state initialization, navigation helpers) was created in Phase 1. This phase provides the complete implementation for every step.

The wizard steps are:

1. **Definir Tema** -- User provides a research topic or manual keywords
2. **Revisar Palavras-chave** -- Review and edit LLM-generated keywords
3. **Fontes e Busca** -- Select data sources and execute the search
4. **Revisar Resultados** -- Filter, sort, and select normativos
5. **Exportar** -- Generate and download the Excel report

### Module Location

```
levantamento-normativos/
  app.py              # Main Streamlit entrypoint
  ui_components.py    # Reusable UI helper functions (optional extraction)
```

### NormativoResult Fields Available

All steps can access these fields on each result object:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | SHA-256 hex digest of `tipo\|numero\|data` |
| `nome` | `str` | Display name (e.g., "Lei 13.709/2018 - LGPD") |
| `tipo` | `str` | Normativo type (e.g., "Lei", "Decreto", "Instrucao Normativa") |
| `numero` | `str` | Official number (e.g., "13.709") |
| `data` | `str` | Date string (e.g., "14/08/2018") |
| `orgao_emissor` | `str` | Issuing body (e.g., "Presidencia da Republica") |
| `ementa` | `str` | Summary/description text |
| `link` | `str` | URL to the full text |
| `categoria` | `str` | LLM-assigned category (e.g., "Protecao de Dados") |
| `situacao` | `str` | Status (e.g., "Vigente", "Revogado") |
| `relevancia` | `float` | Relevance score from 0.0 to 1.0 |
| `source` | `str` | Comma-separated source names (e.g., "lexml, tcu") |
| `found_by` | `str` | Comma-separated keywords that found this result |
| `raw_data` | `dict` | Original API response data (for debugging) |

---

## 5.0 Session State Schema

All wizard state lives in `st.session_state`. The following keys are used:

```python
# Navigation
"current_step": int          # 1-5, default 1

# Step 1 outputs
"input_mode": str            # "topic" or "keywords"
"topic": str                 # User-entered topic text
"raw_keywords": list[str]    # Keywords entered manually (keywords mode)

# Step 2 outputs
"llm_keywords": list[str]    # Original keywords from LLM (for restore)
"edited_keywords": list[str] # Current keywords after user edits
"keywords_generated": bool   # True after first LLM call (prevents re-calling on rerun)

# Step 3 outputs
"source_lexml": bool         # Checkbox state
"source_tcu": bool           # Checkbox state
"source_google": bool        # Checkbox state
"max_results_per_source": int
"search_done": bool          # True after search completes
"results": list[NormativoResult]  # Full result set after dedup

# Step 4 outputs
"sel_{i}": bool              # Checkbox for each result item (dynamic keys)

# Step 5 outputs
"excel_buffer": BytesIO      # Generated Excel file buffer
```

### Navigation Helpers

```python
def go_to_step(step: int) -> None:
    """Set the current wizard step. Used by navigation buttons.

    Args:
        step: Target step number (1-5). Clamped to valid range.
    """
    st.session_state["current_step"] = max(1, min(5, step))


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
```

---

## 5.1 Step 1: Definir Tema

### Purpose

Capture the user's research intent. Two modes are supported:

- **Topic mode:** User describes a broad topic in natural language. The LLM will expand this into search keywords in Step 2.
- **Keywords mode:** User provides explicit search keywords. Step 2 is skipped entirely, advancing directly to Step 3.

### Implementation

```python
def render_step_1():
    """Render Step 1: topic/keyword input."""

    st.header("Passo 1 -- Definir Tema do Levantamento")
    st.markdown(
        "Descreva o tema de pesquisa ou insira palavras-chave diretamente. "
        "O sistema buscara normativos relacionados em multiplas fontes."
    )

    input_mode = st.radio(
        "Modo de entrada:",
        ["Descrever tema (IA expande palavras-chave)", "Inserir palavras-chave manualmente"],
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
        if not gemini_client.is_available():
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
            placeholder="governanca de TI\nseguranca da informacao\nLGPD\nprotecao de dados\nCOBIT",
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
            st.session_state["topic"] = parsed[0]  # Use first keyword as topic

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
                # Skip Step 2 (keyword review) -- go directly to source selection
                go_to_step(3)
            else:
                go_to_step(2)
            st.rerun()
```

### Behavior Notes

- The topic text input retains its value across reruns via session state.
- In keywords mode, the topic for Excel export defaults to the first keyword. Users can always change this later.
- The "Proximo" button is disabled until the user has entered valid input.
- If Gemini is unavailable and the user selects topic mode, they see an info banner but are not blocked (they can still proceed; Step 2 will fall back to manual entry).

---

## 5.2 Step 2: Revisar Palavras-chave

### Purpose

Show the LLM-generated keywords for the user's topic. Allow editing, regeneration, and manual additions before proceeding to search.

### LLM Call (On First Entry)

When Step 2 renders for the first time (i.e., `keywords_generated` is not set), it automatically calls the Gemini client to expand the topic:

```python
def _generate_keywords_if_needed():
    """Call LLM to expand topic into keywords, but only on first visit.

    Uses session_state['keywords_generated'] as a guard to prevent
    re-calling the LLM on every Streamlit rerun.
    """
    if st.session_state.get("keywords_generated"):
        return  # Already generated, skip

    topic = st.session_state.get("topic", "")
    if not topic:
        return

    if not gemini_client.is_available():
        st.session_state["llm_keywords"] = []
        st.session_state["edited_keywords"] = []
        st.session_state["keywords_generated"] = True
        return

    with st.spinner("Gerando palavras-chave com IA..."):
        keywords = gemini_client.expand_topic_to_keywords(topic)

    st.session_state["llm_keywords"] = keywords[:]  # Store copy for restore
    st.session_state["edited_keywords"] = keywords[:]
    st.session_state["keywords_generated"] = True
```

### Implementation

```python
def render_step_2():
    """Render Step 2: keyword review and editing."""

    st.header("Passo 2 -- Revisar Palavras-chave")

    topic = st.session_state.get("topic", "")
    st.caption(f"Tema: {topic}")

    # Generate keywords on first visit
    _generate_keywords_if_needed()

    # Status message
    keywords = st.session_state.get("edited_keywords", [])
    if gemini_client.is_available() and keywords:
        st.success(f"IA gerou {len(st.session_state.get('llm_keywords', []))} palavras-chave a partir do tema.")
    elif not gemini_client.is_available():
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
        regen_disabled = not gemini_client.is_available()
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
```

### Behavior Notes

- **Regenerate** clears the `keywords_generated` flag and triggers a rerun, which causes `_generate_keywords_if_needed()` to call the LLM again.
- **Limpar** empties the keyword list but does not call the LLM.
- **Restaurar** copies the original LLM output back into the editor. Only available if LLM keywords exist.
- The text area content is parsed on every rerun to update the keyword count metric.
- Keywords are stored in session state when "Proximo" is clicked, not on every keystroke.

---

## 5.3 Step 3: Fontes e Busca

### Purpose

Let the user select which data sources to query and configure search parameters, then execute the search with real-time progress feedback.

### Implementation (Before Search)

```python
def render_step_3():
    """Render Step 3: source selection and search execution."""

    st.header("Passo 3 -- Selecionar Fontes e Iniciar Busca")

    # If search already completed, show summary and allow re-search or advance
    if st.session_state.get("search_done"):
        results = st.session_state.get("results", [])
        st.success(f"Busca concluida -- {len(results)} normativos encontrados.")

        col1, col2, col3 = st.columns([1, 1, 3])
        with col1:
            if st.button("Refazer Busca", use_container_width=True):
                st.session_state["search_done"] = False
                st.session_state["results"] = []
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
            value=st.session_state.get("source_lexml", True),
            key="source_lexml",
            help="Base completa de legislacao brasileira. Abrange leis, decretos, "
                 "instrucoes normativas e outros atos de todos os poderes.",
        )
    with col2:
        use_tcu = st.checkbox(
            "TCU Dados Abertos",
            value=st.session_state.get("source_tcu", True),
            key="source_tcu",
            help="Acordaos e atos normativos do Tribunal de Contas da Uniao. "
                 "Indisponivel diariamente das 20h as 21h (manutencao).",
        )
    with col3:
        use_google = st.checkbox(
            "Google Search",
            value=st.session_state.get("source_google", True),
            key="source_google",
            help="Busca por padroes e frameworks internacionais: COBIT, ISO, COSO, "
                 "ITIL, NIST e similares.",
        )

    any_source_selected = use_lexml or use_tcu or use_google

    max_results = st.number_input(
        "Maximo de resultados por fonte:",
        min_value=10,
        max_value=200,
        value=st.session_state.get("max_results_per_source", 50),
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
    st.markdown(keyword_tags)

    selected_sources = []
    if use_lexml:
        selected_sources.append("LexML")
    if use_tcu:
        selected_sources.append("TCU")
    if use_google:
        selected_sources.append("Google")
    st.write(f"**Fontes selecionadas:** {', '.join(selected_sources) or 'Nenhuma'}")

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
```

### Search Execution

```python
def _execute_search(
    keywords: list[str],
    selected_sources: list[str],
    max_results: int,
) -> None:
    """Execute the search across all selected sources with progress tracking.

    This function uses st.status for an expandable progress container,
    matching the UX pattern from dou-clipping-app/app.py lines 349-388.

    The progress bar advances proportionally across sources and keywords:
    - Each source gets an equal share of the progress bar
    - Within each source, progress advances per keyword processed

    Args:
        keywords: List of search keywords.
        selected_sources: List of source name strings ("LexML", "TCU", "Google").
        max_results: Maximum results per source.
    """
    with st.status("Buscando normativos...", expanded=True) as status:
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        all_results = []
        searchers = _get_selected_searchers(selected_sources)
        total_sources = len(searchers)

        for src_idx, searcher in enumerate(searchers):
            source_name = searcher.source_name()

            # Create a progress callback closure for this source.
            # The callback receives (current_keyword_index, total_keywords, message)
            # and maps it to the global progress bar position.
            def make_progress_callback(idx, total, name):
                def callback(current, total_kw, message):
                    source_fraction = idx / total
                    keyword_fraction = (
                        (current / total_kw) / total if total_kw > 0 else 0
                    )
                    combined = min(source_fraction + keyword_fraction, 0.99)
                    progress_bar.progress(combined)
                    status_text.write(f"**{name}** -- {message}")
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
                status_text.write(
                    f"**{source_name}** -- Erro: {str(e)[:100]}. "
                    f"Continuando com demais fontes..."
                )

        # -- Deduplication --
        status_text.write("Removendo duplicatas...")
        from deduplicator import deduplicate
        all_results = deduplicate(all_results)

        # -- LLM enrichment (relevance scoring + categorization) --
        if gemini_client.is_available() and all_results:
            status_text.write("Avaliando relevancia com IA...")
            topic = st.session_state.get("topic", "")

            # Score relevance in batches
            all_results = gemini_client.score_relevance_batch(
                all_results, topic, keywords
            )

            status_text.write("Categorizando normativos...")
            all_results = gemini_client.categorize_batch(all_results)

        progress_bar.progress(1.0)
        status.update(
            label=f"Busca concluida -- {len(all_results)} normativos encontrados",
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


def _get_selected_searchers(selected_sources: list[str]) -> list:
    """Instantiate searcher objects for the selected sources.

    Args:
        selected_sources: List of source names ("LexML", "TCU", "Google").

    Returns:
        List of BaseSearcher instances in the order they should be queried.
    """
    from lexml_searcher import LexMLSearcher
    from tcu_searcher import TCUSearcher
    from google_searcher import GoogleSearcher

    searchers = []
    if "LexML" in selected_sources:
        searchers.append(LexMLSearcher())
    if "TCU" in selected_sources:
        searchers.append(TCUSearcher())
    if "Google" in selected_sources:
        searchers.append(GoogleSearcher())
    return searchers
```

---

## 5.4 Step 4: Revisar Resultados

### Purpose

Present all search results in a filterable, sortable list. Each result has a checkbox for selection. Users can filter by tipo, source, and minimum relevance, and sort by various fields. This is the most complex step, closely mirroring the result review pattern from dou-clipping-app/app.py lines 409-539.

### Tipo Badge Colors

```python
TIPO_COLORS = {
    "Lei": "#1976D2",             # Blue
    "Lei Complementar": "#1565C0", # Darker blue
    "Decreto": "#7B1FA2",         # Purple
    "Instrucao Normativa": "#F57C00",  # Orange
    "Portaria": "#0097A7",        # Teal
    "Acordao TCU": "#D32F2F",     # Red
    "Resolucao": "#388E3C",       # Green
    "Framework/Padrao": "#5D4037", # Brown
    "Medida Provisoria": "#C62828", # Dark red
    "Norma Complementar": "#00695C", # Dark teal
    "Outro": "#757575",           # Gray
}


def _get_tipo_color(tipo: str) -> str:
    """Get the badge background color for a normativo type.

    Args:
        tipo: The normativo type string.

    Returns:
        Hex color string. Falls back to gray for unrecognized types.
    """
    return TIPO_COLORS.get(tipo, "#757575")
```

### Filter and Sort Functions

```python
def _apply_filters(
    results: list,
    tipo_filter: str,
    fonte_filter: str,
    relevancia_min: float,
) -> list:
    """Filter results based on user-selected criteria.

    Args:
        results: Full result list.
        tipo_filter: Selected tipo or "Todos" for no filter.
        fonte_filter: Selected source or "Todas" for no filter.
        relevancia_min: Minimum relevance score (0.0 to 1.0).

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

    if relevancia_min > 0.0:
        filtered = [r for r in filtered if r.relevancia >= relevancia_min]

    return filtered


def _apply_sort(results: list, sort_option: str) -> list:
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
    else:
        return results
```

### Checkbox Management

```python
def _init_checkboxes(results: list) -> None:
    """Initialize selection checkboxes for all results.

    Sets default state to True (selected) for each result that does not
    already have a checkbox key in session state. This preserves user
    selections across reruns while defaulting new results to selected.

    Args:
        results: Full result list (used for count only).
    """
    for i in range(len(results)):
        if f"sel_{i}" not in st.session_state:
            st.session_state[f"sel_{i}"] = True


def _select_all(count: int) -> None:
    """Set all checkboxes to True.

    Args:
        count: Number of results (determines how many keys to set).
    """
    for i in range(count):
        st.session_state[f"sel_{i}"] = True


def _deselect_all(count: int) -> None:
    """Set all checkboxes to False.

    Args:
        count: Number of results (determines how many keys to set).
    """
    for i in range(count):
        st.session_state[f"sel_{i}"] = False


def _count_selected(count: int) -> int:
    """Count how many results are currently selected.

    Args:
        count: Total number of results.

    Returns:
        Number of results with checkbox set to True.
    """
    return sum(
        1 for i in range(count)
        if st.session_state.get(f"sel_{i}", False)
    )
```

### Implementation

```python
def render_step_4():
    """Render Step 4: result review with filters, sorting, and selection."""

    results = st.session_state.get("results", [])

    if not results:
        st.header("Passo 4 -- Revisar Resultados")
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

    st.header(f"Passo 4 -- Revisar Resultados ({len(results)} normativos)")

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
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.1,
            key="filter_rel_min",
            format="%0.0f%%",
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
        for i, item in enumerate(filtered):
            # Find original index in full results list for checkbox key
            original_idx = results.index(item)

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

                st.markdown(
                    f"**{item.nome}**\n\n"
                    f"<small style='color:#666'>"
                    f"<span style='background:{tipo_color};color:white;"
                    f"padding:2px 6px;border-radius:3px;font-size:11px'>"
                    f"{item.tipo}</span> &middot; "
                    f"<b>Orgao:</b> {item.orgao_emissor or 'N/I'} &middot; "
                    f"<b>Data:</b> {item.data or 'N/I'} &middot; "
                    f"<b>Relevancia:</b> {relevancia_pct}% &middot; "
                    f"<b>Fonte:</b> {item.source}"
                    f"</small>\n\n"
                    f"<span style='color:#444'>"
                    f"{item.ementa[:200]}"
                    f"{'...' if len(item.ementa or '') > 200 else ''}"
                    f"</span>",
                    unsafe_allow_html=True,
                )

                # Detail expander
                with st.expander("Ver detalhes", expanded=False):
                    st.markdown(f"**Ementa completa:** {item.ementa}")
                    if item.link:
                        st.markdown(f"**Link:** [{item.link}]({item.link})")
                    else:
                        st.markdown("**Link:** N/I")
                    st.markdown(f"**Categoria:** {item.categoria or 'N/I'}")
                    st.markdown(f"**Situacao:** {item.situacao or 'N/I'}")
                    st.markdown(f"**Encontrado por:** `{item.found_by}`")
                    if item.numero:
                        st.markdown(f"**Numero:** {item.numero}")

            st.divider()

    # --- Navigation ---
    st.divider()
    col_prev, col_spacer, col_next = st.columns([1, 3, 1])

    with col_prev:
        if st.button("<< Anterior", use_container_width=True, key="step4_prev"):
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
```

### Important Implementation Notes

**Checkbox key stability:** The checkbox keys use the original index in the full results list (`sel_{original_idx}`), not the index in the filtered list. This ensures selections persist when filters change. The `results.index(item)` lookup finds the original index. Since NormativoResult objects are the same references (not copies), this works correctly.

**Unsafe HTML:** The `unsafe_allow_html=True` flag is required for the tipo badge styling. The rendered HTML only contains data from our own NormativoResult objects, which were either parsed from trusted API responses or sanitized during construction. Nevertheless, ementa text could theoretically contain HTML if a source returns it -- a production hardening step would be to escape `item.ementa` with `html.escape()` before embedding it.

**Performance with many results:** For 200+ results, the page may render slowly due to many `st.checkbox` and `st.expander` widgets. If this becomes an issue, implement pagination (20 items per page) using `st.session_state["result_page"]` and slice the filtered list.

---

## 5.5 Step 5: Exportar

### Purpose

Show a summary of selected normativos, generate the Excel file, and provide a download button. Also show a preview table using `st.dataframe`.

### Implementation

```python
def render_step_5():
    """Render Step 5: export summary and Excel download."""

    st.header("Passo 5 -- Exportar Resultados")

    results = st.session_state.get("results", [])

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
        from collections import Counter

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
                from excel_export import generate_excel

                buffer = generate_excel(selected, topic)
                st.session_state["excel_buffer"] = buffer
            st.success("Excel gerado com sucesso!")
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

    import pandas as pd

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
```

### Filename Slug Helper

```python
import re as _re


def _make_filename_slug(topic: str) -> str:
    """Convert a topic string into a filesystem-safe filename slug.

    Transforms the topic into lowercase, removes special characters,
    replaces whitespace with underscores, and truncates to 60 characters
    to avoid path length issues on Windows.

    Args:
        topic: Raw topic string from user input.

    Returns:
        Safe slug string suitable for use in filenames.
        Example: 'Governanca de TI no setor publico' -> 'governanca_de_ti_no_setor_publico'
    """
    slug = topic.lower().strip()
    slug = _re.sub(r"[^\w\s]", "", slug)
    slug = _re.sub(r"\s+", "_", slug)
    return slug[:60]
```

---

## 5.6 Main App Dispatcher

The main `app.py` file wires the steps together:

```python
import streamlit as st

# Page configuration (must be first Streamlit call)
st.set_page_config(
    page_title="Levantamento de Normativos",
    page_icon="icon.png",  # Camara dos Deputados icon
    layout="wide",
    initial_sidebar_state="collapsed",
)


def main():
    """Main application entrypoint. Dispatches to the current wizard step."""

    # Initialize session state defaults
    if "current_step" not in st.session_state:
        st.session_state["current_step"] = 1

    # Sidebar: show current step indicator
    with st.sidebar:
        st.title("Levantamento de Normativos")
        st.caption("NUATI - Camara dos Deputados")
        st.divider()

        steps = [
            "1. Definir Tema",
            "2. Revisar Palavras-chave",
            "3. Fontes e Busca",
            "4. Revisar Resultados",
            "5. Exportar",
        ]
        current = st.session_state["current_step"]
        for i, step_name in enumerate(steps, start=1):
            if i == current:
                st.markdown(f"**-> {step_name}**")
            elif i < current:
                st.markdown(f"~~{step_name}~~")
            else:
                st.markdown(f"{step_name}")

    # Dispatch to current step
    step = st.session_state["current_step"]

    if step == 1:
        render_step_1()
    elif step == 2:
        render_step_2()
    elif step == 3:
        render_step_3()
    elif step == 4:
        render_step_4()
    elif step == 5:
        render_step_5()
    else:
        st.error(f"Passo invalido: {step}")
        st.session_state["current_step"] = 1
        st.rerun()


if __name__ == "__main__":
    main()
```

---

## 5.7 Acceptance Criteria

1. **Complete wizard flow works end-to-end:** topic input -> keyword expansion -> source selection -> search execution -> result review -> Excel export download.
2. **All filters in Step 4 work correctly:** tipo dropdown filters by type, fonte dropdown filters by source, relevancia slider filters by minimum score, sorting reorders results.
3. **Checkboxes persist across filter changes:** Changing a filter does not reset checkbox state because keys use the original result index.
4. **Excel download button works:** clicking "Gerar Excel" produces a valid .xlsx file that downloads correctly.
5. **Navigation between all steps works:** both forward ("Proximo") and backward ("Anterior") buttons function correctly.
6. **Progress bar during search** advances smoothly across sources and keywords, matching the dou-clipping-app behavior.
7. **Keywords mode skips Step 2:** when manual keywords are entered in Step 1, clicking "Proximo" goes directly to Step 3.
8. **Empty results handled gracefully:** Step 4 shows an informational message when no results are found.
9. **Sidebar step indicator** highlights the current step and marks completed steps.
10. **All user-facing text is in Portuguese.**
