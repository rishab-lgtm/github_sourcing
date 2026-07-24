"""Side-effect-free smoke test for notification controls and scheduler health."""

from contextlib import ExitStack
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


SAVED_SEARCH = {
    "search_id": "robotics-1",
    "user_email": "owner@m13.co",
    "name": "Robotics",
    "intent": "robotics deployment engineer",
    "mode": "Intent Search",
    "filters": {},
    "notify_on_new": True,
    "last_run_at": None,
    "last_result_count": 0,
}

CACHED_CANDIDATE = {
    "handle": "robot-builder",
    "name": "Robot Builder",
    "bio": "Deploying autonomous robot fleets",
    "company": "Robotics Lab",
    "location": "San Francisco",
    "followers": 120,
    "public_repos": 12,
    "top_repos": "fleet-tools(500⭐)",
    "founder_badges": "🚀 Building",
    "signal_score": 55,
    "match_reasons": ["Direct Robotics evidence: robotics"],
    "github_url": "https://github.com/robot-builder",
}

FEEDBACK_PROFILES = [
    {
        **CACHED_CANDIDATE,
        "handle": "robot-one",
        "_status": "contacted",
        "profile_archetype": "builder",
    },
    {
        **CACHED_CANDIDATE,
        "handle": "robot-two",
        "_status": "interested",
        "profile_archetype": "builder",
    },
    {
        "handle": "bio-one",
        "_status": "passed",
        "bio": "Biotech genomics researcher",
        "top_repos": "protein-lab(100⭐)",
        "founder_badges": "🔬 Researcher",
        "profile_archetype": "researcher",
        "followers": 100,
    },
    {
        "handle": "bio-two",
        "_status": "passed",
        "bio": "Protein biology researcher",
        "top_repos": "biology-tools(100⭐)",
        "founder_badges": "🔬 Researcher",
        "profile_archetype": "researcher",
        "followers": 100,
    },
]


def test_notification_test_button_and_scheduler_warning():
    stack = ExitStack()
    mocks = {}

    def mocked(target, **kwargs):
        mock = stack.enter_context(patch(target, **kwargs))
        mocks[target] = mock
        return mock

    mocked("database.upsert_user")
    mocked("database.get_notification_prefs", return_value={
        "notify_email": "owner@m13.co",
        "recap_frequency": "Weekly",
        "notify_on_new_match": True,
    })
    mocked("database.get_saved_searches", return_value=[SAVED_SEARCH])
    mocked("database.get_breakout_candidates", return_value=[])
    mocked("database.get_run_history", return_value=[])
    mocked("database.load_user_results", return_value=[CACHED_CANDIDATE])
    mocked("database.load_user_recap", return_value={})
    mocked("database.get_candidate_actions", return_value={})
    mocked(
        "database.get_user_pipeline",
        side_effect=lambda user_email, statuses=None: (
            FEEDBACK_PROFILES
            if statuses == ["interested", "contacted", "passed"]
            else []
        ),
    )
    mocked("database.get_watch_activity", return_value=[])
    mocked("database.get_radar_events", return_value=[])
    mocked("database.upsert_profiles")
    mocked("database.record_snapshots")
    mocked("database.set_candidate_action", return_value=True)
    mocked("database.set_candidate_monitoring", return_value=True)
    mocked("database.audit")
    mocked("github_sourcing.get_session_request_count", return_value=0)
    mocked("github_sourcing.set_current_user")
    mocked("github_sourcing.get_user_profile", return_value={
        "login": "robot-builder",
        "html_url": "https://github.com/robot-builder",
    })
    mocked("github_sourcing.get_user_repos", return_value=[])
    mocked("github_sourcing.format_profile", return_value={
        "handle": "robot-builder",
        "name": "Robot Builder",
        "github_url": "https://github.com/robot-builder",
    })
    mocked("streamlit_auth.logout")
    send_test = mocked("notifications.send_test_email", return_value=True)

    app = AppTest.from_file("app.py", default_timeout=10)
    app.session_state["authenticated"] = True
    app.session_state["user_email"] = "owner@m13.co"
    app.session_state["user_name"] = "Owner"

    with stack:
        app.run()
        assert not app.exception
        assert [tab.label for tab in app.tabs] == [
            "Search", "Pipeline", "Saved Searches", "Radar", "History", "Settings"
        ]
        assert any("have not run yet" in error.value for error in app.error)
        assert any(
            expander.label == "Why this person · @robot-builder"
            for expander in app.expander
        )
        assert any(
            "personalized from 4" in caption.value
            for caption in app.caption
        )
        assert not any(
            "profile-handle" in (code.value or "")
            for code in app.code
        )

        app.button(key="send_test_notification").click().run()
        send_test.assert_called_with("owner@m13.co")
        assert any("Test notification sent" in success.value for success in app.success)
        assert not app.exception

        handle_input = next(
            item for item in app.text_input
            if item.label == "GitHub handle or profile URL"
        )
        handle_input.set_value("https://github.com/robot-builder")
        app.button(
            key="FormSubmitter:track_handle_form-Add to Pipeline"
        ).click().run()
        mocks["database.set_candidate_action"].assert_called_with(
            "owner@m13.co",
            "robot-builder",
            "interested",
            "",
        )
        assert (
            app.session_state["candidate_actions"]["robot-builder"]["status"]
            == "interested"
        )
