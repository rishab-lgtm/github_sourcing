"""Compact candidate explanations for the expandable profile detail."""

from __future__ import annotations


def _clean_text(value) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none"} else text


def explain_candidate(profile: dict) -> dict[str, list[str]]:
    match_reasons = [
        str(reason) for reason in (profile.get("match_reasons") or []) if reason
    ]
    preference_reasons = [
        str(reason) for reason in (profile.get("preference_reasons") or []) if reason
    ]
    confidence = (profile.get("match_confidence") or "medium").lower()
    recent_days = profile.get("activity_recency_days")
    badges = (profile.get("founder_badges") or "").lower()

    why_match = match_reasons[:5] or [
        "GitHub evidence matched the search, but the evidence is limited"
    ]

    why_now = list(preference_reasons[:2])
    if isinstance(recent_days, int):
        if recent_days <= 30:
            why_now.append("Shipped public code within the last 30 days")
        elif recent_days <= 90:
            why_now.append("Active public repositories within the last 90 days")
        elif recent_days <= 365:
            why_now.append("Public GitHub activity within the last year")
    if "founder" in badges or "building" in badges:
        why_now.append("Current profile shows an active building or founder signal")
    if any("hidden gem" in reason.lower() or "quietly shipping" in reason.lower()
           for reason in match_reasons):
        why_now.append("Output is strong relative to their current audience")
    source_repo = _clean_text(profile.get("source_repo"))
    if source_repo:
        why_now.append(f"Surfaced through {source_repo}")
    if not why_now:
        why_now.append("Worth reviewing on the strength of the current GitHub evidence")

    watchouts = []
    if confidence == "exploratory":
        watchouts.append(
            "The match relies partly on a relevant source repository, not only their own profile"
        )
    elif confidence == "medium":
        watchouts.append("Some requested dimensions have stronger evidence than others")
    if not _clean_text(profile.get("bio")):
        watchouts.append("GitHub bio is empty, so role and intent are harder to verify")
    if recent_days is None:
        watchouts.append("Recent activity could not be confirmed from the available repositories")
    elif recent_days > 365:
        watchouts.append("Top public repositories have not been active in the last year")
    if int(profile.get("signal_score") or 0) < 40:
        watchouts.append("Signal score is still early; review the underlying work before outreach")
    watchouts.append("GitHub evidence does not confirm availability or interest in a new role")

    return {
        "why_match": list(dict.fromkeys(why_match))[:5],
        "why_now": list(dict.fromkeys(why_now))[:4],
        "watchouts": list(dict.fromkeys(watchouts))[:4],
    }
