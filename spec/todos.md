# Implementation Todos

## Track A: Foundation (Phase 1) — BLOCKING
- [x] A1: Create directory structure, requirements.txt, config files
- [x] A2: Implement models.py (NormativoResult, SearchConfig)
- [x] A3: Implement app.py scaffold (wizard navigation, theme, sidebar)
- [x] A4: Create all placeholder files for modules
- [x] A5: Test Track A (app launches, models importable, navigation works)
- [x] A6: Code review Track A — PASSED, zero discrepancies from spec
- [ ] A7: Document Track A

## Track B: Searchers (Phase 2) — parallel with C, D
- [x] B1: Implement searchers/base.py (BaseSearcher ABC)
- [x] B2: Implement searchers/lexml_searcher.py (LexML SRU/CQL)
- [x] B3: Implement searchers/tcu_searcher.py (TCU Open Data API)
- [x] B4: Implement searchers/google_searcher.py (Google search)
- [x] B5: Implement searchers/__init__.py (exports)
- [x] B6: Test Track B — 13/13 PASS (APIs down but graceful degradation confirmed)
- [x] B7: Code review Track B — CRITICAL: Google ID collision bug, LexML URLs 404, minor issues
- [x] B8: Fixes applied and verified (Google ID collision, LexML fallback URL, accented names)

## Track C: LLM Integration (Phase 3) — parallel with B, D
- [x] C1: Implement llm/gemini_client.py (all 3 functions + fallbacks)
- [x] C2: Implement llm/__init__.py (exports)
- [x] C3: Test Track C — 53/53 tests PASS
- [x] C4: Code review Track C — no critical issues, 2 major suggestions
- [x] C5: Fixes applied and verified (guarded import, topic truncation, response.text guard)

## Track D: Dedup + Excel (Phase 4) — parallel with B, C
- [x] D1: Implement deduplicator.py (3-strategy dedup)
- [x] D2: Implement excel_export.py (openpyxl with formatting)
- [x] D3: Test Track D — 41/41 tests PASS
- [x] D4: Code review Track D — 1 bug found (link merge priority), minor issues noted
- [x] D5: Fixes applied and verified (link priority bug, cached normalization, relevancia handling)

## Track E: Streamlit UI (Phase 5) — depends on B, C, D
- [x] E1: Implement Step 1 (Definir Tema) in app.py
- [x] E2: Implement Step 2 (Palavras-chave) in app.py
- [x] E3: Implement Step 3 (Fontes e Busca) in app.py
- [x] E4: Implement Step 4 (Revisar Resultados) in app.py
- [x] E5: Implement Step 5 (Exportar) in app.py
- [ ] E6: Test Track E (full wizard flow)
- [x] E7: Code review Track E — 2 critical (widget key conflict, slider format), 4 major issues
- [x] E8: Fixes applied (widget keys, slider, O(n²), stale checkboxes, HTML escaping, error handling)

## Track F: Testing & Polish (Phase 6) — depends on E
- [x] F1: Add comprehensive error handling to all modules
- [x] F2: Add logging throughout (app.py, deduplicator.py, excel_export.py)
- [x] F3: Verify all user-facing messages in Portuguese — all confirmed
- [x] F4: Final integration smoke test — PASSED
- [ ] F5: Code review Track F
- [ ] F6: Document Track F

## Track G: Final Reviews — after all tracks
- [x] G1: Security review — 0 critical, 3 medium (prompt injection, verbose errors, SSRF), 4 low
- [x] G2: Code quality review — 2 critical fixed (CQL injection, missing beautifulsoup4), minor issues noted
- [ ] G3: UX/Architecture review (all modules)

## Notion Documentation
- [ ] N1: Update Notion pages with new project structure
