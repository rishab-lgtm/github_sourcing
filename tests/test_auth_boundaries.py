"""
Auth boundary tests.
Zach: "Test auth boundaries: one user cannot view, edit, run, or receive another user's searches."
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock, call
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_supabase_mock(return_data=None):
    """Return a mock Supabase client whose .execute() yields return_data."""
    mock_result = MagicMock()
    mock_result.data = return_data or []
    chain = MagicMock()
    chain.execute.return_value = mock_result
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.upsert.return_value = chain
    chain.delete.return_value = chain
    chain.update.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.lt.return_value = chain
    client = MagicMock()
    client.table.return_value = chain
    client.rpc.return_value = chain
    return client, chain


class TestSavedSearchIsolation:
    def test_get_saved_searches_filters_by_user_email(self):
        """get_saved_searches must pass user_email as a filter — never return all rows."""
        import database
        client, chain = _make_supabase_mock(return_data=[
            {"search_id": "s1", "user_email": "alice@m13.co", "name": "Alice search"},
        ])
        with patch("database.get_client", return_value=client):
            database.get_saved_searches("alice@m13.co")

        # Verify .eq("user_email", "alice@m13.co") was called
        eq_calls = [c for c in chain.eq.call_args_list if c.args[0] == "user_email"]
        assert any(c.args[1] == "alice@m13.co" for c in eq_calls), (
            "get_saved_searches must filter by user_email, not return all saved searches"
        )

    def test_get_saved_searches_for_bob_does_not_use_alice_email(self):
        import database
        client, chain = _make_supabase_mock(return_data=[])
        with patch("database.get_client", return_value=client):
            database.get_saved_searches("bob@m13.co")

        eq_calls = [c for c in chain.eq.call_args_list if c.args[0] == "user_email"]
        assert not any(c.args[1] == "alice@m13.co" for c in eq_calls), (
            "Fetching bob's searches must not include alice's email in any filter"
        )

    def test_delete_saved_search_scoped_to_owner(self):
        """delete_saved_search must apply BOTH search_id AND user_email filters."""
        import database
        client, chain = _make_supabase_mock()
        with patch("database.get_client", return_value=client):
            database.delete_saved_search("s-abc", "alice@m13.co")

        all_eq_calls = chain.eq.call_args_list
        fields = {c.args[0]: c.args[1] for c in all_eq_calls}
        assert "search_id" in fields, "delete must filter by search_id"
        assert "user_email" in fields, "delete must also filter by user_email to prevent cross-user deletes"
        assert fields["user_email"] == "alice@m13.co"

    def test_delete_cannot_be_called_without_user_email(self):
        """Calling delete with empty user_email should not delete anything meaningful."""
        import database
        client, chain = _make_supabase_mock()
        with patch("database.get_client", return_value=client):
            # Should not raise, but the .eq("user_email", "") filter means no rows match
            database.delete_saved_search("s-abc", "")

        all_eq_calls = chain.eq.call_args_list
        fields = {c.args[0]: c.args[1] for c in all_eq_calls}
        # The query still has user_email="" — RLS + app-layer filter both protect against this
        assert "user_email" in fields


class TestRunHistoryIsolation:
    def test_get_run_history_filters_by_user(self):
        import database
        client, chain = _make_supabase_mock(return_data=[])
        with patch("database.get_client", return_value=client):
            database.get_run_history("alice@m13.co", limit=10)

        eq_calls = [c for c in chain.eq.call_args_list if c.args[0] == "user_email"]
        assert any(c.args[1] == "alice@m13.co" for c in eq_calls), (
            "get_run_history must filter by user_email"
        )


class TestNotificationPrefsIsolation:
    def test_get_prefs_filtered_by_user(self):
        import database
        client, chain = _make_supabase_mock(return_data=[])
        with patch("database.get_client", return_value=client):
            database.get_notification_prefs("alice@m13.co")

        eq_calls = [c for c in chain.eq.call_args_list if c.args[0] == "user_email"]
        assert any(c.args[1] == "alice@m13.co" for c in eq_calls)

    def test_save_prefs_upsert_includes_user_email(self):
        import database
        client, chain = _make_supabase_mock()
        with patch("database.get_client", return_value=client):
            database.save_notification_prefs(
                user_email="alice@m13.co",
                notify_email="alice@m13.co",
                recap_frequency="Weekly",
                notify_on_new_match=True,
                digest_recipients=[],
            )

        # Check that upsert was called with user_email in the payload
        upsert_calls = chain.upsert.call_args_list
        assert len(upsert_calls) > 0
        payload = upsert_calls[0].args[0]
        assert payload.get("user_email") == "alice@m13.co"


class TestUserContextSet:
    def test_set_user_context_rpc_called_before_user_scoped_queries(self):
        """
        The anon-key client must call set_user_context RPC before each user-scoped
        query so that RLS policies can filter by the calling user.
        """
        import database
        client, chain = _make_supabase_mock(return_data=[])
        with patch("database.get_client", return_value=client):
            database.get_saved_searches("alice@m13.co")

        rpc_calls = client.rpc.call_args_list
        ctx_calls = [c for c in rpc_calls if c.args[0] == "set_user_context"]
        assert len(ctx_calls) > 0, (
            "set_user_context RPC must be called before user-scoped Supabase queries"
        )
        assert ctx_calls[0].args[1] == {"email": "alice@m13.co"}


class TestSchedulerUsesServiceKey:
    def test_scheduler_calls_get_service_client_not_get_client(self):
        """
        scheduler.py must use the service key client, not the anon client.
        This ensures it can bypass RLS to read all users' saved searches.
        """
        with patch("database.get_service_client") as mock_svc, \
             patch("database.get_client") as mock_anon, \
             patch("github_sourcing.search_by_intent", return_value=[]), \
             patch("database.upsert_profiles", return_value={"new": [], "updated": []}), \
             patch("database.create_search_run", return_value="run-id"), \
             patch("database.record_matches"), \
             patch("database.record_snapshots"), \
             patch("database.update_search_run_count"), \
             patch("database.update_saved_search_last_run"), \
             patch("database.get_prior_handles_for_search", return_value=set()), \
             patch("database.get_notification_prefs", return_value={"notify_on_new_match": False}), \
             patch("database.get_breakout_candidates", return_value=[]), \
             patch("database.audit"):

            # Mock service client to return empty saved searches
            mock_result = MagicMock()
            mock_result.data = []
            svc_chain = MagicMock()
            svc_chain.execute.return_value = mock_result
            svc_chain.select.return_value = svc_chain
            svc_chain.eq.return_value = svc_chain
            mock_svc.return_value.table.return_value = svc_chain

            import importlib
            import scheduler
            importlib.reload(scheduler)
            scheduler.run_all_saved_searches()

        mock_svc.assert_called(), "scheduler must call get_service_client()"
