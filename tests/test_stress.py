"""
Stress and edge-case tests for scoring, badge logic, and search pipeline.
Covers: big-company → Researcher badge, adversarial bios, null/empty inputs,
boundary scores, mixed signals, and scoring monotonicity.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_sourcing import compute_signal_score, founder_signal, expand_query, format_profile


# ── helpers ────────────────────────────────────────────────────────────────────

def _profile(**kwargs):
    base = {
        "handle": "testuser",
        "followers": 100,
        "public_repos": 10,
        "top_repos": "",
        "bio": "",
        "contributes_to_ai": False,
        "location": "",
        "account_age_years": 3,
    }
    base.update(kwargs)
    return base


def _fmt(**kwargs):
    """Return a GitHub-style profile dict for format_profile."""
    base = {
        "login": "testuser",
        "type": "User",
        "name": "Test User",
        "bio": "",
        "company": "",
        "location": "",
        "followers": 100,
        "public_repos": 10,
        "html_url": "https://github.com/testuser",
        "top_repos": [],
    }
    base.update(kwargs)
    return base


# ── Badge logic: big companies → Researcher ────────────────────────────────────

class TestBigCompanyResearcher:
    """People at large companies who aren't explicitly founding should show Researcher."""

    @pytest.mark.parametrize("company", [
        "Google", "google", "GOOGLE",
        "DeepMind", "deepmind",
        "Toyota", "toyota",
        "Meta", "META",
        "Microsoft", "microsoft",
        "Amazon", "amazon",
        "Apple",
        "NVIDIA", "nvidia",
        "OpenAI", "openai",
        "Anthropic",
        "Waymo",
        "Tesla",
        "Baidu",
        "ByteDance",
        "IBM",
        "Intel",
        "Salesforce",
        "Two Sigma", "two sigma",
        "Jane Street", "jane street",
        "Citadel", "citadel",
    ])
    def test_big_company_employee_gets_researcher_badge(self, company):
        result = founder_signal(bio="Software engineer", company=company)
        assert "🔬 Researcher" in result["badges"], (
            f"Expected Researcher badge for company='{company}', got {result['badges']}"
        )

    @pytest.mark.parametrize("bio", [
        "Research engineer at Google",
        "Working at DeepMind on language models",
        "Staff engineer at Meta AI",
        "ML engineer at Anthropic",
        "Scientist at Toyota Research Institute",
        "Engineer at OpenAI",
        "I work at nvidia on GPU software",
    ])
    def test_big_company_in_bio_gets_researcher_badge(self, bio):
        result = founder_signal(bio=bio)
        assert "🔬 Researcher" in result["badges"], (
            f"Expected Researcher badge for bio='{bio}', got {result['badges']}"
        )

    def test_big_company_does_not_get_building_badge(self):
        """'building' at Google should NOT produce a Building badge."""
        result = founder_signal(bio="Building search infrastructure at Google")
        assert "🚀 Building" not in result["badges"]

    def test_big_company_does_not_get_building_badge_toyota(self):
        result = founder_signal(bio="building autonomy systems", company="Toyota")
        assert "🚀 Building" not in result["badges"]

    def test_strong_founder_overrides_big_company(self):
        """Someone who left Google to found a startup should get Founder, not just Researcher."""
        result = founder_signal(bio="ex-google, founder of stealth startup")
        assert "🚀 Founder" in result["badges"]

    def test_left_google_gets_founder_and_not_only_researcher(self):
        result = founder_signal(bio="Left Google last year, now building in stealth")
        assert "🚀 Founder" in result["badges"]

    def test_independent_researcher_not_at_big_company(self):
        """Independent researcher without big-company affiliation still gets Researcher."""
        result = founder_signal(bio="independent researcher, PhD in NLP")
        assert "🔬 Researcher" in result["badges"]

    def test_small_startup_employee_does_not_get_researcher_by_default(self):
        """Random startup employee bio shouldn't auto-get Researcher."""
        result = founder_signal(bio="Backend engineer at a small fintech startup", company="SmallFintechCo")
        # No researcher keyword, not a big company — no Researcher badge
        assert "🔬 Researcher" not in result["badges"]


# ── Adversarial / edge-case bios ───────────────────────────────────────────────

