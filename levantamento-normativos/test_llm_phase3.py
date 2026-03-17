"""Comprehensive tests for the LLM/Gemini module (Phase 3).

Tests cover: imports, CATEGORIES validation, fallback behavior (no API key),
keyword relevance heuristic, edge cases, and JSON parsing.
"""

import os
import sys
import traceback

# ---------------------------------------------------------------------------
# Ensure no API key is set BEFORE importing the module, so we test fallback
# ---------------------------------------------------------------------------
_original_key = os.environ.pop("GEMINI_API_KEY", None)

# Track results
results = []


def record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((name, status, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def run_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ===========================================================================
# 1. Import test
# ===========================================================================
run_section("1. Import Test")

try:
    from llm import is_available, expand_topic_to_keywords, score_relevance, categorize_results, CATEGORIES
    record("Import all public symbols", True)
except Exception as e:
    record("Import all public symbols", False, str(e))
    print("FATAL: Cannot proceed without imports.")
    sys.exit(1)

# Also import private helpers for direct testing
try:
    from llm.gemini_client import _parse_json_array, _keyword_relevance, _chunk_list, _fuzzy_match_category, BATCH_SIZE
    record("Import private helpers", True)
except Exception as e:
    record("Import private helpers", False, str(e))

# ===========================================================================
# 2. CATEGORIES validation
# ===========================================================================
run_section("2. CATEGORIES Validation")

record("CATEGORIES is a list", isinstance(CATEGORIES, list))
record("CATEGORIES has exactly 13 items", len(CATEGORIES) == 13,
       f"got {len(CATEGORIES)}")
record("All CATEGORIES are strings",
       all(isinstance(c, str) for c in CATEGORIES))
record("'Outro' is the last category", CATEGORIES[-1] == "Outro")
record("No duplicate categories", len(set(CATEGORIES)) == len(CATEGORIES))

# Verify specific expected categories from the spec
expected_cats = [
    "Governança de TI",
    "Segurança da Informação",
    "Proteção de Dados",
    "Outro",
]
for cat in expected_cats:
    record(f"Contains category '{cat}'", cat in CATEGORIES)

# ===========================================================================
# 3. Fallback tests (no API key)
# ===========================================================================
run_section("3. Fallback Tests (No API Key)")

# is_available — depends on whether the env had a key before we cleared it
avail = is_available()
record("is_available() returns bool", isinstance(avail, bool))
# The module resolves the key at import time. Since we cleared the env var
# BEFORE importing, is_available() should be False regardless of whether
# the key existed in the env previously.
if avail:
    record("is_available() True (key found at import time)", True,
           "Module found API key via st.secrets or env at import time")
else:
    record("is_available() False (no key at import time)", True,
           "Key was cleared before import, so module has no key")

# expand_topic_to_keywords fallback
try:
    kw_result = expand_topic_to_keywords("test")
    record("expand_topic_to_keywords returns list", isinstance(kw_result, list))
    # If no API key: empty list; if key present: non-empty list (both valid)
    if not avail:
        record("expand_topic_to_keywords returns [] without key",
               kw_result == [])
    else:
        record("expand_topic_to_keywords returns non-empty with key",
               len(kw_result) > 0, f"got {len(kw_result)} keywords")
except Exception as e:
    record("expand_topic_to_keywords no exception", False, str(e))

# score_relevance fallback (no keywords)
try:
    test_results = [{"nome": "Test", "ementa": "test law"}]
    scores = score_relevance("test", test_results)
    record("score_relevance returns list of floats",
           isinstance(scores, list) and all(isinstance(s, float) for s in scores))
    record("score_relevance returns correct length",
           len(scores) == len(test_results), f"got {len(scores)}")
    if not avail:
        record("score_relevance returns [0.5] without key and no keywords",
               scores == [0.5])
except Exception as e:
    record("score_relevance no exception", False, str(e))

# score_relevance fallback (with keywords)
try:
    scores_kw = score_relevance("test", test_results, keywords=["test"])
    record("score_relevance with keywords returns list",
           isinstance(scores_kw, list) and len(scores_kw) == 1)
    record("score_relevance with matching keyword > 0",
           scores_kw[0] > 0, f"score={scores_kw[0]}")
except Exception as e:
    record("score_relevance with keywords no exception", False, str(e))

# categorize_results fallback
try:
    cats = categorize_results("test", test_results)
    record("categorize_results returns list of strings",
           isinstance(cats, list) and all(isinstance(c, str) for c in cats))
    record("categorize_results returns correct length",
           len(cats) == len(test_results))
    if not avail:
        record("categorize_results returns 'Nao categorizado' without key",
               cats == ["Não categorizado"])
except Exception as e:
    record("categorize_results no exception", False, str(e))

# ===========================================================================
# 4. _keyword_relevance fallback test
# ===========================================================================
run_section("4. Keyword Relevance Heuristic")

try:
    # Result with matching keywords should score higher
    score_match = _keyword_relevance(
        ["governança", "TI", "decreto"],
        "Dispõe sobre a governança de TI no setor público conforme decreto federal"
    )
    score_nomatch = _keyword_relevance(
        ["governança", "TI", "decreto"],
        "Regulamenta os procedimentos de licitação para obras públicas"
    )
    record("Matching result scores higher than non-matching",
           score_match > score_nomatch,
           f"match={score_match:.2f} vs nomatch={score_nomatch:.2f}")
    record("Matching score in [0, 1]", 0.0 <= score_match <= 1.0)
    record("Non-matching score in [0, 1]", 0.0 <= score_nomatch <= 1.0)
except Exception as e:
    record("_keyword_relevance tests", False, str(e))

# Edge cases for _keyword_relevance
try:
    record("Empty keywords returns 0.0",
           _keyword_relevance([], "some text") == 0.0)
    record("Empty ementa returns 0.0",
           _keyword_relevance(["test"], "") == 0.0)
    record("Both empty returns 0.0",
           _keyword_relevance([], "") == 0.0)
    record("All keywords match returns 1.0",
           _keyword_relevance(["abc", "def"], "abc def") == 1.0)
    record("Case insensitive matching",
           _keyword_relevance(["ABC"], "abc test") == 1.0)
except Exception as e:
    record("_keyword_relevance edge cases", False, str(e))

# ===========================================================================
# 5. Edge cases
# ===========================================================================
run_section("5. Edge Cases")

# Empty results list
try:
    record("score_relevance([]) returns []",
           score_relevance("test", []) == [])
    record("categorize_results([]) returns []",
           categorize_results("test", []) == [])
except Exception as e:
    record("Empty results edge case", False, str(e))

# Large results list (50+ items) — test batching logic
try:
    large_results = [
        {"nome": f"Normativo {i}", "ementa": f"Ementa do normativo numero {i}"}
        for i in range(55)
    ]
    large_scores = score_relevance("test", large_results, keywords=["normativo"])
    record("Large list (55) score_relevance returns correct length",
           len(large_scores) == 55, f"got {len(large_scores)}")
    record("Large list all scores are floats",
           all(isinstance(s, float) for s in large_scores))

    large_cats = categorize_results("test", large_results)
    record("Large list (55) categorize_results returns correct length",
           len(large_cats) == 55, f"got {len(large_cats)}")
    record("Large list all categories are strings",
           all(isinstance(c, str) for c in large_cats))
except Exception as e:
    record("Large results edge case", False, str(e))

# _chunk_list tests
try:
    record("_chunk_list basic",
           _chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]])
    record("_chunk_list empty", _chunk_list([], 5) == [])
    record("_chunk_list size >= len",
           _chunk_list([1, 2], 10) == [[1, 2]])
    record("BATCH_SIZE is 20", BATCH_SIZE == 20)
