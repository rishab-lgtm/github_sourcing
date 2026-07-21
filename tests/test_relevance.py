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


def test_robotics_deployment_requires_both_dimensions():
    academic_roboticist = _profile(
        bio="Robotics researcher working on manipulation and motion planning"
    )
    cloud_deployer = _profile(
        bio="Deployment engineer operating production cloud infrastructure"
    )
    field_engineer = _profile(
        bio="Robotics deployment engineer field-testing autonomous robot fleets"
    )

    assert not sourcing.candidate_matches_intent(
        "robotics deployment engineers", academic_roboticist
    )
    assert not sourcing.candidate_matches_intent(
        "robotics deployment engineers", cloud_deployer
    )
    assert sourcing.candidate_matches_intent(
        "robotics deployment engineers", field_engineer
    )


def test_robotics_deployment_builds_combined_search_queries():
    queries = sourcing.build_github_user_queries("robotics deployment engineers")
    assert queries
    robotics_terms = ["robot", "autonomous", "ros", "manipulat", "lidar", "slam"]
    assert all(
        any(term in query.lower() for term in robotics_terms)
        for query in queries
    )
    assert any(
        term in " ".join(queries).lower()
        for term in ["deployment", "field robotics", "robot fleet", "field testing"]
    )


def test_intent_fit_outranks_popularity_for_deployment_search():
    field_engineer = _profile(
        bio="Robotics deployment engineer field-testing autonomous robot fleets"
    )
    result = sourcing.format_profile(
        field_engineer,
        intent="robotics deployment engineers",
        search_terms=sourcing.expand_query("robotics deployment engineers"),
    )
    assert result["signal_score"] >= 30
    assert any("Robotics evidence" in reason for reason in result["match_reasons"])
    assert any("Deployment evidence" in reason for reason in result["match_reasons"])


def test_relevant_org_repo_contributors_are_not_skipped():
    repo = {
        "full_name": "robot-company/fleet-deploy",
        "name": "fleet-deploy",
        "description": "Robotics deployment and robot fleet management tooling",
        "topics": ["robotics", "deployment"],
        "owner": {"login": "robot-company"},
        "stargazers_count": 100,
    }
    contributor = _profile(
        login="field-builder",
        bio="Engineer integrating and deploying autonomous systems",
    )

    def fake_paginate(url, params=None, max_pages=3):
        if "search/repositories" in url:
            return [repo]
        return []

    def fake_profile(username):
        if username == "robot-company":
            return {"login": username, "type": "Organization"}
        return contributor.copy()

    def fake_get(url, params=None, retries=None):
        if url.endswith("/contributors"):
            return [{"login": "field-builder"}]
        return None

    with patch.object(sourcing, "_paginate", side_effect=fake_paginate), \
         patch.object(sourcing, "get_user_profile", side_effect=fake_profile), \
         patch.object(sourcing, "get_user_repos", return_value=[]), \
         patch.object(sourcing, "_get", side_effect=fake_get):
        results = sourcing.search_by_intent(
            "robotics deployment engineers", max_results=10
        )

    assert [candidate["handle"] for candidate in results] == ["field-builder"]
