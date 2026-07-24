"""Monitoring for Interested/Contacted people."""

from unittest.mock import MagicMock, patch

import notifications
from datetime import datetime, timedelta, timezone

from scheduler import (
    _watch_snapshot,
    _weekly_watch_due,
    detect_watched_changes,
    run_watched_people,
)


def _profile(**overrides):
    profile = {
        "handle": "robot-builder",
        "name": "Robot Builder",
        "bio": "Deploying autonomous robots",
        "company": "Robotics Lab",
        "followers": 100,
        "public_repos": 10,
        "github_url": "https://github.com/robot-builder",
    }
    profile.update(overrides)
    return profile


def _repos(pushed_at="2026-07-20T00:00:00Z", stars=100):
    return [{
        "name": "robot-stack",
        "stars": stars,
        "pushed_at": pushed_at,
        "url": "https://github.com/robot-builder/robot-stack",
    }]


def test_first_snapshot_is_only_a_baseline():
    current = _watch_snapshot(_profile(), _repos())
    assert detect_watched_changes({}, current) == []


def test_detects_code_push_growth_and_profile_changes():
    previous = _watch_snapshot(_profile(), _repos())
    current = _watch_snapshot(
        _profile(
            bio="Building general-purpose robot fleets",
            company="Stealth",
            followers=125,
            public_repos=11,
        ),
        _repos(pushed_at="2026-07-24T00:00:00Z", stars=125),
    )
    changes = detect_watched_changes(previous, current)
    assert any("Followers grew" in change for change in changes)
    assert any("Pushed new code" in change for change in changes)
    assert any("gained 25 stars" in change for change in changes)
    assert any("Company changed" in change for change in changes)
    assert any("Updated their GitHub bio" in change for change in changes)


def test_baseline_run_does_not_send_email():
    action = {
        "user_email": "owner@m13.co",
        "handle": "robot-builder",
        "status": "interested",
    }
    formatted = _profile()
    send = MagicMock(return_value=True)

    with patch("database.get_all_watched_actions", return_value=[action]), \
         patch("database.get_latest_watch_snapshot", return_value={}), \
         patch("database.record_watch_snapshot") as record_baseline, \
         patch("database.record_watch_activity"), \
         patch("database.get_notification_prefs"), \
         patch("database.upsert_profiles"), \
         patch("database.record_snapshots"), \
         patch("database.audit"), \
         patch("github_sourcing.get_user_profile", return_value={"login": "robot-builder"}), \
         patch("github_sourcing.get_user_repos", return_value=[]), \
         patch("github_sourcing.get_recent_user_repos", return_value=_repos()), \
         patch("github_sourcing.format_profile", return_value=formatted), \
         patch("github_sourcing.set_current_user"):
        summary = run_watched_people(send_fn=send)

    assert summary == {"watched": 1, "changed": 0, "notified": 0}
    record_baseline.assert_called_once()
    send.assert_not_called()


def test_failed_activity_email_is_left_for_retry():
    action = {
        "user_email": "owner@m13.co",
        "handle": "robot-builder",
        "status": "contacted",
    }
    previous = _watch_snapshot(_profile(), _repos())
    formatted = _profile(followers=125)
    send = MagicMock(return_value=False)

    with patch("database.get_all_watched_actions", return_value=[action]), \
         patch("database.get_latest_watch_snapshot", return_value=previous), \
         patch("database.record_watch_snapshot") as record_snapshot, \
         patch("database.record_watch_activity") as record_activity, \
         patch("database.get_notification_prefs", return_value={
             "notify_email": "owner@m13.co",
         }), \
         patch("database.upsert_profiles"), \
         patch("database.record_snapshots"), \
         patch("database.audit"), \
         patch("github_sourcing.get_user_profile", return_value={"login": "robot-builder"}), \
         patch("github_sourcing.get_user_repos", return_value=[]), \
         patch("github_sourcing.get_recent_user_repos", return_value=_repos()), \
         patch("github_sourcing.format_profile", return_value=formatted), \
         patch("github_sourcing.set_current_user"):
        summary = run_watched_people(send_fn=send)

    assert summary == {"watched": 1, "changed": 1, "notified": 0}
    record_activity.assert_called_once()
    record_snapshot.assert_not_called()


def test_weekly_monitor_waits_seven_days():
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    assert not _weekly_watch_due({
        "_recorded_at": (now - timedelta(days=6)).isoformat(),
    }, now=now)
    assert _weekly_watch_due({
        "_recorded_at": (now - timedelta(days=7)).isoformat(),
    }, now=now)


def test_monitoring_off_records_change_without_email():
    action = {
        "user_email": "owner@m13.co",
        "handle": "robot-builder",
        "status": "interested",
        "notify_frequency": "off",
    }
    previous = _watch_snapshot(_profile(), _repos())
    formatted = _profile(followers=125)
    send = MagicMock(return_value=True)

    with patch("database.get_all_watched_actions", return_value=[action]), \
         patch("database.get_latest_watch_snapshot", return_value=previous), \
         patch("database.record_watch_snapshot", return_value=True) as snapshot, \
         patch("database.record_watch_activity", return_value=True) as activity, \
         patch("database.upsert_profiles"), \
         patch("database.record_snapshots"), \
         patch("database.audit"), \
         patch("github_sourcing.get_user_profile", return_value={"login": "robot-builder"}), \
         patch("github_sourcing.get_user_repos", return_value=[]), \
         patch("github_sourcing.get_recent_user_repos", return_value=_repos()), \
         patch("github_sourcing.format_profile", return_value=formatted), \
         patch("github_sourcing.set_current_user"):
        summary = run_watched_people(send_fn=send)

    assert summary == {"watched": 1, "changed": 1, "notified": 0}
    activity.assert_called_once()
    snapshot.assert_called_once()
    send.assert_not_called()


def test_activity_email_escapes_profile_and_change_text():
    response = MagicMock(ok=True, status_code=200, text="ok")
    with patch("notifications.requests.post", return_value=response) as post, \
         patch.object(notifications, "RESEND_API_KEY", "test-key"):
        sent = notifications.send_watched_person_alert(
            {
                "handle": "robot-builder",
                "name": "<script>bad</script>",
                "github_url": "https://github.com/robot-builder",
            },
            ["Created <img src=x onerror=alert(1)>"],
            "owner@m13.co",
        )

    assert sent is True
    html = post.call_args.kwargs["json"]["html"]
    assert "<script>" not in html
    assert "<img " not in html
