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
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def run_saved_search(search: dict, send_fn=None, triggered_by: str = "scheduler") -> dict:
    """Run exactly one saved search; isolated for cron and integration testing."""
    from database import (
        get_service_client,
        upsert_profiles,
        create_search_run,
        update_search_run_count,
        record_matches,
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
    client = get_service_client()
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
    results = execute_search(SearchConfig.from_saved_search(search))
    if not results:
        update_search_run_count(run_id, 0)
        update_saved_search_last_run(search_id, 0)
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
    upsert_profiles(results)
    record_matches(run_id, results, source_query=intent)
    record_snapshots(results)
    update_search_run_count(run_id, len(results))
    update_saved_search_last_run(search_id, len(results))

    notified = False
    if new_profiles and search.get("notify_on_new", True):
        prefs = get_notification_prefs(user_email)
        notify_email = prefs.get("notify_email") or user_email
        if prefs.get("notify_on_new_match", True):
            notified = send_fn(new_profiles, to_email=notify_email)
            if notified:
                now = datetime.utcnow().isoformat()
                new_handles = [profile["handle"] for profile in new_profiles]
                client.table("candidate_matches").update({"notified_at": now}).eq(
                    "run_id", run_id
                ).in_("handle", new_handles).execute()
                audit(user_email, "notification_sent", {
                    "search_name": search["name"],
                    "new_count": len(new_profiles),
                    "notify_email": notify_email,
                    "triggered_by": triggered_by,
                })
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
        return

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
    run_all_saved_searches()
