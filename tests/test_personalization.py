"""Per-user preference learning remains bounded, explainable, and isolated."""

from unittest.mock import patch

from personalization import (
    apply_preference_model,
    build_preference_model,
    personalize_for_user,
)


def _profile(handle, status, bio, archetype="builder", followers=100):
    return {
        "handle": handle,
        "_status": status,
        "bio": bio,
        "company": "",
        "top_repos": "robot-fleet(500⭐)",
        "founder_badges": "🚀 Building" if archetype == "builder" else "🔬 Researcher",
        "profile_archetype": archetype,
        "followers": followers,
        "signal_score": 50,
    }


def test_cold_start_preserves_the_original_score():
    model = build_preference_model([
        _profile("one", "interested", "robotics deployment"),
        _profile("two", "passed", "biotech researcher"),
    ])
    ranked = apply_preference_model([
        _profile("candidate", "", "robotics deployment"),
    ], model)

    assert model["active"] is False
    assert ranked[0]["signal_score"] == 50
    assert ranked[0]["personalized_score"] == 50


def test_feedback_reranks_without_overwriting_signal_score():
    feedback = [
        _profile("robot-one", "contacted", "robotics deployment and fleet systems"),
        _profile("robot-two", "interested", "robotics deployment engineer"),
        _profile("bio-one", "passed", "biotech genomics researcher", "researcher"),
        _profile("bio-two", "passed", "protein biology researcher", "researcher"),
    ]
    model = build_preference_model(feedback)
    candidates = [
        _profile("robot-new", "", "robotics deployment and field testing"),
        _profile("bio-new", "", "biotech protein researcher", "researcher"),
    ]
    ranked = apply_preference_model(candidates, model)

    assert model["active"] is True
    assert ranked[0]["handle"] == "robot-new"
    assert ranked[0]["signal_score"] == 50
    assert ranked[0]["personalized_score"] > ranked[0]["signal_score"]
    assert ranked[1]["personalized_score"] < ranked[1]["signal_score"]
    assert ranked[0]["preference_reasons"]


def test_each_user_loads_only_their_own_feedback():
    candidate = _profile("candidate", "", "robotics deployment")
    with patch("database.get_user_pipeline", return_value=[]) as get_feedback:
        personalize_for_user("owner@m13.co", [candidate])

    get_feedback.assert_called_once_with(
        "owner@m13.co",
        statuses=["interested", "contacted", "passed"],
    )
