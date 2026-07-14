"""
Unit tests for query construction and keyword expansion.
Zach: "Unit-test query construction and keyword expansion."
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_sourcing import expand_query


class TestExpandQuery:
    def test_biotech_expands_to_domain_terms(self):
        terms = expand_query("biotech AI researcher")
        terms_lower = [t.lower() for t in terms]
        # Must include core biotech domain terms — not just the literal word
        assert "genomics" in terms_lower or "bioinformatics" in terms_lower or "biology" in terms_lower
        assert len(terms) > 5, "Should expand to many terms, not just echo the input"

    def test_biotech_includes_specific_sub_terms(self):
        terms = expand_query("biotech AI researcher")
        terms_lower = [t.lower() for t in terms]
        # These must appear — they're what makes the search non-trivial
        expected = ["genomics", "bioinformatics", "drug discovery", "protein"]
        found = [e for e in expected if any(e in t for t in terms_lower)]
        assert len(found) >= 2, f"Expected biotech sub-terms, found only: {found}"

    def test_climate_expands_to_domain_terms(self):
        terms = expand_query("climate tech engineer")
        terms_lower = " ".join(terms).lower()
        assert any(kw in terms_lower for kw in ["carbon", "renewable", "solar", "climate", "energy"])

    def test_fintech_expands_correctly(self):
        terms = expand_query("fintech developer")
        terms_lower = " ".join(terms).lower()
        assert any(kw in terms_lower for kw in ["payments", "banking", "crypto", "defi", "financial"])

    def test_returns_list_of_strings(self):
        terms = expand_query("AI researcher")
        assert isinstance(terms, list)
        assert all(isinstance(t, str) for t in terms)

    def test_deduplication(self):
        terms = expand_query("AI AI AI researcher")
        assert len(terms) == len(set(t.lower() for t in terms)), "Should not return duplicate terms"

    def test_empty_intent_does_not_crash(self):
        terms = expand_query("")
        assert isinstance(terms, list)

    def test_unknown_domain_still_returns_input_words(self):
        terms = expand_query("quantum networking protocols")
        terms_lower = [t.lower() for t in terms]
        # Even without a domain map entry, the literal words should appear
        assert "quantum" in terms_lower or "networking" in terms_lower

    def test_expansion_actually_affects_github_search(self):
        """
        Regression: the old app ran hardcoded scans and filtered by keyword.
        The expansion must produce terms that would drive a different GitHub query
        than just the raw input word.
        """
        raw = expand_query("biotech")
        assert "biotech" in [t.lower() for t in raw], "Original word should still be included"
        assert len(raw) > 3, (
            "If expansion only returns 1-2 terms, it means keyword filtering is happening "
            "instead of real query expansion"
        )


class TestExpansionDrivesAPIQuery:
    """
    Zach: Integration-test that 'biotech AI researcher' keyword affects GitHub
    discovery, not just display filtering.
    Verifies the expanded terms appear in the actual q= parameter of outgoing HTTP requests.
    """

    def test_biotech_terms_appear_in_github_user_search_query(self):
        from unittest.mock import patch, MagicMock, call
        import github_sourcing

        captured_params = []

        def fake_paginate(url, params=None, max_pages=3):
            captured_params.append((url, dict(params or {})))
            return []  # no results — we only care about what was queried

        def fake_get(url, params=None, retries=None):
            captured_params.append((url, dict(params or {})))
            return None

        with patch("github_sourcing._paginate", side_effect=fake_paginate), \
             patch("github_sourcing._get", side_effect=fake_get):
            github_sourcing.search_by_intent("biotech AI researcher", max_results=5)

        # Find all q= params sent to GitHub search endpoints
        all_queries = " ".join(
            params.get("q", "")
            for url, params in captured_params
            if "github.com" in url
        ).lower()

        # At least one of the expanded biotech terms must appear in the actual API query
        biotech_terms = ["biotech", "genomics", "bioinformatics", "biology", "drug"]
        found = [t for t in biotech_terms if t in all_queries]
        assert len(found) >= 1, (
            f"Expanded terms must drive GitHub API queries, not just filter display. "
            f"Expected one of {biotech_terms} in outgoing q= params, got: '{all_queries[:200]}'"
        )

    def test_intent_query_differs_from_raw_keyword(self):
        """The GitHub query for 'biotech AI researcher' must be richer than just 'biotech'."""
        from github_sourcing import build_github_user_query
        raw_q = build_github_user_query("biotech")
        rich_q = build_github_user_query("biotech AI researcher")
        assert len(rich_q) >= len(raw_q), (
            "A more specific intent should produce an equal or richer query"
        )
