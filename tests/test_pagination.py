"""
Tests for _paginate() — Zach: smoke-test pagination.
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import github_sourcing


def _search_response(items, total=100):
    return {"items": items, "total_count": total}


def _list_response(items):
    return items


class TestPaginateSearchEndpoints:
    def test_fetches_page_2_when_page_1_is_full(self):
        """If page 1 returns per_page items, page 2 must be fetched."""
        page1 = _search_response([{"login": f"user{i}"} for i in range(30)])
        page2 = _search_response([{"login": f"user{i}"} for i in range(30, 45)])

        with patch("github_sourcing._get", side_effect=[page1, page2]) as mock_get:
            results = github_sourcing._paginate(
                "https://api.github.com/search/users",
                params={"q": "biotech", "per_page": 30},
                max_pages=3,
            )

        assert mock_get.call_count == 2, "Should fetch page 2 when page 1 is full"
        assert len(results) == 45

    def test_stops_after_partial_page(self):
        """If page 1 returns fewer than per_page items, stop — no page 2 fetch."""
        page1 = _search_response([{"login": f"user{i}"} for i in range(12)])

        with patch("github_sourcing._get", return_value=page1) as mock_get:
            results = github_sourcing._paginate(
                "https://api.github.com/search/users",
                params={"q": "biotech", "per_page": 30},
                max_pages=3,
            )

        assert mock_get.call_count == 1, "Partial page means no more results — stop fetching"
        assert len(results) == 12

    def test_respects_max_pages(self):
        """Must not fetch more pages than max_pages even if each page is full."""
        full_page = _search_response([{"login": f"user{i}"} for i in range(30)])

        with patch("github_sourcing._get", return_value=full_page) as mock_get:
            results = github_sourcing._paginate(
                "https://api.github.com/search/users",
                params={"q": "biotech", "per_page": 30},
                max_pages=2,
            )

        assert mock_get.call_count == 2, f"Expected 2 pages (max_pages=2), got {mock_get.call_count}"
        assert len(results) == 60

    def test_stops_on_none_response(self):
        """If _get returns None (API failure), stop pagination gracefully."""
        with patch("github_sourcing._get", return_value=None):
            results = github_sourcing._paginate(
                "https://api.github.com/search/users",
                params={"q": "biotech", "per_page": 30},
                max_pages=3,
            )
        assert results == []

    def test_handles_list_endpoint(self):
        """Contributors endpoint returns a plain list, not {items: []}."""
        page1 = [{"login": f"contrib{i}"} for i in range(30)]
        page2 = [{"login": f"contrib{i}"} for i in range(30, 38)]

        with patch("github_sourcing._get", side_effect=[page1, page2]) as mock_get:
            results = github_sourcing._paginate(
                "https://api.github.com/repos/some/repo/contributors",
                params={"per_page": 30},
                max_pages=3,
            )

        assert mock_get.call_count == 2
        assert len(results) == 38

    def test_page_numbers_increment(self):
        """Each successive _get call must use an incremented page= param."""
        full_page = _search_response([{"login": f"u{i}"} for i in range(30)])
        partial_page = _search_response([{"login": "last"}])
        called_pages = []

        def fake_get(url, params=None, retries=None):
            called_pages.append(params.get("page"))
            return full_page if params.get("page", 1) < 2 else partial_page

        with patch("github_sourcing._get", side_effect=fake_get):
            github_sourcing._paginate(
                "https://api.github.com/search/users",
                params={"q": "test", "per_page": 30},
                max_pages=3,
            )

        assert called_pages == [1, 2], f"Pages must increment: expected [1, 2], got {called_pages}"

    def test_empty_items_stops_immediately(self):
        """An empty items list means no more results — no further pages."""
        with patch("github_sourcing._get", return_value=_search_response([])) as mock_get:
            results = github_sourcing._paginate(
                "https://api.github.com/search/users",
                params={"q": "zzz", "per_page": 30},
                max_pages=5,
            )

        assert mock_get.call_count == 1
        assert results == []


class TestSearchByIntentUsesPagination:
    def test_search_by_intent_calls_paginate_not_get_directly(self):
        """
        search_by_intent must use _paginate for strategies 1 and 2,
        not single _get calls capped at per_page=30.
        """
        with patch("github_sourcing._paginate", return_value=[]) as mock_paginate, \
             patch("github_sourcing._get", return_value=None):
            github_sourcing.search_by_intent("biotech AI researcher", max_results=10)

        # _paginate must be called for the user search and repo search endpoints
        paginate_urls = [call.args[0] for call in mock_paginate.call_args_list]
        assert any("search/users" in url for url in paginate_urls), \
            "Strategy 1 (user bio search) must use _paginate, not single _get"
        assert any("search/repositories" in url for url in paginate_urls), \
            "Strategy 2 (repo search) must use _paginate, not single _get"
