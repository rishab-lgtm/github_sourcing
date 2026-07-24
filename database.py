"""
Supabase database module for M13 GitHub Sourcing.
Handles candidates, users, saved searches, run tracking, notification prefs, audit log.
"""

from __future__ import annotations

import os
import uuid
import hashlib
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
# Streamlit and the scheduler both execute on the server. The service credential
# stays in this module and is never emitted to HTML, JavaScript, or session state.
# Direct anon/authenticated table access is denied in supabase_schema.sql.
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))
ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "m13.co").lower()


def get_client() -> Client:
    """Server-only client. Never return this object to browser-facing code."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_service_client() -> Client:
    """Explicit alias used by trusted background processes."""
    return get_client()


def _require_user_email(user_email: str) -> str:
    """Validate the verified Google identity before any user-owned operation."""
    email = (user_email or "").strip().lower()
    if not email.endswith(f"@{ALLOWED_EMAIL_DOMAIN}"):
        raise ValueError(f"A verified @{ALLOWED_EMAIL_DOMAIN} email is required")
    return email


# ── Users ─────────────────────────────────────────────────────────────────────

def upsert_user(email: str, name: str = "") -> bool:
    """Create or update a user record on login."""
    email = _require_user_email(email)
    client = get_client()
    try:
        client.table("users").upsert(
            {"email": email, "name": name, "last_login_at": datetime.utcnow().isoformat()},
            on_conflict="email",
        ).execute()
        return True
    except Exception as e:
        log.warning("upsert_user failed: %s", e)
        return False


# ── Candidates ────────────────────────────────────────────────────────────────

def upsert_profiles(profiles: list) -> dict:
    """Insert new profiles, update existing ones. Returns {new, updated}."""
    if not profiles:
        return {"new": [], "updated": []}

    client = get_client()
    now = datetime.utcnow().isoformat()

    try:
        existing = client.table("candidates").select("handle, times_seen, first_seen").execute()
        existing_map = {r["handle"]: r for r in (existing.data or [])}
    except Exception as e:
        log.warning("Could not fetch existing candidates: %s", e)
        existing_map = {}

    new_profiles, updated_profiles = [], []

    for p in profiles:
        handle = (p.get("handle") or "").lower()
        if not handle:
            continue
        record = {
            "handle": handle,
            "name": p.get("name") or "",
            "location": p.get("location") or "",
            "bio": p.get("bio") or "",
            "company": p.get("company") or "",
            "followers": p.get("followers") or 0,
            "public_repos": p.get("public_repos") or 0,
            "top_repos": p.get("top_repos") or "",
            "contributes_to_ai": p.get("contributes_to_ai") or False,
            "signal_score": p.get("signal_score") or 0,
            "founder_badges": p.get("founder_badges") or "",
            "account_age_years": p.get("account_age_years"),
            "github_url": p.get("github_url") or "",
            "last_seen": now,
        }
        if handle in existing_map:
            record["times_seen"] = existing_map[handle]["times_seen"] + 1
            record["first_seen"] = existing_map[handle]["first_seen"]
            record["is_new"] = False
            updated_profiles.append(record)
        else:
            record["first_seen"] = now
            record["times_seen"] = 1
            record["is_new"] = True
            new_profiles.append(record)

    all_records = new_profiles + updated_profiles
    if all_records:
        try:
            client.table("candidates").upsert(all_records, on_conflict="handle").execute()
        except Exception as e:
            log.warning("upsert_profiles failed: %s", e)

    return {"new": new_profiles, "updated": updated_profiles}


def get_all_candidates(limit: int = 500) -> list:
    client = get_client()
    try:
        return client.table("candidates").select("*").order("signal_score", desc=True).limit(limit).execute().data or []
    except Exception as e:
        log.warning("get_all_candidates: %s", e)
        return []


def get_new_since(days: int = 7) -> list:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    client = get_client()
    try:
        return (
            client.table("candidates").select("*")
            .gte("first_seen", cutoff).order("signal_score", desc=True).execute().data or []
        )
    except Exception as e:
        log.warning("get_new_since: %s", e)
        return []


# ── Saved searches ────────────────────────────────────────────────────────────

def save_search(user_email: str, name: str, intent: str, mode: str,
                filters: dict = None, notify_on_new: bool = True) -> str | None:
    """Persist a new saved search. Return its ID only after a confirmed write."""
    user_email = _require_user_email(user_email)
    search_id = str(uuid.uuid4())
    client = get_client()
    try:
        client.table("saved_searches").insert({
            "search_id": search_id,
            "user_email": user_email,
            "name": name,
            "intent": intent,
            "mode": mode,
            "filters": filters or {},
            "notify_on_new": notify_on_new,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
        audit(user_email, "saved_search_created", {"search_id": search_id, "name": name, "intent": intent})
    except Exception as e:
        log.warning("save_search failed: %s", e)
        return None
    return search_id


def get_saved_searches(user_email: str) -> list:
    user_email = _require_user_email(user_email)
    client = get_client()
    try:
        return (
            client.table("saved_searches").select("*")
            .eq("user_email", user_email).order("created_at", desc=True).execute().data or []
        )
    except Exception as e:
        log.warning("get_saved_searches: %s", e)
        return []


def delete_saved_search(search_id: str, user_email: str):
    user_email = _require_user_email(user_email)
    client = get_client()
    try:
        client.table("saved_searches").delete().eq("search_id", search_id).eq("user_email", user_email).execute()
        audit(user_email, "saved_search_deleted", {"search_id": search_id})
    except Exception as e:
        log.warning("delete_saved_search: %s", e)


def update_saved_search_last_run(search_id: str, result_count: int):
    client = get_client()
    try:
        client.table("saved_searches").update({
            "last_run_at": datetime.utcnow().isoformat(),
            "last_result_count": result_count,
        }).eq("search_id", search_id).execute()
    except Exception as e:
        log.warning("update_saved_search_last_run: %s", e)


# ── Search runs ───────────────────────────────────────────────────────────────

def create_search_run(user_email: str, intent: str, mode: str,
                      filters: dict = None, saved_search_id: str = None,
                      triggered_by: str = "manual") -> str:
    user_email = _require_user_email(user_email)
    run_id = str(uuid.uuid4())
    client = get_client()
    try:
        client.table("search_runs").insert({
            "run_id": run_id,
            "user_email": user_email,
            "saved_search_id": saved_search_id,
            "intent": intent,
            "mode": mode,
            "filters": filters or {},
            "ran_at": datetime.utcnow().isoformat(),
            "result_count": 0,
            "triggered_by": triggered_by,
        }).execute()
    except Exception as e:
        log.warning("create_search_run failed: %s", e)
    return run_id


def update_search_run_count(run_id: str, count: int):
    client = get_client()
    try:
        client.table("search_runs").update({"result_count": count}).eq("run_id", run_id).execute()
    except Exception as e:
        log.warning("update_search_run_count: %s", e)


def record_matches(run_id: str, profiles: list, source_query: str = ""):
    if not profiles:
        return
    client = get_client()
    now = datetime.utcnow().isoformat()
    records = [
        {
            "run_id": run_id,
            "handle": p["handle"],
            "signal_score": p.get("signal_score") or 0,
            "match_reasons": p.get("match_reasons") or [],
            "first_matched_at": now,
            "last_matched_at": now,
            "source_query": source_query,
        }
        for p in profiles if p.get("handle")
    ]
    if records:
        try:
            client.table("candidate_matches").upsert(records, on_conflict="run_id,handle").execute()
        except Exception as e:
            log.warning("record_matches failed: %s", e)


def get_run_history(user_email: str, limit: int = 20) -> list:
    user_email = _require_user_email(user_email)
    client = get_client()
    try:
        return (
            client.table("search_runs").select("*")
            .eq("user_email", user_email).order("ran_at", desc=True).limit(limit).execute().data or []
        )
    except Exception as e:
        log.warning("get_run_history: %s", e)
        return []


def get_prior_handles_for_search(saved_search_id: str) -> set:
    """Return all handles ever matched by a saved search (for dedup/new-detection)."""
    client = get_client()
    try:
        runs = (
            client.table("search_runs").select("run_id")
            .eq("saved_search_id", saved_search_id).execute().data or []
        )
        if not runs:
            return set()
        run_ids = [r["run_id"] for r in runs]
        matches = (
            client.table("candidate_matches").select("handle")
            .in_("run_id", run_ids).execute().data or []
        )
        return {m["handle"] for m in matches}
    except Exception as e:
        log.warning("get_prior_handles_for_search: %s", e)
        return set()


def get_notified_handles_for_search(saved_search_id: str) -> set:
    """Return handles already included in a successful alert for this search."""
    client = get_client()
    try:
        runs = (
            client.table("search_runs").select("run_id")
            .eq("saved_search_id", saved_search_id).execute().data or []
        )
        if not runs:
            return set()
        run_ids = [r["run_id"] for r in runs]
        matches = (
            client.table("candidate_matches").select("handle,notified_at")
            .in_("run_id", run_ids).execute().data or []
        )
        return {m["handle"] for m in matches if m.get("notified_at")}
    except Exception as e:
        log.warning("get_notified_handles_for_search: %s", e)
        return set()


# ── Notification preferences ──────────────────────────────────────────────────

def get_notification_prefs(user_email: str) -> dict:
    user_email = _require_user_email(user_email)
    client = get_client()
    try:
        result = client.table("notification_preferences").select("*").eq("user_email", user_email).execute()
        rows = result.data or []
        if rows:
            return rows[0]
    except Exception as e:
        log.warning("get_notification_prefs: %s", e)
    # Defaults
    return {
        "notify_email": user_email,
        "recap_frequency": "Weekly",
        "notify_on_new_match": True,
        "digest_recipients": [],
    }


def save_notification_prefs(user_email: str, notify_email: str,
                             recap_frequency: str, notify_on_new_match: bool,
                             digest_recipients: list) -> bool:
    user_email = _require_user_email(user_email)
    notify_email = _require_user_email(notify_email)
    safe_recipients = [_require_user_email(email) for email in (digest_recipients or [])]
    client = get_client()
    try:
        client.table("notification_preferences").upsert({
            "user_email": user_email,
            "notify_email": notify_email,
            "recap_frequency": recap_frequency,
            "notify_on_new_match": notify_on_new_match,
            "digest_recipients": safe_recipients,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="user_email").execute()
        audit(user_email, "notification_prefs_updated", {"notify_email": notify_email})
        return True
    except Exception as e:
        log.warning("save_notification_prefs: %s", e)
        return False


# ── Audit log ─────────────────────────────────────────────────────────────────

def audit(user_email: str, action: str, detail: dict = None):
    client = get_client()
    try:
        client.table("audit_log").insert({
            "user_email": user_email,
            "action": action,
            "detail": detail or {},
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        log.debug("audit log failed (non-critical): %s", e)


# ── Per-user result cache (file-based, namespaced by email) ───────────────────

def _user_cache_path(user_email: str, kind: str) -> str:
    safe = hashlib.md5(user_email.encode()).hexdigest()[:12]
    os.makedirs("output", exist_ok=True)
    return f"output/{safe}_{kind}.json"


def load_user_results(user_email: str) -> list:
    path = _user_cache_path(user_email, "last_results")
    try:
        with open(path) as f:
            return json_load(f)
    except Exception:
        return []


def save_user_results(user_email: str, results: list):
    import json
    path = _user_cache_path(user_email, "last_results")
    with open(path, "w") as f:
        json.dump(results, f)


def load_user_recap(user_email: str) -> dict:
    path = _user_cache_path(user_email, "last_recap")
    try:
        with open(path) as f:
            return json_load(f)
    except Exception:
        return {}


def save_user_recap(user_email: str, data: dict):
    import json
    path = _user_cache_path(user_email, "last_recap")
    with open(path, "w") as f:
        json.dump(data, f)


def json_load(f):
    import json
    return json.load(f)


# ── Pipeline / profile actions ───────────────────────────────────────────────

def get_candidate_actions(user_email: str) -> dict:
    """Return {handle: {status, note, updated_at}} for all actions by this user."""
    user_email = _require_user_email(user_email)
    client = get_client()
    try:
        rows = client.table("candidate_actions").select("*").eq("user_email", user_email).execute().data or []
        return {r["handle"]: r for r in rows}
    except Exception as e:
        log.warning("get_candidate_actions: %s", e)
        return {}


def set_candidate_action(user_email: str, handle: str, status: str, note: str = "") -> bool:
    """Upsert a status/note for a profile. status: 'none'|'interested'|'contacted'|'passed'."""
    user_email = _require_user_email(user_email)
    handle = handle.lower().strip()
    client = get_client()
    try:
        client.table("candidate_actions").upsert({
            "user_email": user_email,
            "handle": handle,
            "status": status,
            "note": note,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="user_email,handle").execute()
        audit(user_email, "candidate_action", {"handle": handle, "status": status})
        return True
    except Exception as e:
        log.warning("set_candidate_action: %s", e)
        return False


def get_user_pipeline(user_email: str, statuses: list = None) -> list:
    """Return candidates with status in `statuses` (default: interested + contacted), enriched."""
    user_email = _require_user_email(user_email)
    statuses = statuses or ["interested", "contacted"]
    client = get_client()
    try:
        actions = (
            client.table("candidate_actions").select("*")
            .eq("user_email", user_email).in_("status", statuses)
            .order("updated_at", desc=True).execute().data or []
        )
        if not actions:
            return []
        handles = [a["handle"] for a in actions]
        profiles_raw = (
            client.table("candidates").select("*").in_("handle", handles).execute().data or []
        )
        profile_map = {p["handle"]: p for p in profiles_raw}
        result = []
        for action in actions:
            h = action["handle"]
            profile = profile_map.get(h, {"handle": h})
            profile["_status"] = action["status"]
            profile["_note"] = action.get("note", "")
            profile["_action_updated"] = action.get("updated_at", "")[:10]
            result.append(profile)
        return result
    except Exception as e:
        log.warning("get_user_pipeline: %s", e)
        return []


# ── Watched-person activity ───────────────────────────────────────────────────

def get_all_watched_actions() -> list:
    """Return all Interested/Contacted people for the trusted daily scheduler."""
    client = get_service_client()
    try:
        return (
            client.table("candidate_actions").select("*")
            .in_("status", ["interested", "contacted"]).execute().data or []
        )
    except Exception as e:
        log.warning("get_all_watched_actions: %s", e)
        return []


def get_latest_watch_snapshot(user_email: str, handle: str) -> dict:
    """Return the latest monitoring baseline for one user/person pair."""
    user_email = _require_user_email(user_email)
    handle = (handle or "").strip().lower()
    if not handle:
        return {}
    client = get_client()
    try:
        rows = (
            client.table("audit_log").select("detail,created_at")
            .eq("user_email", user_email)
            .eq("action", "person_watch_snapshot")
            .contains("detail", {"handle": handle})
            .order("created_at", desc=True).limit(1).execute().data or []
        )
        return rows[0].get("detail", {}) if rows else {}
    except Exception as e:
        log.warning("get_latest_watch_snapshot: %s", e)
        return {}


def record_watch_snapshot(user_email: str, snapshot: dict):
    user_email = _require_user_email(user_email)
    audit(user_email, "person_watch_snapshot", snapshot)


def record_watch_activity(user_email: str, detail: dict):
    user_email = _require_user_email(user_email)
    audit(user_email, "person_watch_activity", detail)


def get_watch_activity(user_email: str, limit: int = 100) -> list:
    """Return recent detected changes for people this user is monitoring."""
    user_email = _require_user_email(user_email)
    client = get_client()
    try:
        rows = (
            client.table("audit_log").select("detail,created_at")
            .eq("user_email", user_email)
            .eq("action", "person_watch_activity")
            .order("created_at", desc=True).limit(limit).execute().data or []
        )
        return [
            {**(row.get("detail") or {}), "detected_at": row.get("created_at", "")}
            for row in rows
        ]
    except Exception as e:
        log.warning("get_watch_activity: %s", e)
        return []


# ── Velocity / snapshot tracking ──────────────────────────────────────────────

def _top_repo_stars(top_repos_str: str) -> int:
    import re
    return sum(int(s) for s in re.findall(r'\((\d+)⭐\)', top_repos_str or ""))


def record_snapshots(profiles: list):
    """Write one snapshot row per profile. Call after each scheduler/scan run."""
    if not profiles:
        return
    client = get_client()
    now = datetime.utcnow().isoformat()
    rows = [
        {
            "handle": p["handle"],
            "recorded_at": now,
            "followers": p.get("followers") or 0,
            "public_repos": p.get("public_repos") or 0,
            "signal_score": p.get("signal_score") or 0,
            "top_repo_stars": _top_repo_stars(p.get("top_repos", "")),
        }
        for p in profiles if p.get("handle")
    ]
    try:
        client.table("candidate_snapshots").insert(rows).execute()
    except Exception as e:
        log.warning("record_snapshots failed: %s", e)


def get_snapshots_for_handle(handle: str, limit: int = 30) -> list:
    client = get_client()
    try:
        return (
            client.table("candidate_snapshots").select("*")
            .eq("handle", handle).order("recorded_at", desc=True).limit(limit).execute().data or []
        )
    except Exception as e:
        log.warning("get_snapshots_for_handle: %s", e)
        return []


def get_breakout_candidates(days: int = 30, min_multiplier: float = 3.0,
                             min_abs_growth: int = 200, limit: int = 50) -> list:
    """
    Return candidates whose followers or star count grew by >= min_multiplier
    in the last `days` days. Each row includes velocity metrics.
    """
    client = get_client()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        # Fetch all snapshots newer than cutoff
        recent = client.table("candidate_snapshots").select("*").gte("recorded_at", cutoff).execute().data or []
        # Fetch the snapshot just before cutoff for comparison
        old = client.table("candidate_snapshots").select("*").lt("recorded_at", cutoff).order("recorded_at", desc=True).execute().data or []
    except Exception as e:
        log.warning("get_breakout_candidates: %s", e)
        return []

    # Group: latest recent snapshot and oldest-before-cutoff per handle
    from collections import defaultdict
    recent_by_handle: dict = {}
    for row in recent:
        h = row["handle"]
        if h not in recent_by_handle or row["recorded_at"] > recent_by_handle[h]["recorded_at"]:
            recent_by_handle[h] = row

    old_by_handle: dict = {}
    for row in old:
        h = row["handle"]
        if h not in old_by_handle:  # already ordered desc, first = most recent before cutoff
            old_by_handle[h] = row

    breakouts = []
    for handle, now_snap in recent_by_handle.items():
        past_snap = old_by_handle.get(handle)
        if not past_snap:
            continue

        now_followers = now_snap.get("followers") or 0
        past_followers = past_snap.get("followers") or 1
        now_stars = now_snap.get("top_repo_stars") or 0
        past_stars = past_snap.get("top_repo_stars") or 1
        now_score = now_snap.get("signal_score") or 0
        past_score = past_snap.get("signal_score") or 0

        follower_mult = now_followers / max(past_followers, 1)
        star_mult = now_stars / max(past_stars, 1)
        follower_gain = now_followers - past_followers
        star_gain = now_stars - past_stars

        if (follower_mult >= min_multiplier and follower_gain >= min_abs_growth) or \
           (star_mult >= min_multiplier and star_gain >= min_abs_growth):
            breakouts.append({
                "handle": handle,
                "github_url": f"https://github.com/{handle}",
                "followers_then": past_followers,
                "followers_now": now_followers,
                "follower_mult": round(follower_mult, 1),
                "follower_gain": follower_gain,
                "stars_then": past_stars,
                "stars_now": now_stars,
                "star_mult": round(star_mult, 1),
                "star_gain": star_gain,
                "score_then": past_score,
                "score_now": now_score,
                "score_delta": now_score - past_score,
                "first_seen": past_snap.get("recorded_at", "")[:10],
                "last_seen": now_snap.get("recorded_at", "")[:10],
            })

    breakouts.sort(key=lambda x: max(x["follower_mult"], x["star_mult"]), reverse=True)
    return breakouts[:limit]
