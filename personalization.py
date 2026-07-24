"""Conservative, explainable per-user ranking from Pipeline feedback."""

from __future__ import annotations

import re
from collections import defaultdict


STATUS_WEIGHT = {
    "interested": 1.0,
    "contacted": 2.0,
    "passed": -1.5,
}

DOMAIN_FEATURES = {
    "robotics": ("robot", "robotics", "ros", "slam", "autonomous", "lidar", "manipulation"),
    "deployment": ("deployment", "deploy", "field testing", "fleet", "integration", "reliability"),
    "biotech": ("biotech", "genomics", "protein", "biology", "drug discovery", "crispr"),
    "infrastructure": ("infrastructure", "distributed systems", "kubernetes", "platform", "systems"),
    "ai safety": ("alignment", "interpretability", "ai safety", "rlhf", "reward model"),
    "climate": ("climate", "energy", "carbon", "grid", "battery"),
    "developer tools": ("developer tools", "sdk", "api", "cli", "devtools"),
}

DISPLAY_FEATURES = {
    "archetype:builder": "builder profiles",
    "archetype:researcher": "research profiles",
    "signal:founder": "founder signals",
    "signal:building": "people actively building",
    "signal:top_lab": "top-lab backgrounds",
    "signal:hidden_gem": "low-profile, high-output builders",
    "signal:high_output": "high-output open-source work",
    "signal:large_audience": "large-audience profiles",
    **{f"domain:{name}": f"{name} experience" for name in DOMAIN_FEATURES},
}


def _profile_text(profile: dict) -> str:
    return " ".join(str(profile.get(field) or "") for field in (
        "bio", "company", "top_repos", "founder_badges", "match_evidence"
    )).lower()


def _star_total(profile: dict) -> int:
    return sum(
        int(value)
        for value in re.findall(r"\((\d+)⭐\)", str(profile.get("top_repos") or ""))
    )


def profile_features(profile: dict) -> set[str]:
    """Return stable, human-explainable traits rather than arbitrary word tokens."""
    features: set[str] = set()
    text = _profile_text(profile)
    archetype = (profile.get("profile_archetype") or "").lower()
    if archetype in {"builder", "researcher"}:
        features.add(f"archetype:{archetype}")

    badges = (profile.get("founder_badges") or "").lower()
    if "founder" in badges:
        features.add("signal:founder")
    if "building" in badges:
        features.add("signal:building")
    if "top lab" in badges:
        features.add("signal:top_lab")

    followers = int(profile.get("followers") or 0)
    stars = _star_total(profile)
    if followers < 300 and stars >= 200:
        features.add("signal:hidden_gem")
    if stars >= 1000:
        features.add("signal:high_output")
    if followers >= 5000:
        features.add("signal:large_audience")

    for name, terms in DOMAIN_FEATURES.items():
        if any(term in text for term in terms):
            features.add(f"domain:{name}")
    return features


def build_preference_model(feedback_profiles: list[dict]) -> dict:
    """Build an ephemeral model scoped to one verified M13 user."""
    labeled = [
        profile for profile in feedback_profiles
        if (profile.get("_status") or profile.get("status")) in STATUS_WEIGHT
    ]
    positive_count = sum(
        1 for profile in labeled
        if (profile.get("_status") or profile.get("status")) in {"interested", "contacted"}
    )
    if len(labeled) < 3 or positive_count == 0:
        return {
            "active": False,
            "feedback_count": len(labeled),
            "weights": {},
        }

    raw_weights: dict[str, float] = defaultdict(float)
    occurrences: dict[str, int] = defaultdict(int)
    for profile in labeled:
        status = profile.get("_status") or profile.get("status")
        weight = STATUS_WEIGHT[status]
        for feature in profile_features(profile):
            raw_weights[feature] += weight
            occurrences[feature] += 1

    confidence = min(1.0, len(labeled) / 10)
    weights = {
        feature: round((raw_weights[feature] / occurrences[feature]) * confidence, 3)
        for feature in raw_weights
        if occurrences[feature] >= 2 and abs(raw_weights[feature]) >= 1
    }
    return {
        "active": bool(weights),
        "feedback_count": len(labeled),
        "weights": weights,
    }


def apply_preference_model(profiles: list[dict], model: dict) -> list[dict]:
    """Add a bounded preference adjustment while preserving the base signal score."""
    personalized = []
    for original in profiles:
        profile = dict(original)
        base_score = int(profile.get("signal_score") or 0)
        matched_weights = [
            (feature, model.get("weights", {}).get(feature, 0.0))
            for feature in profile_features(profile)
            if model.get("weights", {}).get(feature)
        ]
        raw_adjustment = sum(weight for _, weight in matched_weights) * 2
        adjustment = max(-8, min(8, round(raw_adjustment)))
        profile["base_signal_score"] = base_score
        profile["preference_adjustment"] = adjustment if model.get("active") else 0
        profile["personalized_score"] = max(
            0, min(100, base_score + profile["preference_adjustment"])
        )
        profile["personalization_active"] = bool(model.get("active"))
        profile["preference_feedback_count"] = model.get("feedback_count", 0)

        positive = sorted(
            ((feature, weight) for feature, weight in matched_weights if weight > 0),
            key=lambda item: item[1],
            reverse=True,
        )
        negative = sorted(
            ((feature, weight) for feature, weight in matched_weights if weight < 0),
            key=lambda item: item[1],
        )
        reasons = []
        if positive:
            reasons.append(
                f"Your feedback favors {DISPLAY_FEATURES.get(positive[0][0], positive[0][0])}"
            )
        if negative:
            reasons.append(
                f"Your feedback tends to pass {DISPLAY_FEATURES.get(negative[0][0], negative[0][0])}"
            )
        profile["preference_reasons"] = reasons
        personalized.append(profile)

    return sorted(
        personalized,
        key=lambda profile: (
            -(profile.get("personalized_score") or 0),
            -(profile.get("signal_score") or 0),
            profile.get("handle") or "",
        ),
    )


def personalize_for_user(user_email: str, profiles: list[dict]) -> list[dict]:
    """Load only this user's decisions, then apply their explainable preference model."""
    if not profiles:
        return []
    try:
        from database import get_user_pipeline

        feedback = get_user_pipeline(
            user_email,
            statuses=["interested", "contacted", "passed"],
        )
    except Exception:
        feedback = []
    return apply_preference_model(profiles, build_preference_model(feedback))
