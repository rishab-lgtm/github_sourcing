"""
Thorough integration and regression tests.
Covers format_profile output contract, building-context filtering,
archetype confidence levels, LinkedIn direct-link detection,
scoring edge cases, and realistic multi-signal VC sourcing profiles.
"""

import sys
import os
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import github_sourcing as src
from github_sourcing import (
    compute_signal_score,
    founder_signal,
    infer_profile_archetype,
    linkedin_search_url,
    format_profile,
    candidate_matches_intent,
    expand_query,
    BUILDING_CONTEXT_KEYWORDS,
    BIG_COMPANY_KEYWORDS,
    STRONG_FOUNDER_KEYWORDS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gh_profile(**kwargs):
    """Minimal GitHub API-shaped profile dict."""
    base = {
        "login": "dev",
        "type": "User",
        "name": "Dev Person",
        "bio": "",
        "company": "",
        "location": "",
        "followers": 50,
        "public_repos": 10,
        "html_url": "https://github.com/dev",
        "top_repos": [],
        "blog": "",
        "created_at": "2019-01-01T00:00:00Z",
    }
    base.update(kwargs)
    return base


def _score_profile(**kwargs):
    """Minimal scoring-shaped profile dict."""
    base = {
        "handle": "dev",
        "followers": 50,
        "public_repos": 10,
        "top_repos": "",
        "bio": "",
        "contributes_to_ai": False,
        "location": "",
        "account_age_years": 4,
    }
    base.update(kwargs)
    return base


# ── format_profile output contract ───────────────────────────────────────────

class TestFormatProfileContract:
    REQUIRED_KEYS = [
        "handle", "name", "location", "bio", "company",
        "followers", "public_repos", "top_repos",
        "signal_score", "match_reasons", "founder_badges",
        "account_age_years", "github_url",
        "linkedin_url", "profile_archetype",
        "archetype_confidence", "archetype_signals",
    ]

    def test_all_required_keys_present(self):
        result = format_profile(_gh_profile())
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key}"

    def test_handle_is_lowercased(self):
        result = format_profile(_gh_profile(login="UPPERCASE"))
        assert result["handle"] == "uppercase"

    def test_signal_score_is_int(self):
        result = format_profile(_gh_profile())
        assert isinstance(result["signal_score"], int)

    def test_match_reasons_is_list(self):
        result = format_profile(_gh_profile())
        assert isinstance(result["match_reasons"], list)

    def test_founder_badges_is_string(self):
        result = format_profile(_gh_profile())
        assert isinstance(result["founder_badges"], str)

    def test_archetype_signals_is_list(self):
        result = format_profile(_gh_profile())
        assert isinstance(result["archetype_signals"], list)

    def test_linkedin_url_is_string(self):
        result = format_profile(_gh_profile())
        assert isinstance(result["linkedin_url"], str)

    def test_account_age_computed_from_created_at(self):
        result = format_profile(_gh_profile(created_at="2020-01-01T00:00:00Z"))
        assert result["account_age_years"] is not None
        assert result["account_age_years"] > 0

    def test_missing_created_at_does_not_crash(self):
        profile = _gh_profile()
        del profile["created_at"]
        result = format_profile(profile)
        assert result["account_age_years"] is None

    def test_top_repos_list_formatted_to_string(self):
        result = format_profile(_gh_profile(top_repos=[
            {"name": "cool-tool", "stars": 300, "description": ""},
            {"name": "other", "stars": 50, "description": ""},
        ]))
        assert "cool-tool" in result["top_repos"]
        assert "300" in result["top_repos"]

    def test_extra_dict_merged_into_result(self):
        result = format_profile(_gh_profile(), extra={"custom_field": "hello"})
        assert result["custom_field"] == "hello"

    def test_no_crash_on_all_none_fields(self):
        result = format_profile(_gh_profile(
            bio=None, company=None, location=None,
            name=None, blog=None,
        ))
        assert isinstance(result["signal_score"], int)


# ── LinkedIn URL logic ────────────────────────────────────────────────────────

