"""Integration-style scheduler test across search, dedupe, persistence, and notify."""

from unittest.mock import MagicMock, patch

from scheduler import run_saved_search


def _candidate(handle):
    return {"handle": handle, "signal_score": 50, "match_reasons": ["Matches: biotech"]}


def test_second_run_notifies_only_new_candidate():
    search = {
        "search_id": "search-1",
        "user_email": "owner@m13.co",
        "name": "Biotech",
        "intent": "biotech AI researcher",
        "mode": "Intent Search",
        "filters": {},
    }
    first = [_candidate("alice"), _candidate("bob")]
    second = [*first, _candidate("carol")]
    send = MagicMock(return_value=True)
    client = MagicMock()

    with patch("database.get_service_client", return_value=client), \
         patch("database.get_prior_handles_for_search", side_effect=[set(), {"alice", "bob"}]), \
         patch("database.get_notification_prefs", return_value={
             "notify_email": "owner@m13.co", "notify_on_new_match": True,
         }), \
         patch("database.create_search_run", side_effect=["run-1", "run-2"]), \
         patch("database.upsert_profiles"), \
         patch("database.record_matches"), \
         patch("database.record_snapshots"), \
         patch("database.update_search_run_count"), \
         patch("database.update_saved_search_last_run"), \
         patch("database.audit"), \
         patch("search_service.execute_search", side_effect=[first, second]):
        run1 = run_saved_search(search, send_fn=send)
        run2 = run_saved_search(search, send_fn=send)

    assert run1["new_count"] == 2
    assert run2["new_count"] == 1
    assert [len(call.args[0]) for call in send.call_args_list] == [2, 1]
    assert send.call_args_list[1].args[0][0]["handle"] == "carol"
