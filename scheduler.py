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


def run_all_saved_searches():
    from database import (
        get_service_client,
        upsert_profiles,
        create_search_run,
        update_search_run_count,
        record_matches,
        record_snapshots,
        update_saved_search_last_run,
        get_prior_handles_for_search,
        get_notification_prefs,
        get_breakout_candidates,
        audit,
    )
    from github_sourcing import search_by_intent, find_sf_ai_contributors, find_trending_repo_authors
    from notifications import send_new_profiles_email

    client = get_service_client()

    try:
        saved = client.table("saved_searches").select("*").eq("notify_on_new", True).execute()
        searches = saved.data or []
    except Exception as e:
        log.error("Could not fetch saved searches: %s", e)
        return

    log.info("Found %d saved searches to run", len(searches))

    for s in searches:
        search_id = s["search_id"]
        user_email = s["user_email"]
        intent = s["intent"]
        mode = s["mode"]
        filters = s.get("filters") or {}

        log.info("Running saved search '%s' for %s (mode=%s)", s["name"], user_email, mode)

        try:
            if mode == "Intent Search":
                results = search_by_intent(
                    intent=intent,
                    location=filters.get("region", ""),
                    max_results=60,
                )
            elif mode == "SF-Based AI Contributors":
                results = find_sf_ai_contributors(limit_per_repo=30)
            else:
                results = find_trending_repo_authors(days=7)
        except Exception as e:
            log.error("Search failed for '%s': %s", s["name"], e)
            continue

        if not results:
            log.info("No results for '%s'", s["name"])
            continue

        # Find candidates new to this saved search
        prior_handles = get_prior_handles_for_search(search_id)
        new_profiles = [p for p in results if p["handle"] not in prior_handles]

        log.info("'%s': %d total, %d new", s["name"], len(results), len(new_profiles))

        # Persist
        run_id = create_search_run(
            user_email=user_email,
            intent=intent,
            mode=mode,
            filters=filters,
            saved_search_id=search_id,
            triggered_by="scheduler",
        )
        upsert_profiles(results)
        record_matches(run_id, results, source_query=intent)
        record_snapshots(results)
        update_search_run_count(run_id, len(results))
        update_saved_search_last_run(search_id, len(results))

        # Notify if new candidates found
        if new_profiles:
            prefs = get_notification_prefs(user_email)
            notify_email = prefs.get("notify_email") or user_email
            if prefs.get("notify_on_new_match", True):
                log.info("Sending notification to %s (%d new)", notify_email, len(new_profiles))
                sent = send_new_profiles_email(new_profiles, to_email=notify_email)
                if sent:
                    # Mark notified_at on the matches
                    try:
                        now = datetime.utcnow().isoformat()
                        new_handles = [p["handle"] for p in new_profiles]
                        client.table("candidate_matches").update({"notified_at": now}).eq("run_id", run_id).in_("handle", new_handles).execute()
                    except Exception as e:
                        log.warning("Could not set notified_at: %s", e)
                    audit(user_email, "notification_sent", {
                        "search_name": s["name"],
                        "new_count": len(new_profiles),
                        "notify_email": notify_email,
                        "triggered_by": "scheduler",
                    })
                else:
                    log.warning("Notification failed for %s", user_email)
        else:
            log.info("No new candidates for '%s' — skipping notification", s["name"])

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
