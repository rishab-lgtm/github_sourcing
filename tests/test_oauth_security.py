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


def test_render_url_is_used_when_explicit_redirect_is_missing():
    state = AttrDict()
    with patch.object(streamlit_auth.st, "session_state", state), \
         patch.dict("os.environ", {
             "GOOGLE_CLIENT_ID": "client-id",
             "RENDER_EXTERNAL_URL": "https://github-sourcing.onrender.com/",
             "ALLOWED_EMAIL_DOMAIN": "m13.co",
         }, clear=True):
        url = streamlit_auth.get_google_oauth_url()
    query = parse_qs(urlparse(url).query)
    assert query["redirect_uri"] == ["https://github-sourcing.onrender.com"]


def test_localhost_is_used_only_outside_render_without_configuration():
    with patch.dict("os.environ", {}, clear=True):
        assert streamlit_auth.get_auth_redirect_url() == "http://localhost:8501"


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


def test_email_login_rejects_non_m13_recipient():
    with patch("notifications.send_email") as send:
        ok, message = streamlit_auth.request_email_login_code("person@gmail.com")
    assert ok is False
    assert "@m13.co" in message
    send.assert_not_called()


def test_email_login_code_is_one_time_and_creates_session():
    streamlit_auth._session_store().clear()
    state = AttrDict()
    with patch.object(streamlit_auth.st, "session_state", state), \
         patch("notifications.send_email", return_value=True) as send, \
         patch.object(streamlit_auth.secrets, "randbelow", return_value=123456), \
         patch.dict("os.environ", {"ALLOWED_EMAIL_DOMAIN": "m13.co"}, clear=False):
        sent, _ = streamlit_auth.request_email_login_code("rishab@m13.co")
        verified, _ = streamlit_auth.verify_email_login_code("rishab@m13.co", "123456")
        replayed, _ = streamlit_auth.verify_email_login_code("rishab@m13.co", "123456")

    assert sent is True
    assert verified is True
    assert replayed is False
    assert state.authenticated is True
    assert state.user_email == "rishab@m13.co"
    assert state.session_id
    send.assert_called_once()


def test_email_login_rejects_wrong_code():
    streamlit_auth._session_store().clear()
    state = AttrDict()
    with patch.object(streamlit_auth.st, "session_state", state), \
         patch("notifications.send_email", return_value=True), \
         patch.object(streamlit_auth.secrets, "randbelow", return_value=123456):
        streamlit_auth.request_email_login_code("rishab@m13.co")
        verified, message = streamlit_auth.verify_email_login_code("rishab@m13.co", "654321")
    assert verified is False
    assert "incorrect" in message
