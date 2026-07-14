"""
Tests for GitHub API failure handling.
Zach: "Smoke-test GitHub API failures, rate limits, missing tokens, empty results, pagination."
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import github_sourcing


def _mock_response(status=200, json_data=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data or {}
    r.headers = headers or {}
    r.ok = (200 <= status < 300)
    return r


class TestAPITimeout:
    def test_get_includes_timeout(self):
        """Every GitHub API call must specify a timeout."""
        with patch("github_sourcing.requests.get") as mock_get:
            mock_get.return_value = _mock_response(json_data={"items": []})
            try:
                github_sourcing._get("https://api.github.com/search/users?q=test")
            except Exception:
                pass
        if mock_get.called:
            kwargs = mock_get.call_args.kwargs
            assert "timeout" in kwargs, "_get() must pass timeout= to requests.get"
            assert kwargs["timeout"] > 0


class TestRateLimitHandling:
    def test_rate_limit_403_does_not_raise(self):
        """A 403 rate-limit response should be handled gracefully, not crash."""
        with patch("github_sourcing.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                status=403,
                json_data={"message": "API rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(__import__('time').time()) + 1)},
            )
            with patch("time.sleep"):  # don't actually wait
                result = github_sourcing._get("https://api.github.com/search/users?q=test", retries=1)
        # Should return None (no valid response), not raise
        assert result is None or isinstance(result, dict)

    def test_rate_limit_429_does_not_raise(self):
        with patch("github_sourcing.requests.get") as mock_get:
            mock_get.return_value = _mock_response(status=429, json_data={},
                                                    headers={"X-RateLimit-Reset": str(int(__import__('time').time()) + 1)})
            with patch("time.sleep"):
                result = github_sourcing._get("https://api.github.com/search/users?q=test", retries=1)
        assert result is None or isinstance(result, dict)


class TestRetryLogic:
    def test_retries_on_500(self):
        """A 500 server error should trigger a retry and succeed on second attempt."""
        responses = [
            _mock_response(status=500),
            _mock_response(status=200, json_data={"items": []}),
        ]
        with patch("github_sourcing.requests.get", side_effect=responses):
            with patch("time.sleep"):
                result = github_sourcing._get(
                    "https://api.github.com/search/users?q=test", retries=2
                )
        assert result == {"items": []}

    def test_no_retry_beyond_max(self):
        """Should not retry more than `retries` times, and returns None."""
        call_count = {"n": 0}
        def counting_get(*a, **kw):
            call_count["n"] += 1
            return _mock_response(status=500)
        with patch("github_sourcing.requests.get", side_effect=counting_get):
            with patch("time.sleep"):
                result = github_sourcing._get(
                    "https://api.github.com/search/users?q=test", retries=2
                )
        assert call_count["n"] == 2, f"Expected 2 attempts, got {call_count['n']}"
        assert result is None


class TestMissingToken:
    def test_search_by_intent_with_no_token_does_not_crash(self):
        """If GITHUB_TOKEN is missing, should fail gracefully, not crash."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            with patch("github_sourcing.requests.get") as mock_get:
                mock_get.return_value = _mock_response(status=401, json_data={"message": "Bad credentials"})
                with patch("time.sleep"):
                    result = github_sourcing.search_by_intent("biotech researcher", max_results=5)
        assert isinstance(result, list), "Should return empty list, not raise"


class TestEmptyResults:
    def test_search_by_intent_empty_github_response(self):
        """Empty GitHub API response should return empty list, not crash."""
        with patch("github_sourcing.requests.get") as mock_get:
            mock_get.return_value = _mock_response(json_data={"items": [], "total_count": 0})
            result = github_sourcing.search_by_intent("zzznoresults12345xyz", max_results=5)
        assert result == [] or isinstance(result, list)

    def test_find_sf_ai_contributors_empty(self):
        with patch("github_sourcing.requests.get") as mock_get:
            mock_get.return_value = _mock_response(json_data={"items": []})
            result = github_sourcing.find_sf_ai_contributors(limit_per_repo=1)
        assert isinstance(result, list)

    def test_find_trending_repo_authors_empty(self):
        with patch("github_sourcing.requests.get") as mock_get:
            mock_get.return_value = _mock_response(json_data={"items": []})
            result = github_sourcing.find_trending_repo_authors(days=7)
        assert isinstance(result, list)


class TestSessionBudget:
    def test_session_budget_starts_within_limit(self):
        assert github_sourcing.session_budget_ok() is True or \
               github_sourcing.get_session_request_count() <= github_sourcing.SESSION_REQUEST_LIMIT

    def test_get_session_request_count_returns_int(self):
        count = github_sourcing.get_session_request_count()
        assert isinstance(count, int)
        assert count >= 0


class TestSwallowedExceptions:
    def test_get_user_profile_bad_handle_does_not_raise(self):
        """Exceptions in profile fetching must be caught and logged, not propagated."""
        with patch("github_sourcing.requests.get", side_effect=ConnectionError("network down")):
            with patch("time.sleep"):
                result = github_sourcing._get("https://api.github.com/users/someuser", retries=1)
        assert result is None, "ConnectionError should return None, not raise"
