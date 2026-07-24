"""Candidate detail gives useful evidence without overcrowding the card."""

from explanations import explain_candidate


def test_explanation_separates_match_timing_and_watchouts():
    explanation = explain_candidate({
        "handle": "robot-builder",
        "bio": "Deploying autonomous robot fleets",
        "signal_score": 55,
        "match_confidence": "exploratory",
        "match_reasons": [
            "Direct Robotics evidence: robotics, robot",
            "Source Deployment evidence: deployment",
        ],
        "preference_reasons": [
            "Your feedback favors builder profiles",
        ],
        "activity_recency_days": 20,
        "founder_badges": "🚀 Building",
        "source_repo": "robot-lab/fleet-tools",
    })

    assert any("Robotics" in reason for reason in explanation["why_match"])
    assert any("feedback favors" in reason for reason in explanation["why_now"])
    assert any("last 30 days" in reason for reason in explanation["why_now"])
    assert any("source repository" in reason for reason in explanation["watchouts"])
    assert any("availability" in reason for reason in explanation["watchouts"])


def test_sparse_profile_is_explicit_about_missing_evidence():
    explanation = explain_candidate({
        "handle": "quiet-profile",
        "signal_score": 20,
        "bio": "",
        "match_reasons": [],
    })

    assert explanation["why_match"]
    assert any("bio is empty" in item.lower() for item in explanation["watchouts"])
    assert any("recent activity" in item.lower() for item in explanation["watchouts"])