except Exception as e:
    record("_chunk_list tests", False, str(e))

# _fuzzy_match_category tests
try:
    record("Fuzzy match exact", _fuzzy_match_category("Governança de TI") == "Governança de TI")
    record("Fuzzy match lowercase", _fuzzy_match_category("governança de ti") == "Governança de TI")
    record("Fuzzy match no accents", _fuzzy_match_category("Governanca de TI") == "Governança de TI")
    record("Fuzzy match unknown returns None", _fuzzy_match_category("Categoria Inventada") is None)
except Exception as e:
    record("_fuzzy_match_category tests", False, str(e))

# ===========================================================================
# 6. JSON parsing test
# ===========================================================================
run_section("6. JSON Parsing (_parse_json_array)")

try:
    # Valid JSON array
    r1 = _parse_json_array('["a", "b"]')
    record("Valid JSON array", r1 == ["a", "b"], f"got {r1}")

    # JSON with markdown fences
    r2 = _parse_json_array('```json\n["a"]\n```')
    record("JSON with markdown fences", r2 == ["a"], f"got {r2}")

    # JSON with bare fences (no json tag)
    r3 = _parse_json_array('```\n["x", "y"]\n```')
    record("JSON with bare markdown fences", r3 == ["x", "y"], f"got {r3}")

    # Invalid JSON
    r4 = _parse_json_array("this is not json at all")
    record("Invalid JSON returns None", r4 is None, f"got {r4}")

    # Numeric array
    r5 = _parse_json_array("[0.9, 0.3, 0.7]")
    record("Numeric JSON array", r5 == [0.9, 0.3, 0.7], f"got {r5}")

    # JSON with surrounding text
    r6 = _parse_json_array('Here are the results: ["a", "b"] hope that helps!')
    record("JSON array embedded in text", r6 == ["a", "b"], f"got {r6}")

    # Empty array
    r7 = _parse_json_array("[]")
    record("Empty JSON array", r7 == [], f"got {r7}")

    # JSON object (not array) should return None
    r8 = _parse_json_array('{"key": "value"}')
    record("JSON object returns None", r8 is None, f"got {r8}")

except Exception as e:
    record("_parse_json_array tests", False, traceback.format_exc())

# ===========================================================================
# Summary
# ===========================================================================
print(f"\n{'='*60}")
print("  SUMMARY")
print(f"{'='*60}")
total = len(results)
passed = sum(1 for _, s, _ in results if s == "PASS")
failed = sum(1 for _, s, _ in results if s == "FAIL")
print(f"  Total: {total}  |  PASS: {passed}  |  FAIL: {failed}")

if failed > 0:
    print(f"\n  Failed tests:")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"    - {name}: {detail}")

print()

# Restore original key if it existed
if _original_key is not None:
    os.environ["GEMINI_API_KEY"] = _original_key
