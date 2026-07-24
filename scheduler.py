"""
M13 GitHub Sourcing — Scheduler
Runs all saved searches that have notify_on_new=true and sends notifications
when new candidates are found.

Run as a cron job:
    0 8 * * * cd /path/to/github_sourcing && python3 scheduler.py

Or manually:
    python3 scheduler.py
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def _write_confirmed(result) -> bool:
    """Mocks/legacy adapters may return None; only an explicit False is failure."""
    return result is not False


def _weekly_watch_due(previous: dict, now: datetime | None = None) -> bool:
    recorded_at = previous.get("_recorded_at")
    if not recorded_at:
        return True
    try:
        recorded = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    now = now or datetime.now(timezone.utc)
    return (now - recorded).days >= 7


@contextmanager
def scheduler_lock(path: str | None = None):
    """Prevent two scheduler processes on the same service from overlapping."""
    import fcntl

    lock_path = path or os.environ.get(
        "SCHEDULER_LOCK_PATH", "/tmp/m13-github-sourcing-scheduler.lock"
    )
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def _watch_snapshot(profile: dict, recent_repos: list) -> dict:
    """Create the compact state used to detect a person's future activity."""
    return {
        "handle": (profile.get("handle") or "").lower(),
        "name": profile.get("name") or "",
        "bio": profile.get("bio") or "",
        "company": profile.get("company") or "",
        "followers": profile.get("followers") or 0,
        "public_repos": profile.get("public_repos") or 0,
        "top_repo_stars": sum(repo.get("stars", 0) or 0 for repo in recent_repos),
        "recent_repos": [
            {
                "name": repo.get("name") or "",
                "stars": repo.get("stars") or 0,
                "pushed_at": repo.get("pushed_at") or "",
                "url": repo.get("url") or "",
            }
            for repo in recent_repos
        ],
    }


def detect_watched_changes(previous: dict, current: dict) -> list[str]:
    """Return meaningful, human-readable changes since the previous snapshot."""
    if not previous:
        return []

    changes: list[str] = []
    old_followers = previous.get("followers") or 0
    new_followers = current.get("followers") or 0
    follower_gain = new_followers - old_followers
    if follower_gain >= max(10, int(old_followers * 0.10)):
        changes.append(
            f"Followers grew by {follower_gain:,} to {new_followers:,}"
        )

    old_repos = {
        repo.get("name"): repo
        for repo in previous.get("recent_repos", [])
        if repo.get("name")
    }
    new_repos = {
        repo.get("name"): repo
        for repo in current.get("recent_repos", [])
        if repo.get("name")
    }
    added = [name for name in new_repos if name not in old_repos]
    if added:
        changes.append(f"Created or surfaced new repo: {', '.join(added[:3])}")

    pushed = [
        name for name, repo in new_repos.items()
        if name in old_repos
        and repo.get("pushed_at")
        and repo.get("pushed_at") != old_repos[name].get("pushed_at")
    ]
    if pushed:
        changes.append(f"Pushed new code to: {', '.join(pushed[:3])}")

    old_stars = previous.get("top_repo_stars") or 0
    new_stars = current.get("top_repo_stars") or 0
    star_gain = new_stars - old_stars
    if star_gain >= max(10, int(old_stars * 0.10)):
        changes.append(f"Recent repos gained {star_gain:,} stars")

    old_public_repos = previous.get("public_repos") or 0
    new_public_repos = current.get("public_repos") or 0
    if new_public_repos > old_public_repos and not added:
        changes.append(
            f"Public repository count increased from "
            f"{old_public_repos} to {new_public_repos}"
        )

    old_company = (previous.get("company") or "").strip()
    new_company = (current.get("company") or "").strip()
    if old_company and new_company and old_company != new_company:
        changes.append(f"Company changed from {old_company} to {new_company}")

    old_bio = (previous.get("bio") or "").strip()
    new_bio = (current.get("bio") or "").strip()
    if old_bio and new_bio and old_bio != new_bio:
        changes.append("Updated their GitHub bio")

    return changes