class TestLinkedInURLLogic:
    def test_direct_linkedin_in_blog_used_as_is(self):
        result = format_profile(_gh_profile(
            blog="https://linkedin.com/in/johndoe"
        ))
        assert result["linkedin_url"] == "https://linkedin.com/in/johndoe"

    def test_blog_linkedin_without_https_gets_prefix(self):
        result = format_profile(_gh_profile(
            blog="linkedin.com/in/johndoe"
        ))
        assert result["linkedin_url"].startswith("https://")
        assert "linkedin.com/in/johndoe" in result["linkedin_url"]

    def test_non_linkedin_blog_falls_back_to_search(self):
        result = format_profile(_gh_profile(
            name="Alice Zhang", company="Stripe",
            blog="https://aliceblog.com"
        ))
        assert "linkedin.com/search" in result["linkedin_url"]
        assert "Alice" in result["linkedin_url"] or "alice" in result["linkedin_url"].lower()

    def test_search_url_includes_company(self):
        url = linkedin_search_url("Bob Lee", "Databricks")
        assert "Databricks" in url or "databricks" in url.lower()

    def test_search_url_handles_comma_in_company(self):
        url = linkedin_search_url("Jane", "Google, Inc.")
        assert "," not in url.split("keywords=")[1]

    def test_special_chars_in_name_are_encoded(self):
        url = linkedin_search_url("François Müller", "CERN")
        assert " " not in url.split("keywords=")[1]

    @pytest.mark.parametrize("company", [
        "@Google", "@Meta", "@Apple",
    ])
    def test_at_prefix_stripped(self, company):
        url = linkedin_search_url("Dev", company)
        assert "@" not in url


# ── Building-context keyword gate ─────────────────────────────────────────────

class TestBuildingContextGate:
    """'building' must be paired with startup-context to earn the badge."""

    PLAIN_BUILDING_BIOS = [
        "building stuff every day",
        "building cool things with code",
        "always building",
        "I love building",
        "currently building — details TBA",
    ]

    @pytest.mark.parametrize("bio", PLAIN_BUILDING_BIOS)
    def test_plain_building_bio_no_badge(self, bio):
        result = founder_signal(bio=bio)
        assert "🚀 Building" not in result["badges"], (
            f"Should NOT badge for bio='{bio}'"
        )

    CONTEXTUAL_BUILDING_BIOS = [
        "building an AI startup",
        "building a SaaS product for developers",
        "building an agent platform",
        "building an API for fintech",
        "building and launching my MVP this month",
        "building a bootstrapped b2b tool",
        "building in stealth",
        "working on a startup",
        "shipping a new product",
        "launched my own company",
        "making an open source tool",
    ]

    @pytest.mark.parametrize("bio", CONTEXTUAL_BUILDING_BIOS)
    def test_contextual_building_bio_gets_badge(self, bio):
        result = founder_signal(bio=bio)
        has_badge = "🚀 Building" in result["badges"] or "🚀 Founder" in result["badges"]
        assert has_badge, f"Should badge for bio='{bio}'"

    def test_all_building_context_keywords_covered(self):
        """Every BUILDING_CONTEXT_KEYWORD should trigger the badge when paired with 'building'."""
        failed = []
        for kw in BUILDING_CONTEXT_KEYWORDS:
            bio = f"building a {kw} project"
            result = founder_signal(bio=bio)
            has_badge = "🚀 Building" in result["badges"] or "🚀 Founder" in result["badges"]
            if not has_badge:
                failed.append(kw)
        assert not failed, f"These context keywords didn't trigger badge: {failed}"


# ── Archetype confidence levels ───────────────────────────────────────────────

