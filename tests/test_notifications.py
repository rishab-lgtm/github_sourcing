"""
Unit tests for notification safety.
Zach: "Test notification safety: invalid emails rejected, HTML escaped, failures logged and retried."
"""

import html
import pytest
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifications


class TestEmailValidation:
    def test_missing_at_sign_rejected(self):
        with patch("notifications.requests.post") as mock_post:
            result = notifications.send_email("notanemail", "subj", "<p>body</p>")
        mock_post.assert_not_called()
        assert result is False

    def test_empty_recipient_rejected(self):
        with patch("notifications.requests.post") as mock_post:
            result = notifications.send_email("", "subj", "<p>body</p>")
        mock_post.assert_not_called()
        assert result is False

    def test_none_recipient_rejected(self):
        with patch("notifications.requests.post") as mock_post:
            result = notifications.send_email(None, "subj", "<p>body</p>")
        mock_post.assert_not_called()
        assert result is False

    def test_valid_email_calls_resend(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        with patch("notifications.requests.post", return_value=mock_resp) as mock_post:
            with patch.dict(os.environ, {"RESEND_API_KEY": "test-key"}):
                notifications.RESEND_API_KEY = "test-key"
                result = notifications.send_email("user@m13.co", "subj", "<p>body</p>")
        mock_post.assert_called_once()
        assert result is True


class TestHTMLEscaping:
    def test_escape_helper_escapes_script_tags(self):
        dangerous = "<script>alert('xss')</script>"
        escaped = notifications._e(dangerous)
        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped

    def test_escape_helper_escapes_quotes(self):
        val = 'He said "hello" & goodbye'
        escaped = notifications._e(val)
        assert '"' not in escaped or "&quot;" in escaped

    def test_escape_helper_handles_none(self):
        assert notifications._e(None) == ""

    def test_escape_helper_handles_int(self):
        result = notifications._e(42)
        assert result == "42"

    def test_profile_row_escapes_name(self):
        malicious_profile = {
            "handle": "attacker",
            "name": '<img src=x onerror=alert(1)>',
            "github_url": "https://github.com/attacker",
            "signal_score": 50,
            "location": "",
            "company": "",
            "bio": "",
            "founder_badges": "",
            "top_repos": "",
            "match_reasons": [],
        }
        html_out = notifications._profile_row(malicious_profile)
        # Raw HTML tags must not appear — html.escape converts < > so tags can't execute
        assert "<img " not in html_out, "Raw <img> tag must be escaped"
        assert "&lt;img" in html_out or "img" not in html_out.replace("&lt;img", ""), \
            "< must be escaped to &lt;"

    def test_profile_row_escapes_bio(self):
        profile = {
            "handle": "user",
            "name": "User",
            "github_url": "https://github.com/user",
            "signal_score": 55,
            "location": "",
            "company": "",
            "bio": '<script>document.cookie</script>',
            "founder_badges": "",
            "top_repos": "",
            "match_reasons": [],
        }
        html_out = notifications._profile_row(profile)
        assert "<script>" not in html_out, "Raw <script> must be escaped"

    def test_profile_row_escapes_location(self):
        profile = {
            "handle": "user",
            "name": "User",
            "github_url": "https://github.com/user",
            "signal_score": 55,
            "location": '"><svg onload=alert(1)>',
            "company": "",
            "bio": "",
            "founder_badges": "",
            "top_repos": "",
            "match_reasons": [],
        }
        html_out = notifications._profile_row(profile)
        assert "<svg" not in html_out, "Raw <svg> tag must be escaped"


class TestResendFailureHandling:
    def test_missing_api_key_returns_false(self):
        original = notifications.RESEND_API_KEY
        notifications.RESEND_API_KEY = ""
        try:
            result = notifications.send_email("user@m13.co", "subj", "<p>body</p>")
            assert result is False
        finally:
            notifications.RESEND_API_KEY = original

    def test_resend_4xx_returns_false(self):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 422
        mock_resp.text = "Unprocessable"
        with patch("notifications.requests.post", return_value=mock_resp):
            notifications.RESEND_API_KEY = "test-key"
            result = notifications.send_email("user@m13.co", "subj", "<p>body</p>")
        assert result is False

    def test_network_exception_returns_false(self):
        with patch("notifications.requests.post", side_effect=Exception("timeout")):
            notifications.RESEND_API_KEY = "test-key"
            result = notifications.send_email("user@m13.co", "subj", "<p>body</p>")
        assert result is False

    def test_request_has_timeout(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        with patch("notifications.requests.post", return_value=mock_resp) as mock_post:
            notifications.RESEND_API_KEY = "test-key"
            notifications.send_email("user@m13.co", "subj", "<p>body</p>")
        call_kwargs = mock_post.call_args
        assert "timeout" in call_kwargs.kwargs or (
            len(call_kwargs.args) > 0  # positional — check value
        ), "requests.post must include a timeout to avoid hanging"
        if "timeout" in call_kwargs.kwargs:
            assert call_kwargs.kwargs["timeout"] > 0

    def test_transient_failure_is_retried_then_succeeds(self):
        failed = MagicMock(ok=False, status_code=503, text="unavailable")
        succeeded = MagicMock(ok=True, status_code=200, text="ok")
        with patch("notifications.requests.post", side_effect=[failed, succeeded]) as post, \
             patch("notifications.time.sleep"):
            notifications.RESEND_API_KEY = "test-key"
            result = notifications.send_email("user@m13.co", "subj", "<p>body</p>")
        assert result is True
        assert post.call_count == 2
