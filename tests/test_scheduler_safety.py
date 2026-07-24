"""Failure-path and overlap protection for scheduled sourcing."""

from unittest.mock import MagicMock, patch

import pytest

from scheduler import run_saved_search, scheduler_lock


SEARCH = {
    "search_id": "robotics-1",
    "user_email": "owner@m13.co",
    "name": "Robotics",
    "intent": "robotics deployment engineer",
    "mode": "Intent Search",
    "filters": {},
    "notify_on_new": True,
}


def test_email_is_not_sent_when_matches_cannot_be_recorded():
    send = MagicMock(return_value=True)
    candidate = {"handle": "robot-builder", "signal_score": 80}

    with patch("database.create_search_run", return_value="run-1"), \
         patch("database.get_notified_handles_for_search", return_value=set()), \
         patch("database.upsert_profiles", return_value={
             "new": [candidate], "updated": [], "persisted": True,
         }), \
         patch("database.record_matches", return_value=False), \
         patch("database.record_snapshots"), \
         patch("database.update_search_run_count"), \
         patch("database.update_saved_search_last_run"), \
         patch("search_service.execute_search", return_value=[candidate]):
        with pytest.raises(RuntimeError, match="candidate matches"):
            run_saved_search(SEARCH, send_fn=send)

    send.assert_not_called()


def test_scheduler_lock_rejects_an_overlapping_process(tmp_path):
    lock_path = str(tmp_path / "scheduler.lock")
    with scheduler_lock(lock_path) as first:
        with scheduler_lock(lock_path) as second:
            assert first is True
            assert second is False