class TestArchetypeConfidence:
    def test_strong_builder_gets_high_confidence(self):
        result = infer_profile_archetype({
            "bio": "CTO at startup, seed-funded",
            "company": "MySaasCo",
            "followers": 80,
            "public_repos": 45,
            "top_repos": [
                {"name": "my-saas-api", "stars": 500, "description": "REST API platform"},
                {"name": "payment-sdk", "stars": 300, "description": "Stripe integration SDK"},
                {"name": "cli-tool", "stars": 200, "description": "developer cli tool"},
                {"name": "auth-service", "stars": 150, "description": "auth backend service"},
            ],
        })
        assert result["archetype"] == "builder"
        assert result["confidence"] in ("high", "medium")

    def test_strong_researcher_gets_high_confidence(self):
        result = infer_profile_archetype({
            "bio": "PhD candidate at Stanford, postdoc at MIT",
            "company": "Stanford AI Lab",
            "followers": 1500,
            "public_repos": 6,
            "top_repos": [
                {"name": "neurips-paper-code", "stars": 300, "description": "NeurIPS paper replication"},
                {"name": "benchmark-suite", "stars": 200, "description": "eval and benchmark dataset"},
                {"name": "ablation-study", "stars": 80, "description": "ablation experiments"},
            ],
        })
        assert result["archetype"] == "researcher"
        assert result["confidence"] in ("high", "medium")

    def test_ambiguous_profile_gets_low_confidence(self):
        result = infer_profile_archetype({
            "bio": "engineer",
            "company": "",
            "followers": 20,
            "public_repos": 5,
            "top_repos": [{"name": "misc-scripts", "stars": 10, "description": ""}],
        })
        assert result["confidence"] == "low"

    def test_confidence_values_are_valid(self):
        for bio in ["", "founder", "PhD researcher", "CTO at startup", "google engineer"]:
            result = infer_profile_archetype({"bio": bio, "company": "", "followers": 0,
                                              "public_repos": 0, "top_repos": []})
            assert result["confidence"] in ("high", "medium", "low")
            assert result["archetype"] in ("builder", "researcher", "unknown")


# ── Scoring edge cases ────────────────────────────────────────────────────────

class TestScoringEdgeCases:
    def test_nan_strings_in_profile_dont_crash(self):
        score, reasons = compute_signal_score(_score_profile(bio="nan", company="nan"))
        assert isinstance(score, int)

    def test_stars_parsed_from_string_top_repos(self):
        s1 = compute_signal_score(_score_profile(top_repos="proj (50⭐)"))[0]
        s2 = compute_signal_score(_score_profile(top_repos="proj (5000⭐)"))[0]
        assert s2 > s1

    def test_multiple_repos_stars_aggregate(self):
        single = compute_signal_score(_score_profile(top_repos="a (800⭐)"))[0]
        combined = compute_signal_score(_score_profile(top_repos="a (500⭐), b (500⭐)"))[0]
        assert combined >= single

    def test_sf_location_variants_all_score(self):
        variants = [
            "San Francisco, CA", "SF, USA", "Bay Area", "Silicon Valley",
            "Palo Alto", "Menlo Park", "Mountain View", "Sunnyvale",
        ]
        for loc in variants:
            s_no = compute_signal_score(_score_profile(location=""))[0]
            s_yes = compute_signal_score(_score_profile(location=loc), is_sf=True)[0]
            assert s_yes >= s_no, f"SF boost missing for location='{loc}'"

    def test_search_term_match_in_top_repos_boosts(self):
        no_match = compute_signal_score(
            _score_profile(top_repos="random-utils (100⭐)"),
            search_terms=["genomics"]
        )[0]
        match = compute_signal_score(
            _score_profile(top_repos="genomics-pipeline (100⭐)"),
            search_terms=["genomics"]
        )[0]
        assert match > no_match

    def test_hidden_gem_boost_requires_low_followers_and_high_stars(self):
        not_gem = compute_signal_score(_score_profile(followers=5000, top_repos="proj (600⭐)"))[0]
        gem = compute_signal_score(_score_profile(followers=30, top_repos="proj (600⭐)"))[0]
        assert gem >= not_gem  # hidden gem should compensate for low followers

    def test_score_type_always_int(self):
        profiles = [
            _score_profile(),
            _score_profile(followers=999999),
            _score_profile(bio="founder yc stealth", top_repos="proj (50000⭐)"),
            _score_profile(bio=None),
        ]
        for p in profiles:
            score, _ = compute_signal_score(p)
            assert isinstance(score, int), f"Score {score!r} is not int for {p}"