def run_watched_people(send_fn=None) -> dict:
    """Refresh Interested/Contacted people and notify owners about new activity."""
    from database import (
        audit,
        get_all_watched_actions,
        get_latest_watch_snapshot,
        get_notification_prefs,
        record_snapshots,
        record_watch_activity,
        record_watch_snapshot,
        upsert_profiles,
    )
    from github_sourcing import (
        format_profile,
        get_recent_user_repos,
        get_user_profile,
        get_user_repos,
        set_current_user,
    )
    from notifications import send_watched_person_alert

    send_fn = send_fn or send_watched_person_alert
    actions = get_all_watched_actions()
    if not actions:
        return {"watched": 0, "changed": 0, "notified": 0}

    profile_cache: dict[str, tuple[dict, dict]] = {}
    changed_count = 0
    notified_count = 0

    for action in actions:
        user_email = action.get("user_email", "")
        handle = (action.get("handle") or "").lower()
        if not user_email or not handle:
            continue

        if handle not in profile_cache:
            set_current_user(user_email)
            raw = get_user_profile(handle)
            if not raw:
                audit(user_email, "person_watch_failed", {
                    "handle": handle,
                    "reason": "GitHub profile unavailable",
                })
                continue
            raw["top_repos"] = get_user_repos(handle)
            recent_repos = get_recent_user_repos(handle)
            formatted = format_profile(raw)
            current = _watch_snapshot(formatted, recent_repos)
            profile_cache[handle] = (formatted, current)
            upsert_profiles([formatted])
            record_snapshots([formatted])

        formatted, current = profile_cache[handle]
        previous = get_latest_watch_snapshot(user_email, handle)
        changes = detect_watched_changes(previous, current)
        frequency = (action.get("notify_frequency") or "daily").lower()

        if not previous:
            if not _write_confirmed(record_watch_snapshot(user_email, current)):
                log.error("Could not persist monitoring baseline for @%s", handle)
            audit(user_email, "person_watch_started", {"handle": handle})
            continue

        if frequency == "weekly" and not _weekly_watch_due(previous):
            continue

        if not changes:
            record_watch_snapshot(user_email, current)
            continue

        changed_count += 1
        activity = {
            "handle": handle,
            "name": formatted.get("name") or handle,
            "github_url": formatted.get("github_url") or f"https://github.com/{handle}",
            "changes": changes,
            "notified": False,
            "frequency": frequency,
        }
        if frequency == "off":
            if _write_confirmed(record_watch_activity(user_email, activity)):
                record_watch_snapshot(user_email, current)
            continue

        if not _write_confirmed(record_watch_activity(user_email, activity)):
            log.error(
                "Skipping alert for @%s because activity could not be recorded",
                handle,
            )
            continue

        prefs = get_notification_prefs(user_email)
        notify_email = prefs.get("notify_email") or user_email
        delivered = send_fn(formatted, changes, to_email=notify_email)
        if delivered:
            notified_count += 1
            record_watch_snapshot(user_email, current)
            audit(user_email, "person_watch_notification_sent", {
                "handle": handle,
                "change_count": len(changes),
                "frequency": frequency,
            })
        else:
            audit(user_email, "person_watch_notification_failed", {
                "handle": handle,
                "change_count": len(changes),
            })

    return {
        "watched": len(actions),
        "changed": changed_count,
        "notified": notified_count,
    }


