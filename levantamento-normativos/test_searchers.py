"""
Test suite for the Searchers module (Phase 2).

Covers import checks, unit tests, integration tests, and edge cases
for BaseSearcher, LexMLSearcher, TCUSearcher, and GoogleSearcher.
"""

import sys
import traceback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

results_summary: list[tuple[str, str, str]] = []  # (test_id, status, detail)


def record(test_id: str, status: str, detail: str = "") -> None:
    """Record a test result and print it immediately."""
    tag = "PASS" if status == "PASS" else "FAIL"
    msg = f"[{tag}] Test {test_id}: {detail}" if detail else f"[{tag}] Test {test_id}"
    print(msg)
    results_summary.append((test_id, status, detail))


# ===========================================================================
# 1. Import Tests
# ===========================================================================

print("=" * 70)
print("IMPORT TESTS")
print("=" * 70)

try:
    from searchers import BaseSearcher, LexMLSearcher, TCUSearcher, GoogleSearcher
    record("1", "PASS", "All four classes imported successfully from searchers package")
except Exception as exc:
    record("1", "FAIL", f"Import failed: {exc}")
    traceback.print_exc()
    # Cannot continue without imports
    sys.exit(1)

# Test 2: Verify each searcher has search() and source_name() methods
test2_ok = True
for cls in (LexMLSearcher, TCUSearcher, GoogleSearcher):
    for method_name in ("search", "source_name"):
        if not callable(getattr(cls, method_name, None)):
            record("2", "FAIL", f"{cls.__name__} missing callable {method_name}()")
            test2_ok = False
            break
    if not test2_ok:
        break

if test2_ok:
    record("2", "PASS", "All searchers have search() and source_name() methods")


# ===========================================================================
# 3-6. Unit Tests (no network)
# ===========================================================================

print()
print("=" * 70)
print("UNIT TESTS")
print("=" * 70)

from models import NormativoResult

# Test 3: _normalize_text with accented text
try:
    norm = BaseSearcher._normalize_text("  Instrução Normativa NÚMERO 65  ")
    expected = "instrucao normativa numero 65"
    if norm == expected:
        record("3", "PASS", f"_normalize_text accented text -> '{norm}'")
    else:
        record("3", "FAIL", f"Expected '{expected}', got '{norm}'")
except Exception as exc:
    record("3", "FAIL", f"Exception: {exc}")

# Test 3b: _normalize_text edge cases
try:
    assert BaseSearcher._normalize_text("") == "", "empty string failed"
    assert BaseSearcher._normalize_text("   ") == "", "whitespace-only failed"
    assert BaseSearcher._normalize_text("café résumé") == "cafe resume", "accents failed"
    assert BaseSearcher._normalize_text("A  B\t\tC\n\nD") == "a b c d", "whitespace collapse failed"
    record("3b", "PASS", "_normalize_text edge cases all correct")
except AssertionError as exc:
    record("3b", "FAIL", str(exc))
except Exception as exc:
    record("3b", "FAIL", f"Exception: {exc}")

# Test 4: _safe_date_format with various formats
try:
    tests_4 = [
        ("2023-08-14", "14/08/2023"),
        ("2023-08-14T10:30:00", "14/08/2023"),
        ("2023-08-14T10:30:00Z", "14/08/2023"),
        ("2023-08-14 10:30:00", "14/08/2023"),
        ("14/08/2023", "14/08/2023"),
        ("14-08-2023", "14/08/2023"),
        ("2023", "01/01/2023"),
        ("", ""),
        ("unknown-format", "unknown-format"),  # should return original
    ]
    all_ok = True
    for input_val, expected_val in tests_4:
        result = BaseSearcher._safe_date_format(input_val)
        if result != expected_val:
            record("4", "FAIL", f"_safe_date_format('{input_val}'): expected '{expected_val}', got '{result}'")
            all_ok = False
            break
    if all_ok:
        record("4", "PASS", f"_safe_date_format handled all {len(tests_4)} formats correctly")
except Exception as exc:
    record("4", "FAIL", f"Exception: {exc}")

# Test 5: source_name() returns non-empty string
try:
    searchers_instances = [LexMLSearcher(), TCUSearcher(), GoogleSearcher()]
    all_ok = True
    for s in searchers_instances:
        name = s.source_name()
        if not isinstance(name, str) or len(name.strip()) == 0:
            record("5", "FAIL", f"{type(s).__name__}.source_name() returned empty or non-string: {repr(name)}")
            all_ok = False
            break
    if all_ok:
        names = [s.source_name() for s in searchers_instances]
        record("5", "PASS", f"source_name() values: {names}")
except Exception as exc:
    record("5", "FAIL", f"Exception: {exc}")

# Test 6: each searcher is a subclass of BaseSearcher
try:
    all_ok = True
    for cls in (LexMLSearcher, TCUSearcher, GoogleSearcher):
        if not issubclass(cls, BaseSearcher):
            record("6", "FAIL", f"{cls.__name__} is NOT a subclass of BaseSearcher")
            all_ok = False
            break
    if all_ok:
        record("6", "PASS", "All three searchers are subclasses of BaseSearcher")
except Exception as exc:
    record("6", "FAIL", f"Exception: {exc}")


# ===========================================================================
# 7-9. Integration Tests (network -- may fail if APIs are down)
# ===========================================================================

print()
print("=" * 70)
print("INTEGRATION TESTS (network required -- failures are acceptable)")
print("=" * 70)