class TestAdversarialBios:
    def test_empty_bio_and_company(self):
        result = founder_signal(bio="", company="")
        assert isinstance(result["badges"], list)
        assert isinstance(result["boost"], int)

    def test_none_bio_none_company(self):
        result = founder_signal(bio=None, company=None)
        assert isinstance(result["badges"], list)

    def test_very_long_bio_does_not_crash(self):
        long_bio = "google " * 5000
        result = founder_signal(bio=long_bio)
        assert "🔬 Researcher" in result["badges"]

    def test_unicode_bio_does_not_crash(self):
        result = founder_signal(bio="研究员 at 百度 Baidu AI Lab 🤖")
        assert isinstance(result["badges"], list)

    def test_injection_attempt_in_bio(self):
        """Sneaky bios shouldn't cause a crash or unexpected badge."""
        result = founder_signal(bio="'; DROP TABLE users; -- google")
        # Still detects google in text — should get Researcher
        assert "🔬 Researcher" in result["badges"]

    def test_repeated_keywords_do_not_inflate_boost(self):
        """Keyword appearing 100x should not multiply the boost."""
        result_once = founder_signal(bio="founder at startup", company="")
        result_many = founder_signal(bio="founder " * 100, company="")
        assert result_once["boost"] == result_many["boost"]

    def test_case_insensitive_matching(self):
        for variant in ["GOOGLE", "Google", "google", "gOoGlE"]:
            result = founder_signal(bio=f"Engineer at {variant}")
            assert "🔬 Researcher" in result["badges"], f"Failed for '{variant}'"


# ── Scoring monotonicity ───────────────────────────────────────────────────────

class TestScoringMonotonicity:
    def test_more_stars_scores_higher(self):
        s1 = compute_signal_score(_profile(top_repos="proj (50⭐)"))[0]
        s2 = compute_signal_score(_profile(top_repos="proj (300⭐)"))[0]
        s3 = compute_signal_score(_profile(top_repos="proj (2000⭐)"))[0]
        s4 = compute_signal_score(_profile(top_repos="proj (8000⭐)"))[0]
        assert s1 <= s2 <= s3 <= s4

    def test_founder_bio_scores_higher_than_generic(self):
        generic = compute_signal_score(_profile(bio="Software engineer"))[0]
        founder = compute_signal_score(_profile(bio="Founder of stealth AI startup, ex-OpenAI"))[0]
        assert founder > generic

    def test_ex_top_lab_scores_higher_than_still_at_lab(self):
        at_lab = compute_signal_score(_profile(bio="Research scientist at Google DeepMind"))[0]
        ex_lab = compute_signal_score(_profile(bio="ex-DeepMind, now independent researcher"))[0]
        # ex-lab should get Ex-Top Lab boost (20) vs still at lab (smaller boost)
        assert ex_lab >= at_lab

    def test_ai_contributions_always_add_score(self):
        base = compute_signal_score(_profile(bio="", contributes_to_ai=False))[0]
        with_ai = compute_signal_score(_profile(bio="", contributes_to_ai=True))[0]
        assert with_ai > base

    def test_hidden_gem_pattern_scores_well(self):
        """Low followers, high stars = quietly shipping, should score above average."""
        gem = compute_signal_score(_profile(followers=30, top_repos="secret-tool (1500⭐)"))[0]
        assert gem >= 20

    def test_score_never_negative(self):
        profiles = [
            _profile(),
            _profile(followers=0, public_repos=0, bio="", top_repos=""),
            _profile(followers=1, bio="random person"),
            _profile(bio=None),
        ]
        for p in profiles:
            score, _ = compute_signal_score(p)
            assert score >= 0, f"Negative score for profile: {p}"


# ── Scoring boundary conditions ────────────────────────────────────────────────

class TestScoringBoundaries:
    def test_extremely_high_followers_do_not_dominate(self):
        """Linus Torvalds-level followers should help but not push score above 100 alone."""
        score, _ = compute_signal_score(_profile(followers=200_000, bio=""))
        # followers alone are a weak signal — cap sanity check
        assert score < 50

    def test_strong_founder_signal_dominates_score(self):
        """A genuine founder signal should push score significantly higher."""
        score, _ = compute_signal_score(
            _profile(bio="YC founder, ex-OpenAI, stealth AI startup, seed round raised")
        )
        assert score >= 50

    def test_researcher_at_top_lab_scores_positively(self):
        score, _ = compute_signal_score(_profile(bio="Research scientist at DeepMind"))
        assert score >= 10

    def test_completely_empty_profile_scores_below_20(self):
        score, _ = compute_signal_score(_profile(followers=0, public_repos=0, bio="", top_repos=""))
        assert score < 20

    def test_all_signals_combined_score_high(self):
        score, _ = compute_signal_score(
            _profile(
                bio="ex-OpenAI founder building stealth robotics startup",
                followers=3000,
                top_repos="rl-robot (6000⭐), motion-ctrl (1200⭐)",
                contributes_to_ai=True,
                location="San Francisco, CA",
            ),
            is_sf=True,
            search_terms=["robotics", "reinforcement learning"],
        )
        assert score >= 60


