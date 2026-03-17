# Project Context

## Goal
Build the "Levantamento de Normativos" Streamlit application — a multi-source legislation search tool for NUATI (Câmara dos Deputados) that:
1. Takes a topic or keywords as input
2. Searches LexML, TCU Open Data, and Google for relevant laws/regulations/standards
3. Uses Gemini Flash LLM for keyword expansion, relevance scoring, and categorization
4. Presents results in a 5-step wizard with checkboxes, filters, and detail toggles
5. Exports selected normativos to a formatted Excel file

## Spec Location
All implementation specs are in `spec/implementation-plan/` (files 00 through 06).

## Target Directory
New app lives in `levantamento-normativos/` at the project root.

## Tech Stack
- Python 3.11+, Streamlit, Google Gemini Flash, openpyxl, requests, googlesearch-python
- Câmara dos Deputados green (#4CAF50) / gold (#c8a415) visual identity

## Dependency Graph Between Phases
```
Phase 1 (Foundation) ──────────────────┐
    │                                   │
    ├──→ Phase 2 (Searchers)            │
    │       depends on: models.py       │
    │                                   │
    ├──→ Phase 3 (LLM/Gemini)          │
    │       depends on: models.py       │
    │                                   │
    └──→ Phase 4 (Dedup + Excel)        │
            depends on: models.py       │
                                        │
Phase 5 (Streamlit UI) ◄───────────────┘
    depends on: ALL of phases 1-4

Phase 6 (Testing & Polish)
    depends on: ALL of phases 1-5
```

## Parallel Tracks Identified
- **Track A**: Phase 1 (Foundation) — MUST complete first, all others depend on it
- **Track B**: Phase 2 (Searchers) — can run in parallel with C and D after Track A
- **Track C**: Phase 3 (LLM/Gemini) — can run in parallel with B and D after Track A
- **Track D**: Phase 4 (Dedup + Excel) — can run in parallel with B and C after Track A
- **Track E**: Phase 5 (Streamlit UI) — depends on B, C, D completion
- **Track F**: Phase 6 (Testing & Polish) — final pass after E
- **Track G**: Final Code Reviews (3 reviewers) — after everything