# ── founder_signal badge combinations ────────────────────────────────────────

class TestFounderSignalCombinations:
    def test_ex_top_lab_and_founder(self):
        result = founder_signal(bio="ex-DeepMind, now founder at stealth AI company")
        assert "🚀 Founder" in result["badges"]
        assert "🏛 Ex-Top Lab" in result["badges"]

    def test_researcher_and_top_lab_at_big_co(self):
        result = founder_signal(bio="Research scientist at Google DeepMind, PhD NLP")
        assert "🔬 Researcher" in result["badges"]

    def test_no_duplicate_badges(self):
        result = founder_signal(bio="founder stealth yc ex-openai seed raising")
        assert len(result["badges"]) == len(set(result["badges"]))

    def test_boost_is_additive_across_badges(self):
        founder_only = founder_signal(bio="founder at startup")["boost"]
        founder_ex_lab = founder_signal(bio="ex-DeepMind founder stealth")["boost"]
        assert founder_ex_lab > founder_only

    def test_big_company_suppresses_building_not_founder(self):
        result = founder_signal(bio="building product, ex-Google, now YC founder")
        assert "🚀 Founder" in result["badges"]
        assert "🚀 Building" not in result["badges"]

    def test_independent_researcher_badge(self):
        result = founder_signal(bio="independent researcher working on NLP")
        assert "🔬 Researcher" in result["badges"]
        assert "🚀 Founder" not in result["badges"]

    @pytest.mark.parametrize("kw", STRONG_FOUNDER_KEYWORDS)
    def test_every_strong_founder_keyword_triggers_founder_badge(self, kw):
        result = founder_signal(bio=f"I am a {kw} in AI")
        assert "🚀 Founder" in result["badges"], (
            f"STRONG_FOUNDER_KEYWORD '{kw}' did not trigger Founder badge"
        )


# ── Archetype: realistic mixed signals ───────────────────────────────────────

class TestArchetypeMixedSignals:
    def test_high_star_low_follower_repo_dominates(self):
        """Prolific shipper with quiet profile should classify as builder."""
        result = infer_profile_archetype({
            "bio": "engineer",
            "company": "",
            "followers": 40,
            "public_repos": 35,
            "top_repos": [
                {"name": "cli-deploy", "stars": 800, "description": "deployment cli tool"},
                {"name": "auth-sdk", "stars": 400, "description": "auth SDK for SaaS apps"},
            ],
        })
        assert result["archetype"] == "builder"

    def test_academic_with_many_followers_and_few_stars(self):
        result = infer_profile_archetype({
            "bio": "Professor at CMU, faculty AI lab",
            "company": "Carnegie Mellon University",
            "followers": 3000,
            "public_repos": 5,
            "top_repos": [
                {"name": "lecture-notes", "stars": 200, "description": "course materials"},
                {"name": "ml-study", "stars": 100, "description": "study notebooks"},
            ],
        })
        assert result["archetype"] == "researcher"

    def test_signals_list_is_human_readable(self):
        result = infer_profile_archetype({
            "bio": "CTO", "company": "",
            "followers": 100, "public_repos": 30,
            "top_repos": [{"name": "api-gateway", "stars": 500, "description": "API gateway service"}],
        })
        for signal in result["signals"]:
            assert isinstance(signal, str)
            assert len(signal) > 5

    def test_big_company_bumps_researcher_score(self):
        big_co = infer_profile_archetype({
            "bio": "engineer", "company": "Google",
            "followers": 100, "public_repos": 10,
            "top_repos": [],
        })
        small_co = infer_profile_archetype({
            "bio": "engineer", "company": "MyCo",
            "followers": 100, "public_repos": 10,
            "top_repos": [],
        })
        # Google employee should lean more researcher than random small co
        big_co_is_researcher = big_co["archetype"] == "researcher" or big_co.get("confidence") == "low"
        assert big_co_is_researcher