# ── Reason quality ─────────────────────────────────────────────────────────────

class TestReasonQuality:
    def test_founder_reason_mentions_signal(self):
        _, reasons = compute_signal_score(_profile(bio="Founder at stealth startup"))
        combined = " ".join(reasons).lower()
        assert "founder" in combined or "startup" in combined or "building" in combined

    def test_ai_contributions_reason_present(self):
        _, reasons = compute_signal_score(_profile(contributes_to_ai=True))
        combined = " ".join(reasons).lower()
        assert "ai" in combined or "ml" in combined or "contributor" in combined

    def test_no_duplicate_reasons(self):
        _, reasons = compute_signal_score(
            _profile(bio="founder stealth startup", contributes_to_ai=True, followers=5000)
        )
        assert len(reasons) == len(set(reasons)), f"Duplicate reasons: {reasons}"

    def test_star_count_reason_present_for_high_star_repos(self):
        _, reasons = compute_signal_score(_profile(top_repos="awesome-ml (7000⭐)"))
        combined = " ".join(reasons).lower()
        assert "star" in combined or "⭐" in combined or "repo" in combined

    def test_reasons_all_strings(self):
        for bio in ["", "founder", "google engineer", "PhD researcher at Meta AI"]:
            _, reasons = compute_signal_score(_profile(bio=bio))
            assert all(isinstance(r, str) for r in reasons), f"Non-string reason for bio='{bio}'"


# ── Query expansion sanity ─────────────────────────────────────────────────────

class TestQueryExpansion:
    def test_expand_query_returns_list(self):
        terms = expand_query("RLHF researcher")
        assert isinstance(terms, list)
        assert len(terms) >= 3

    def test_expand_query_no_crash_on_empty(self):
        terms = expand_query("")
        assert isinstance(terms, list)

    def test_expand_query_robotics(self):
        terms = expand_query("robotics deployment engineer")
        combined = " ".join(terms).lower()
        assert "robot" in combined or "ros" in combined or "autonomous" in combined

    def test_expand_query_biotech(self):
        terms = expand_query("biotech AI researcher")
        combined = " ".join(terms).lower()
        assert any(kw in combined for kw in ["bio", "protein", "genomics", "drug", "molecule"])

    def test_expand_query_llm_finetuning(self):
        terms = expand_query("LLM fine-tuning researcher")
        combined = " ".join(terms).lower()
        assert any(kw in combined for kw in ["fine", "rlhf", "llm", "language model", "finetun"])


# ── Mixed-signal profiles (realistic VC sourcing scenarios) ───────────────────

class TestRealisticVCSourcingProfiles:
    def test_peter_norvig_type(self):
        """Senior researcher at Google — should get Researcher, no Building."""
        result = founder_signal(
            bio="Director of Research at Google. Author of AIMA. Previously NASA Ames.",
            company="Google"
        )
        assert "🔬 Researcher" in result["badges"]
        assert "🚀 Building" not in result["badges"]

    def test_yc_founder_post_openai(self):
        """Ex-OpenAI who's now a YC founder — should get Founder badge."""
        result = founder_signal(
            bio="ex-OpenAI. Founder @ YC W24. Building AI agents for enterprise.",
            company=""
        )
        assert "🚀 Founder" in result["badges"]

    def test_independent_ml_researcher(self):
        """PhD grad, no affiliation, doing research — should get Researcher."""
        result = founder_signal(bio="Independent ML researcher. PhD MIT. Working on transformers.")
        assert "🔬 Researcher" in result["badges"]

    def test_stealth_mode_former_deepmind(self):
        """Stealth mode, came from DeepMind — strong signal."""
        result = founder_signal(bio="building in stealth. previously deepmind.")
        assert "🚀 Founder" in result["badges"]

    def test_generic_swe_no_special_signal(self):
        """Ordinary engineer at a random company — no special badge."""
        result = founder_signal(bio="Software engineer. I like coffee and code.", company="AcmeCorp")
        assert "🚀 Founder" not in result["badges"]
        assert "🚀 Building" not in result["badges"]
        assert "🔬 Researcher" not in result["badges"]

    def test_toyota_research_institute_engineer(self):
        """TRI engineer explicitly — Researcher badge."""
        result = founder_signal(bio="Robotics engineer at Toyota Research Institute", company="Toyota")
        assert "🔬 Researcher" in result["badges"]
        assert "🚀 Building" not in result["badges"]
