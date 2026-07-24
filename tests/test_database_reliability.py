"""Database writes must report failure and monitoring data stays user-scoped."""

from unittest.mock import MagicMock, patch

import database


def test_create_search_run_returns_none_when_insert_fails():
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = RuntimeError(
        "database unavailable"
    )
    with patch("database.get_client", return_value=client):
        run_id = database.create_search_run(
            "owner@m13.co",
            "robotics deployment engineer",
            "Intent Search",
        )
    assert run_id is None


def test_monitoring_frequency_updates_only_the_owned_profile():
    client = MagicMock()
    table = client.table.return_value
    table.update.return_value = table
    table.eq.return_value = table
    table.execute.return_value.data = []

    with patch("database.get_client", return_value=client), \
         patch("database.audit") as audit:
        saved = database.set_candidate_monitoring(
            "owner@m13.co", "Robot-Builder", "weekly"
        )

    assert saved is True
    table.update.assert_called_once()
    payload = table.update.call_args.args[0]
    assert payload["notify_frequency"] == "weekly"
    assert table.eq.call_args_list[0].args == ("user_email", "owner@m13.co")
    assert table.eq.call_args_list[1].args == ("handle", "robot-builder")
    audit.assert_called_once()


def test_radar_feed_queries_the_current_user_and_formats_events():
    client = MagicMock()
    query = client.table.return_value
    for method in ("select", "eq", "in_", "order", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value.data = [{
        "action": "notification_sent",
        "detail": {
            "search_name": "Robotics",
            "new_count": 2,
            "handles": ["alice", "bob"],
        },
        "created_at": "2026-07-24T12:00:00Z",
    }]

    with patch("database.get_client", return_value=client):
        events = database.get_radar_events("owner@m13.co")

    query.eq.assert_called_once_with("user_email", "owner@m13.co")
    assert events[0]["title"] == "2 new matches"
    assert events[0]["summary"] == "Robotics"
    assert events[0]["handles"] == ["alice", "bob"]
