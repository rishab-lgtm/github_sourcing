"""Saved-search writes and notification delivery must fail honestly."""

from unittest.mock import MagicMock, patch

import database
import notifications


def test_failed_saved_search_insert_returns_none():
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = RuntimeError("down")
    with patch("database.get_client", return_value=client):
        saved_id = database.save_search(
            user_email="owner@m13.co",
            name="Robotics",
            intent="robotics deployment engineer",
            mode="Intent Search",
            notify_on_new=True,
        )
    assert saved_id is None


def test_successful_saved_search_insert_returns_id():
    client = MagicMock()
    with patch("database.get_client", return_value=client), \
         patch("database.audit"):
        saved_id = database.save_search(
            user_email="owner@m13.co",
            name="Robotics",
            intent="robotics deployment engineer",
            mode="Intent Search",
            notify_on_new=True,
        )
    assert saved_id


def test_test_notification_uses_configured_sender():
    response = MagicMock(ok=True, status_code=200, text="ok")
    with patch("notifications.requests.post", return_value=response) as post, \
         patch.object(notifications, "RESEND_API_KEY", "test-key"), \
         patch.object(
             notifications,
             "RESEND_FROM_EMAIL",
             "M13 GitHub Sourcing <sourcing@m13.co>",
         ):
        assert notifications.send_test_email("owner@m13.co") is True

    payload = post.call_args.kwargs["json"]
    assert payload["from"] == "M13 GitHub Sourcing <sourcing@m13.co>"
    assert payload["to"] == "owner@m13.co"