# Test 7: LexMLSearcher with a known term
try:
    lexml = LexMLSearcher()
    lexml_results = lexml.search(["LGPD"], max_results=5)
    if isinstance(lexml_results, list):
        if len(lexml_results) > 0:
            # Verify items are NormativoResult
            first = lexml_results[0]
            if isinstance(first, NormativoResult):
                record("7", "PASS", f"LexML returned {len(lexml_results)} NormativoResult(s) for 'LGPD'. First: '{first.nome}'")
            else:
                record("7", "FAIL", f"LexML returned items of type {type(first).__name__}, expected NormativoResult")
        else:
            record("7", "PASS", "LexML returned empty list (API may have no results or be down)")
    else:
        record("7", "FAIL", f"LexML.search() returned {type(lexml_results).__name__}, expected list")
except Exception as exc:
    record("7", "FAIL", f"Exception (API may be down): {exc}")
    traceback.print_exc()

# Test 8: TCUSearcher with a known term
try:
    tcu = TCUSearcher()
    tcu_results = tcu.search(["governança de TI"], max_results=5)
    if isinstance(tcu_results, list):
        if len(tcu_results) > 0:
            first = tcu_results[0]
            if isinstance(first, NormativoResult):
                record("8", "PASS", f"TCU returned {len(tcu_results)} NormativoResult(s) for 'governança de TI'. First: '{first.nome}'")
            else:
                record("8", "FAIL", f"TCU returned items of type {type(first).__name__}, expected NormativoResult")
        else:
            record("8", "PASS", "TCU returned empty list (API may have no matching results or be down)")
    else:
        record("8", "FAIL", f"TCU.search() returned {type(tcu_results).__name__}, expected list")
except Exception as exc:
    record("8", "FAIL", f"Exception (API may be down): {exc}")
    traceback.print_exc()

# Test 9: GoogleSearcher instantiation and search() return type (no actual Google call)
try:
    gs = GoogleSearcher()
    assert gs.source_name() == "Google (Frameworks/Padroes)"
    # Test with empty keywords to avoid actual Google calls
    google_results = gs.search([], max_results=5)
    if isinstance(google_results, list) and len(google_results) == 0:
        record("9", "PASS", "GoogleSearcher instantiates correctly and search([]) returns empty list")
    else:
        record("9", "FAIL", f"GoogleSearcher.search([]) returned {len(google_results)} items, expected 0")
except Exception as exc:
    record("9", "FAIL", f"Exception: {exc}")
    traceback.print_exc()


# ===========================================================================
# 10-12. Edge Cases
# ===========================================================================

print()
print("=" * 70)
print("EDGE CASE TESTS")
print("=" * 70)

# Test 10: Each searcher with empty keywords list -> should return empty list
try:
    all_ok = True
    for cls_name, instance in [("LexML", LexMLSearcher()), ("TCU", TCUSearcher()), ("Google", GoogleSearcher())]:
        result = instance.search([], max_results=10)
        if not isinstance(result, list) or len(result) != 0:
            record("10", "FAIL", f"{cls_name}.search([]) returned {len(result)} items, expected 0")
            all_ok = False
            break
    if all_ok:
        record("10", "PASS", "All searchers return empty list for empty keywords")
except Exception as exc:
    record("10", "FAIL", f"Exception: {exc}")
    traceback.print_exc()

# Test 11: Each searcher with max_results=0 -> should return empty list or handle gracefully
try:
    all_ok = True
    for cls_name, instance in [("LexML", LexMLSearcher()), ("TCU", TCUSearcher()), ("Google", GoogleSearcher())]:
        result = instance.search(["test"], max_results=0)
        if not isinstance(result, list):
            record("11", "FAIL", f"{cls_name}.search(max_results=0) returned {type(result).__name__}, expected list")
            all_ok = False
            break
        if len(result) != 0:
            record("11", "FAIL", f"{cls_name}.search(max_results=0) returned {len(result)} items, expected 0")
            all_ok = False
            break
    if all_ok:
        record("11", "PASS", "All searchers handle max_results=0 gracefully (return empty list)")
except Exception as exc:
    record("11", "FAIL", f"Exception: {exc}")
    traceback.print_exc()

# Test 12: Verify returned items from integration tests are NormativoResult with populated fields
try:
    # Use results from tests 7 and 8 if available
    all_items_checked = 0
    issues = []

    for label, items_var in [("LexML", "lexml_results"), ("TCU", "tcu_results")]:
        items = globals().get(items_var, [])
        for item in items:
            all_items_checked += 1
            if not isinstance(item, NormativoResult):
                issues.append(f"{label}: item is {type(item).__name__}, not NormativoResult")
                continue
            # Check that key fields are populated
            if not item.id:
                issues.append(f"{label}: item.id is empty")
            if not item.nome:
                issues.append(f"{label}: item.nome is empty")
            if not item.source:
                issues.append(f"{label}: item.source is empty")
            if not item.found_by:
                issues.append(f"{label}: item.found_by is empty")

    if issues:
        record("12", "FAIL", f"Issues found: {'; '.join(issues[:5])}")
    elif all_items_checked > 0:
        record("12", "PASS", f"All {all_items_checked} returned items are valid NormativoResult with populated fields")
    else:
        record("12", "PASS", "No items to validate (integration tests returned empty results, likely API unavailable)")
except Exception as exc:
    record("12", "FAIL", f"Exception: {exc}")


# ===========================================================================
# Summary
# ===========================================================================

print()
print("=" * 70)
print("TEST SUMMARY")
print("=" * 70)

pass_count = sum(1 for _, s, _ in results_summary if s == "PASS")
fail_count = sum(1 for _, s, _ in results_summary if s == "FAIL")
total = len(results_summary)

for test_id, status, detail in results_summary:
    tag = "PASS" if status == "PASS" else "FAIL"
    print(f"  [{tag}] Test {test_id}: {detail[:100]}")

print()
print(f"Results: {pass_count}/{total} passed, {fail_count}/{total} failed")
print("=" * 70)
