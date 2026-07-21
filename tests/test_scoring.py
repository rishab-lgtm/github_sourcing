"""
Unit tests for candidate scoring and match reason explainability.
Zach: "Unit-test scoring, matching."
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_sourcing import compute_signal_score


def _profile(**kwargs):
    base = {
        "handle": "testuser",
        "followers": 0,
        "public_repos": 0,
        "top_repos": "",
        "bio": "",
        "contributes_to_ai": False,
        "location": "",
        "account_age_years": 5,
    }
    base.update(kwargs)
    return base


class TestSignalScore:
    def test_returns_tuple_of_score_and_reasons(self):
        result = compute_signal_score(_profile())
        assert isinstance(result, tuple), "Must return (score, reasons)"
        assert len(result) == 2
        score, reasons = result
        assert isinstance(score, int)
        assert isinstance(reasons, list)

    def test_higher_followers_yields_higher_score(self):
        low = compute_signal_score(_profile(followers=10))[0]
        high = compute_signal_score(_profile(followers=5000))[0]
        assert high > low

    def test_ai_contributions_boost_score(self):
        no_ai = compute_signal_score(_profile(followers=100, contributes_to_ai=False))[0]
        with_ai = compute_signal_score(_profile(followers=100, contributes_to_ai=True))[0]
        assert with_ai > no_ai

    def test_sf_location_boosts_score(self):
        no_sf = compute_signal_score(_profile(followers=100), is_sf=False)[0]
        with_sf = compute_signal_score(_profile(followers=100), is_sf=True)[0]
        assert with_sf >= no_sf

    def test_search_terms_in_bio_boost_score(self):
        no_match = compute_signal_score(_profile(bio="I like dogs"), search_terms=["genomics"])[0]
        match = compute_signal_score(_profile(bio="I work on genomics and drug discovery"), search_terms=["genomics"])[0]
        assert match >= no_match

    def test_reasons_are_non_empty_strings(self):
        _, reasons = compute_signal_score(_profile(followers=500, contributes_to_ai=True))
        assert len(reasons) > 0
        assert all(isinstance(r, str) and len(r) > 0 for r in reasons)

    def test_reasons_explain_the_score(self):
        _, reasons = compute_signal_score(_profile(followers=2000))
        combined = " ".join(reasons).lower()
        assert any(kw in combined for kw in ["follower", "popular", "active", "contributor", "repos"])

    def test_score_is_non_negative(self):
        score, _ = compute_signal_score(_profile())
        assert score >= 0

    def test_zero_activity_scores_low(self):
        score, _ = compute_signal_score(_profile(followers=0, public_repos=0))
        assert score < 60, "Account with no activity should not score as high-signal"

    def test_star_rich_repos_boost_score(self):
        no_stars = compute_signal_score(_profile(top_repos="myrepo (0⭐)"))[0]
        many_stars = compute_signal_score(_profile(top_repos="awesome-ml (3000⭐), infra-tool (800⭐)"))[0]
        assert many_stars > no_stars