def run_saved_search(search: dict, send_fn=None, triggered_by: str = "scheduler") -> dict:
    """Run exactly one saved search; isolated for cron and integration testing."""
    from database import (
        upsert_profiles,
        create_search_run,
        update_search_run_count,
        record_matches,
        mark_matches_notified,
        record_snapshots,
        update_saved_search_last_run,
        get_notified_handles_for_search,
        get_notification_prefs,
        audit,
    )
    from search_service import SearchConfig, execute_search
    from github_sourcing import set_current_user
    from notifications import send_new_profiles_email

    send_fn = send_fn or send_new_profiles_email
    search_id = search["search_id"]
    user_email = search["user_email"]
    intent = search["intent"]
    mode = search["mode"]
    filters = search.get("filters") or {}

    log.info("Running saved search '%s' for %s (mode=%s)", search["name"], user_email, mode)
    set_current_user(user_email)
    run_id = create_search_run(
        user_email=user_email,
        intent=intent,
        mode=mode,
        filters=filters,
        saved_search_id=search_id,
        triggered_by=triggered_by,
    )
    if not run_id:
        raise RuntimeError("Could not create a durable record for this search run")
    results = execute_search(SearchConfig.from_saved_search(search))
    if not results:
        if not _write_confirmed(update_search_run_count(run_id, 0)):
            raise RuntimeError("Could not finalize the empty search run")
        if not _write_confirmed(update_saved_search_last_run(search_id, 0)):
            raise RuntimeError("Could not update the saved search status")
        audit(user_email, "saved_search_run", {
            "search_name": search["name"],
            "result_count": 0,
            "new_count": 0,
            "triggered_by": triggered_by,
        })
        return {
            "run_id": run_id,
            "result_count": 0,
            "new_count": 0,
            "notified": False,
        }

    notified_handles = get_notified_handles_for_search(search_id)
    new_profiles = [
        profile for profile in results
        if profile["handle"] not in notified_handles
    ]
    profile_write = upsert_profiles(results)
    if profile_write.get("persisted") is False:
        raise RuntimeError("Could not persist candidate profiles")
    if not _write_confirmed(record_matches(run_id, results, source_query=intent)):
        raise RuntimeError("Could not persist candidate matches")
    record_snapshots(results)
    if not _write_confirmed(update_search_run_count(run_id, len(results))):
        raise RuntimeError("Could not finalize the search run")
    if not _write_confirmed(update_saved_search_last_run(search_id, len(results))):
        raise RuntimeError("Could not update the saved search status")

    notified = False
    if new_profiles and search.get("notify_on_new", True):
        prefs = get_notification_prefs(user_email)
        notify_email = prefs.get("notify_email") or user_email
        if prefs.get("notify_on_new_match", True):
            notified = send_fn(new_profiles, to_email=notify_email)
            if notified:
                new_handles = [profile["handle"] for profile in new_profiles]
                audit(user_email, "notification_sent", {
                    "search_id": search_id,
                    "run_id": run_id,
                    "search_name": search["name"],
                    "handles": new_handles,
                    "new_count": len(new_profiles),
                    "notify_email": notify_email,
                    "triggered_by": triggered_by,
                })
                if not mark_matches_notified(run_id, new_handles):
                    log.warning(
                        "Email delivered for '%s', but match receipts need repair",
                        search["name"],
                    )
            else:
                audit(user_email, "notification_failed", {
                    "search_name": search["name"],
                    "new_count": len(new_profiles),
                    "notify_email": notify_email,
                    "triggered_by": triggered_by,
                })

    audit(user_email, "saved_search_run", {
        "search_name": search["name"],
        "result_count": len(results),
        "new_count": len(new_profiles),
        "notified": notified,
        "triggered_by": triggered_by,
    })

    return {
        "run_id": run_id,
        "result_count": len(results),
        "new_count": len(new_profiles),
        "notified": notified,
    }


def run_all_saved_searches():
    from database import (
        get_service_client,
        get_breakout_candidates,
        audit,
    )

    client = get_service_client()

    try:
        saved = client.table("saved_searches").select("*").eq("notify_on_new", True).execute()
        searches = saved.data or []
    except Exception as e:
        log.error("Could not fetch saved searches: %s", e)
        searches = []

    log.info("Found %d saved searches to run", len(searches))

    for s in searches:
        try:
            summary = run_saved_search(s)
            log.info(
                "'%s': %d total, %d new, notified=%s",
                s["name"], summary["result_count"], summary["new_count"], summary["notified"],
            )
        except Exception as e:
            log.error("Search failed for '%s': %s", s["name"], e)
            continue

    # ── People monitoring ────────────────────────────────────────────────────
    log.info("Checking Interested/Contacted people for new GitHub activity…")
    try:
        watch_summary = run_watched_people()
        log.info(
            "People monitoring: %d watched, %d changed, %d notified",
            watch_summary["watched"],
            watch_summary["changed"],
            watch_summary["notified"],
        )
    except Exception as e:
        log.error("People monitoring failed: %s", e)

    # ── Breakout alerts ───────────────────────────────────────────────────────
    log.info("Checking for breakout candidates (3x growth in 30 days)…")
    from notifications import send_breakout_alert
    breakouts = get_breakout_candidates(days=30, min_multiplier=3.0, min_abs_growth=200)
    if breakouts:
        log.info("%d breakout candidates found", len(breakouts))
        BREAKOUT_RECIPIENTS = ["rishab@m13.co", "brent@m13.co", "thomas@m13.co"]
        for email in BREAKOUT_RECIPIENTS:
            sent = send_breakout_alert(breakouts, to_email=email)
            if sent:
                log.info("Breakout alert sent to %s", email)
            else:
                log.warning("Failed to send breakout alert to %s", email)
        audit("scheduler", "breakout_alert_sent", {"count": len(breakouts)})
    else:
        log.info("No breakout candidates this run.")

    log.info("Scheduler run complete.")


if __name__ == "__main__":
    with scheduler_lock() as acquired:
        if acquired:
            run_all_saved_searches()
        else:
            log.warning("Another scheduler run is already active; exiting cleanly.")
