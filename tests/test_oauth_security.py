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
