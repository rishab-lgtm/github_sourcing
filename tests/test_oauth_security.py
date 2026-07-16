"""OAuth least-privilege and CSRF-state tests."""

from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import streamlit_auth


class AttrDict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def test_oauth_requests_only_identity_scopes():
    state = AttrDict()
    with patch.object(streamlit_auth.st, "session_state", state), \
         patch.dict("os.environ", {
             "GOOGLE_CLIENT_ID": "client-id",
             "AUTH_REDIRECT_URL": "http://localhost:8503",
             "ALLOWED_EMAIL_DOMAIN": "m13.co",
         }, clear=False):
        url = streamlit_auth.get_google_oauth_url(page="search")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["openid email profile"]
    assert "gmail" not in query["scope"][0]
    assert query["redirect_uri"] == ["http://localhost:8503"]
    assert query["state"][0].split("|")[1] == "search"


def test_callback_rejects_wrong_state_before_network_call():
    state = AttrDict(_oauth_state_nonce="expected")
    with patch.object(streamlit_auth.st, "session_state", state), \
         patch.object(streamlit_auth.requests, "post") as post, \
         patch.dict("os.environ", {
             "GOOGLE_CLIENT_ID": "client-id",
             "GOOGLE_CLIENT_SECRET": "secret",
         }, clear=False):
        assert streamlit_auth.handle_oauth_callback("code", "wrong|search") is False
    post.assert_not_called()
    assert "Invalid or expired" in state.auth_error


def test_parallel_login_states_are_unique_and_independently_valid():
    streamlit_auth._session_store().clear()
    first_state = AttrDict()
    second_state = AttrDict()
    env = {
        "GOOGLE_CLIENT_ID": "client-id",
        "AUTH_REDIRECT_URL": "http://localhost:8503",
    }

    with patch.object(streamlit_auth.st, "session_state", first_state), \
         patch.dict("os.environ", env, clear=False):
        first_url = streamlit_auth.get_google_oauth_url()
    with patch.object(streamlit_auth.st, "session_state", second_state), \
         patch.dict("os.environ", env, clear=False):
        second_url = streamlit_auth.get_google_oauth_url()

    first_nonce = parse_qs(urlparse(first_url).query)["state"][0]
    second_nonce = parse_qs(urlparse(second_url).query)["state"][0]
    assert first_nonce != second_nonce
    assert streamlit_auth._consume_oauth_nonce(first_nonce) is True
    assert streamlit_auth._consume_oauth_nonce(second_nonce) is True
    assert streamlit_auth._consume_oauth_nonce(first_nonce) is False


def test_oauth_state_expires_after_ten_minutes():
    streamlit_auth._session_store().clear()
    with patch("streamlit_auth.time.time", return_value=1000):
        streamlit_auth._store_oauth_nonce("short-lived")
    with patch("streamlit_auth.time.time", return_value=1000 + streamlit_auth.OAUTH_STATE_TTL + 1):
        assert streamlit_auth._consume_oauth_nonce("short-lived") is False
