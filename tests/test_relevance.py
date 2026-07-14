"""Regression tests for intent relevance and evidence gating."""

from unittest.mock import patch

import github_sourcing as sourcing


def _profile(login="person", bio="AI engineer", repos=None):
    return {
        "login": login,
        "type": "User",
        "name": login,
        "bio": bio,
        "company": "",
        "location": "",
        "followers": 10,
        "public_repos": 2,
        "html_url": f"https://github.com/{login}",
        "top_repos": repos or [],
    }


def test_generic_ai_profile_is_not_a_biotech_match():
    profile = _profile(bio="LLM inference and distributed systems")
    assert not sourcing.candidate_matches_intent("biotech AI researcher", profile)


def test_biotech_repository_is_concrete_match_evidence():
    profile = _profile(bio="machine learning researcher")
    evidence = "protein-lab/folding-tools Protein design and genomics models"
    assert sourcing.candidate_matches_intent("biotech AI researcher", profile, evidence)


def test_search_filters_irrelevant_popular_profiles():
    generic = _profile(login="generic-ai", bio="LLM infrastructure")
    biotech = _profile(login="bio-researcher", bio="computational biology and genomics")

    def fake_profile(username):
        return generic.copy() if username == "generic-ai" else biotech.copy()

    def fake_paginate(url, params=None, max_pages=3):
        if "search/users" in url:
            return [{"login": "generic-ai"}, {"login": "bio-researcher"}]
        return []

    with patch.object(sourcing, "_paginate", side_effect=fake_paginate), \
         patch.object(sourcing, "get_user_profile", side_effect=fake_profile), \
         patch.object(sourcing, "get_user_repos", return_value=[]):
        results = sourcing.search_by_intent("biotech AI researcher", max_results=10)

    assert [candidate["handle"] for candidate in results] == ["bio-researcher"]
    assert any(
        "genomics" in reason or "computational biology" in reason
        for reason in results[0]["match_reasons"]
    )


def test_discovery_queries_are_domain_first_and_deterministic():
    terms = sourcing.discovery_terms("biotech AI researcher")
    assert terms[0] == "biotech"
    assert terms == sourcing.discovery_terms("biotech AI researcher")
    assert "biotech" in sourcing.build_github_repo_query("biotech AI researcher")