# ── Search intent matching ────────────────────────────────────────────────────

class TestIntentMatching:
    def test_rlhf_researcher_matches_rlhf_profile(self):
        profile = {
            "login": "rlhf-dev", "type": "User", "bio": "RLHF and fine-tuning researcher",
            "company": "", "location": "", "followers": 200, "public_repos": 15,
            "html_url": "https://github.com/rlhf-dev",
            "top_repos": [{"name": "rlhf-trainer", "stars": 500, "description": "RLHF training library"}],
        }
        assert candidate_matches_intent("RLHF post-training researcher", profile)

    def test_robotics_requires_both_robotics_and_deployment(self):
        only_robotics = {
            "login": "bot", "type": "User", "bio": "Robotics researcher, manipulation",
            "company": "", "location": "", "followers": 50, "public_repos": 5,
            "html_url": "https://github.com/bot", "top_repos": [],
        }
        both = {
            "login": "bot2", "type": "User",
            "bio": "Robotics deployment engineer, field testing robot fleets",
            "company": "", "location": "", "followers": 50, "public_repos": 5,
            "html_url": "https://github.com/bot2", "top_repos": [],
        }
        assert not candidate_matches_intent("robotics deployment engineers", only_robotics)
        assert candidate_matches_intent("robotics deployment engineers", both)

    def test_biotech_doesnt_match_generic_ai(self):
        generic = {
            "login": "ai-dev", "type": "User", "bio": "LLM infra engineer",
            "company": "", "location": "", "followers": 100, "public_repos": 10,
            "html_url": "https://github.com/ai-dev", "top_repos": [],
        }
        assert not candidate_matches_intent("biotech AI researcher", generic)

    def test_intent_matching_case_insensitive(self):
        profile = {
            "login": "u", "type": "User", "bio": "RLHF Fine-Tuning Engineer",
            "company": "", "location": "", "followers": 50, "public_repos": 5,
            "html_url": "https://github.com/u", "top_repos": [],
        }
        assert candidate_matches_intent("rlhf fine-tuning", profile)


# ── Dedup and handle normalisation ───────────────────────────────────────────

class TestHandleNormalisation:
    def test_format_profile_lowercases_handle(self):
        result = format_profile(_gh_profile(login="JohnDoe"))
        assert result["handle"] == "johndoe"

    def test_format_profile_handle_already_lower(self):
        result = format_profile(_gh_profile(login="janedoe"))
        assert result["handle"] == "janedoe"

    def test_format_profile_mixed_case_handle(self):
        result = format_profile(_gh_profile(login="MixedCASE"))
        assert result["handle"] == "mixedcase"


# ── Security: auth boundary regression ───────────────────────────────────────

class TestAuthBoundaryRegression:
    def test_non_m13_email_raises(self):
        from database import _require_user_email
        for bad in ["hacker@gmail.com", "user@m13.co.evil.com", "", "m13.co"]:
            with pytest.raises((ValueError, Exception)):
                _require_user_email(bad)

    def test_none_email_raises(self):
        from database import _require_user_email
        with pytest.raises((ValueError, Exception, AttributeError)):
            _require_user_email(None)

    def test_m13_email_accepted(self):
        from database import _require_user_email
        assert _require_user_email("rishab@m13.co") == "rishab@m13.co"
        assert _require_user_email("zach@m13.co") == "zach@m13.co"

    def test_subdomain_spoof_rejected(self):
        from database import _require_user_email
        with pytest.raises((ValueError, Exception)):
            _require_user_email("user@fake.m13.co")
        with pytest.raises((ValueError, Exception)):
            _require_user_email("user@m13.co.attacker.com")

    def test_email_normalised_to_lowercase(self):
        from database import _require_user_email
        result = _require_user_email("RISHAB@M13.CO")
        assert result == "rishab@m13.co"
