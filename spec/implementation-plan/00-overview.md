# Levantamento de Normativos — Implementation Plan Overview

## Project Summary

**App Name:** Levantamento de Normativos
**Team:** NUATI — Núcleo de Auditoria de TI, Câmara dos Deputados
**Author:** Rodrigo Pinto
**Tech Stack:** Python 3.11+, Streamlit, Google Gemini Flash, openpyxl
**Visual Identity:** Câmara dos Deputados green (#4CAF50) / gold (#c8a415) theme

## Purpose

A Streamlit web app that systematically searches for **all relevant laws, regulations, TCU decisions, and standards** applicable to a given audit topic. The user describes a topic (or provides keywords), the app searches multiple data sources, the user reviews and selects relevant results, and the app generates a formatted Excel file mapping all applicable normativos.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit UI (app.py)                 │
│  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌───────┐ ┌────┐ │
│  │ Step 1  │→│ Step 2   │→│ Step 3  │→│Step 4 │→│ 5  │ │
│  │ Topic   │ │ Keywords │ │ Search  │ │Review │ │Exp.│ │
│  └─────────┘ └──────────┘ └─────────┘ └───────┘ └────┘ │
└──────────┬──────────┬──────────┬──────────┬─────────────┘
           │          │          │          │
     ┌─────▼────┐ ┌───▼────┐ ┌──▼──────┐ ┌▼──────────┐
     │ Gemini   │ │LexML   │ │  TCU    │ │  Google   │
     │ Flash    │ │SRU API │ │REST API │ │  Search   │
     │(keyword  │ └────────┘ └─────────┘ └───────────┘
     │ expand,  │          │
     │ scoring, │    ┌─────▼──────┐    ┌──────────┐
     │ categ.)  │    │Deduplicator│───→│Excel     │
     └──────────┘    └────────────┘    │Export    │
                                       └──────────┘
```

## Data Sources

| Source | API Type | Data | Priority |
|--------|----------|------|----------|
| **LexML Brasil** | SRU/XML (CQL queries) | All Brazilian legislation (federal, state, municipal) | 1 |
| **TCU Open Data** | REST/JSON | Acórdãos + atos normativos do TCU | 2 |
| **Google Search** | googlesearch-python | Standards/frameworks: COBIT, ISO, COSO, ITIL | 3 |

## LLM Integration

- **Model:** Google Gemini Flash (free tier)
- **API Key:** Stored in `.streamlit/secrets.toml` as `GEMINI_API_KEY`
- **Capabilities:** Keyword expansion, relevance scoring (0-1), auto-categorization
- **Graceful degradation:** App works fully without LLM (keyword-only mode)

## Excel Output Columns

| Column | Description |
|--------|------------|
| Nome do Normativo | Full name of the law/regulation |
| Tipo | Lei, Decreto, IN, Portaria, Acórdão TCU, Resolução, Framework/Padrão |
| Número | Normativo number |
| Data | Publication date (DD/MM/YYYY) |
| Órgão Emissor | Issuing body |
| Ementa | Summary/description |
| Link | URL to original document (clickable hyperlink) |
| Categoria/Tema | Thematic category (from LLM or manual) |
| Situação | Vigente / Revogado / Não identificado |
| Relevância | 0-100% relevance score |

## Implementation Phases

| Phase | Document | Description |
|-------|----------|-------------|
| 1 | [01-foundation.md](01-foundation.md) | Directory structure, models.py, app scaffold, theme |
| 2 | [02-searchers.md](02-searchers.md) | BaseSearcher ABC, LexML, TCU, Google searchers |
| 3 | [03-llm-integration.md](03-llm-integration.md) | Gemini client: keyword expansion, scoring, categorization |
| 4 | [04-dedup-and-excel.md](04-dedup-and-excel.md) | Cross-source deduplication, Excel export with formatting |
| 5 | [05-streamlit-ui.md](05-streamlit-ui.md) | Complete 5-step wizard UI implementation |
| 6 | [06-testing-and-polish.md](06-testing-and-polish.md) | Error handling, testing script, performance targets |

## Dependencies

```
streamlit>=1.33.0
requests
openpyxl
google-generativeai
googlesearch-python
```

## Quick Start (for developers)

```bash
cd levantamento-normativos
pip install -r requirements.txt
# Copy secrets template and add your Gemini API key
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml with your GEMINI_API_KEY
streamlit run app.py
```

## Key Design Decisions

1. **Modular searchers** — Each source is a separate class following `BaseSearcher` ABC. New sources can be added by implementing `search()` and `source_name()`.
2. **LLM-optional** — App works fully without Gemini. LLM adds keyword expansion, relevance scoring, and categorization. When unavailable, keyword-match heuristic is used for relevance.
3. **Reuse existing patterns** — Streamlit UI patterns (checkboxes, expanders, progress bars, session state) copied from the existing `dou-clipping-app`.
4. **stdlib preferred** — Uses `xml.etree.ElementTree` (not lxml), `difflib` (not fuzzywuzzy), `hashlib` (not external UUID libs) to minimize dependencies.
5. **Rate limiting built-in** — Each searcher has configurable delays between API requests to respect service limits.
