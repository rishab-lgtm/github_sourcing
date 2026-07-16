"""
Streamlit Authentication Module
Google OAuth SSO for the AI Enrichment Engine.
Uses direct Google OAuth 2.0 with persistent sessions via browser localStorage.
"""

import html as html_mod
import os
import base64
import secrets
import time
import hmac
from urllib.parse import urlencode

import requests
import streamlit as st
from google.oauth2 import id_token
from google.auth.transport.requests import Request as GoogleAuthRequest

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SESSION_TTL = 30 * 24 * 60 * 60  # 30 days

# App state keys to persist across session reconnects.
# Auth keys and non-serializable objects (uploaded_files) are excluded.
_PERSISTED_STATE_KEYS = [
    "step", "sheet_mode", "use_google_sheets", "spreadsheet_id", "sheet_name",
    "tab_id", "original_columns", "selected_columns", "local_selected_columns",
    "data_source", "extracted_data", "local_input_data", "enrichment_status",
    "enriched_data", "companies_only", "one_row", "enrichment_job_id",
    "current_enrichment_step", "app_mode", "parking_lot_source",
    "parking_lot_data", "parking_lot_sheet_id", "parking_lot_tab_name",
    "parking_lot_view_mode", "parking_lot_company_idx",
    "x_network_search_results", "x_network_search_url",
    "x_network_search_count", "x_network_enriching_idx",
    "x_network_enriched_data", "x_network_search_cursor",
    "x_network_search_offset", "x_network_view_mode",
    "x_network_profile_view_idx",
]


@st.cache_resource
def _session_store():
    """Server-side session store. Persists across reruns but not server restarts."""
    return {}


def _create_session(email: str, name: str) -> str:
    """Create a server-side session and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    _session_store()[session_id] = {
        "email": email,
        "name": name,
        "created": time.time(),
    }
    return session_id


def _validate_session(session_id: str):
    """Validate a session ID. Returns user info dict or None."""
    store = _session_store()
    session = store.get(session_id)
    if not session:
        return None
    if time.time() - session["created"] > SESSION_TTL:
        del store[session_id]
        return None
    return session


def _destroy_session(session_id: str):
    """Remove a session from the server-side store."""
    _session_store().pop(session_id, None)


def save_app_state():
    """Snapshot persisted app state into the server-side session store.

    Call this at the end of each Streamlit rerun so the latest state is
    available if the session disconnects and later reconnects.
    """
    session_id = st.session_state.get("session_id")
    if not session_id:
        return
    store = _session_store()
    session = store.get(session_id)
    if not session:
        return
    snapshot = {}
    for key in _PERSISTED_STATE_KEYS:
        if key in st.session_state:
            snapshot[key] = st.session_state[key]
    session["app_state"] = snapshot


def restore_app_state(session_id: str):
    """Restore persisted app state from the server-side session store.

    Call this when a session is restored via localStorage session ID,
    after auth fields have already been set.
    """
    store = _session_store()
    session = store.get(session_id)
    if not session:
        return
    snapshot = session.get("app_state")
    if not snapshot:
        return
    for key, value in snapshot.items():
        st.session_state[key] = value


def _inject_ls_iframe(js_body: str, allow_top_nav: bool = False):
    """Inject a hidden srcdoc iframe that runs JavaScript with localStorage access.

    Uses srcdoc + allow-same-origin so the iframe shares the parent's origin
    and can access the same localStorage.
    """
    srcdoc_content = f"<script>{js_body}</script>"
    escaped = html_mod.escape(srcdoc_content, quote=True)
    sandbox = "allow-same-origin allow-scripts"
    if allow_top_nav:
        sandbox += " allow-top-navigation"
    st.markdown(
        f'<iframe srcdoc="{escaped}" sandbox="{sandbox}" '
        f'style="display:none;width:0;height:0;border:none;"></iframe>',
        unsafe_allow_html=True,
    )


def get_google_oauth_url(prompt: str = None, page: str = None,
                         source: str = None, company: str = None) -> str:
    """Generate the Google OAuth URL.

    Routing params (page, source, company) are encoded as pipe-delimited
    state so they survive the OAuth round-trip.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    redirect_uri = os.environ.get("AUTH_REDIRECT_URL", "http://localhost:8501")
    allowed_domain = os.environ.get("ALLOWED_EMAIL_DOMAIN", "")

    if not client_id:
        return "#"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        # Sourcing notifications use Resend; requesting Gmail or Sheets access
        # here would be unnecessary privilege and makes Workspace approval harder.
        "scope": "openid email profile",
    }

    # Restrict to allowed domain (skips account picker if only one match)
    if allowed_domain:
        params["hd"] = allowed_domain

    if prompt is not None:
        params["prompt"] = prompt

    # A cryptographically random nonce prevents login CSRF. Routing parameters
    # follow it and are accepted only after the nonce is verified on callback.
    # Store in both session_state AND the cache_resource store so it survives
    # the Streamlit session reset that happens on OAuth redirect.
    nonce = st.session_state.get("_oauth_state_nonce") or secrets.token_urlsafe(24)
    st.session_state._oauth_state_nonce = nonce
    _session_store()["_pending_nonce"] = nonce
    state_parts = [nonce, page or "", source or "", company or ""]
    # Strip trailing empty segments
    while state_parts and state_parts[-1] == "":
        state_parts.pop()
    params["state"] = "|".join(state_parts)

    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def handle_oauth_callback(code: str, state: str = "") -> bool:
    """
    Exchange the authorization code for tokens via Google's token endpoint,
    verify the ID token, and validate the email domain.

    Returns True if authentication succeeded, False otherwise.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("AUTH_REDIRECT_URL", "http://localhost:8501")

    if not client_id or not client_secret:
        st.session_state.auth_error = "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        return False

    state_nonce = state.split("|", 1)[0] if state else ""
    expected_nonce = st.session_state.get("_oauth_state_nonce") or _session_store().pop("_pending_nonce", "")
    if not state_nonce or not expected_nonce or not hmac.compare_digest(state_nonce, expected_nonce):
        st.session_state.auth_error = "Invalid or expired login request. Please start sign-in again."
        return False

    try:
        # Exchange authorization code for tokens
        token_response = requests.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=12)

        if token_response.status_code != 200:
            error_detail = token_response.json().get("error_description", token_response.text)
            st.session_state.auth_error = f"Failed to exchange authorization code: {error_detail}"
            return False

        tokens = token_response.json()
        # Verify the ID token and extract user info
        idinfo = id_token.verify_oauth2_token(
            tokens["id_token"],
            GoogleAuthRequest(),
            client_id,
        )

        email = idinfo.get("email", "")
        name = idinfo.get("name", email)

        if not email:
            st.session_state.auth_error = "Could not retrieve email from Google."
            return False
        if not idinfo.get("email_verified", False):
            st.session_state.auth_error = "Google did not verify this email address."
            return False

        # Check allowed email domain
        allowed_domain = os.environ.get("ALLOWED_EMAIL_DOMAIN", "")
        if allowed_domain and (
            not email.lower().endswith(f"@{allowed_domain.lower()}")
            or idinfo.get("hd", "").lower() != allowed_domain.lower()
        ):
            st.session_state.auth_error = (
                f"Access denied. Only @{allowed_domain} email addresses are authorized."
            )
            return False

        # Create persistent server-side session
        session_id = _create_session(email, name)

        # Store auth info in session state
        st.session_state.authenticated = True
        st.session_state.user_email = email
        st.session_state.user_name = name
        st.session_state.auth_error = None
        st.session_state.session_id = session_id
        st.session_state.pending_session_store = True
        st.session_state.pop("_oauth_state_nonce", None)
        return True

    except Exception as e:
        st.session_state.auth_error = f"Authentication failed: {str(e)}"
        return False


def check_auth() -> bool:
    """
    Check if the user is authenticated. Handles OAuth callback,
    persistent session restoration from localStorage, and error recovery.

    Returns True if authenticated, False otherwise.
    """
    # Already authenticated in this Streamlit session
    if st.session_state.get("authenticated"):
        # Store session token in browser localStorage after OAuth (runs on rerun)
        if st.session_state.get("pending_session_store"):
            sid = st.session_state.get("session_id", "")
            if sid:
                _inject_ls_iframe(
                    f'localStorage.setItem("aie_session","{sid}");'
                )
            st.session_state.pending_session_store = False
        return True

    # Handle OAuth callback (successful code return from Google)
    code = st.query_params.get("code")
    if code:
        state = st.query_params.get("state", "")  # routing params carried through OAuth
        authenticated = handle_oauth_callback(code, state)
        st.query_params.clear()
        if not authenticated:
            return False
        # Parse pipe-delimited state: nonce|page|source|company
        parts = state.split("|") if state else []
        if len(parts) >= 2 and parts[1]:
            st.session_state._pending_page = parts[1]
        if len(parts) >= 3 and parts[2]:
            st.session_state._pending_source = parts[2]
        if len(parts) >= 4 and parts[3]:
            st.session_state._pending_company = parts[3]
        return st.session_state.get("authenticated", False)

    # Handle session restoration from localStorage (redirect with ?session_id=)
    session_id = st.query_params.get("session_id")
    if session_id:
        page = st.query_params.get("page")
        source = st.query_params.get("source")
        company = st.query_params.get("company")
        st.query_params.clear()
        if page:
            st.session_state._pending_page = page
        if source:
            st.session_state._pending_source = source
        if company:
            st.session_state._pending_company = company
        session = _validate_session(session_id)
        if session:
            st.session_state.authenticated = True
            st.session_state.user_email = session["email"]
            st.session_state.user_name = session["name"]
            st.session_state.session_id = session_id
            st.session_state.auth_error = None
            # Restore app state saved before the session disconnected
            restore_app_state(session_id)
            return True
        else:
            # Invalid or expired session — clear browser localStorage
            st.session_state.clear_local_storage = True
            st.session_state.checked_local_storage = True
            return False

    # Handle OAuth error
    error = st.query_params.get("error")
    if error:
        st.query_params.clear()
        st.session_state.checked_local_storage = True
        return False

    # Clear localStorage if flagged (e.g. after logout or expired session)
    if st.session_state.get("clear_local_storage"):
        _inject_ls_iframe(
            'localStorage.removeItem("aie_session");'
            'localStorage.removeItem("aie_page");'
        )
        st.session_state.clear_local_storage = False
        st.session_state.checked_local_storage = True
        return False

    # Try reading session from localStorage (first page load only)
    if not st.session_state.get("checked_local_storage"):
        st.session_state.checked_local_storage = True
        redirect_base = os.environ.get("AUTH_REDIRECT_URL", "http://localhost:8501")
        current_page = st.query_params.get("page", "")
        current_source = st.query_params.get("source", "")
        current_company = st.query_params.get("company", "")
        extra_params = ""
        if current_source:
            extra_params += f"&source={current_source}"
        if current_company:
            extra_params += f"&company={current_company}"
        _inject_ls_iframe(
            'var sid=localStorage.getItem("aie_session");'
            'if(sid){'
            # If no page in the URL, fall back to the page saved in localStorage
            f'var pg="{current_page}"||localStorage.getItem("aie_page")||"";'
            f'window.top.location.href="{redirect_base}?session_id="+encodeURIComponent(sid)'
            f'+"&page="+encodeURIComponent(pg)+"{extra_params}";'
            '}',
            allow_top_nav=True,
        )
        # Don't st.stop() — let the login page render below as fallback
        return False

    return False


def save_page_to_local_storage(page_name: str):
    """Persist the current page name to browser localStorage.

    Called on each rerun so the user returns to the correct page
    even after a server restart clears the in-memory session store.
    """
    _inject_ls_iframe(
        f'localStorage.setItem("aie_page","{page_name}");'
    )


def logout():
    """Clear auth session state and destroy persistent session."""
    session_id = st.session_state.get("session_id")
    if session_id:
        _destroy_session(session_id)
    st.session_state.authenticated = False
    st.session_state.user_email = None
    st.session_state.user_name = None
    st.session_state.auth_error = None
    st.session_state.session_id = None
    st.session_state.checked_local_storage = False
    st.session_state.pending_session_store = False
    st.session_state.clear_local_storage = True


def show_login_page():
    """Render the styled login page with animated gradient background and glass-morphism card."""
    allowed_domain = os.environ.get("ALLOWED_EMAIL_DOMAIN", "")
    auth_error = st.session_state.get("auth_error")

    oauth_url = get_google_oauth_url(
        # "consent" forces Google to re-show and re-grant the full current scope
        # list every time, instead of silently reusing whatever was granted the
        # first time this account ever authorized the app — without it, scopes
        # added later (like gmail.send) never actually get granted just by
        # logging out and back in.
        prompt="select_account consent",
        page=st.query_params.get("page"),
        source=st.query_params.get("source"),
        company=st.query_params.get("company"),
    )
    if oauth_url == "#":
        auth_error = "Google OAuth not configured. Set GOOGLE_CLIENT_ID environment variable."

    # Build error alert HTML if needed
    error_html = ""
    if auth_error:
        error_html = (
            '<div class="error-box">'
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ff85d8" stroke-width="2" stroke-linecap="round" style="flex-shrink:0;margin-top:1px">'
            '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'
            f'</svg>{auth_error}</div>'
        )

    # Build the self-contained login page HTML
    login_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: 'Poppins', -apple-system, BlinkMacSystemFont, sans-serif;
        background: linear-gradient(145deg, #e8eef8 0%, #f5f7fc 50%, #dde6f5 100%);
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        position: relative;
    }}
    /* Soft blue glow */
    body::before {{
        content: '';
        position: absolute;
        width: 600px; height: 600px;
        border-radius: 50%;
        background: radial-gradient(circle, rgba(0,131,255,0.12) 0%, transparent 70%);
        top: 30%; left: 55%;
        transform: translate(-50%, -50%);
        pointer-events: none;
    }}
    .card {{
        position: relative; z-index: 10;
        background: #ffffff;
        border: 1px solid #E0E6F0;
        border-radius: 20px;
        padding: 48px 44px 40px;
        width: 100%; max-width: 400px;
        text-align: center;
        box-shadow: 0 8px 40px rgba(21,15,58,0.08);
    }}
    .m13-badge {{
        display: inline-block;
        background: #0083FF;
        color: #fff;
        font-weight: 800;
        font-size: 0.7rem;
        letter-spacing: 0.1em;
        padding: 5px 10px;
        border-radius: 6px;
        margin-bottom: 28px;
    }}
    h1 {{
        font-size: 1.6rem; font-weight: 700;
        color: #150F3A;
        letter-spacing: -0.03em;
        margin: 0 0 8px;
        line-height: 1.2;
    }}
    .subtitle {{
        color: #737368;
        font-size: 0.83rem;
        font-weight: 400;
        margin: 0 0 32px;
        line-height: 1.5;
    }}
    .google-btn {{
        display: flex; align-items: center; justify-content: center;
        gap: 10px; width: 100%;
        padding: 13px 20px;
        background: #150F3A;
        border: none;
        border-radius: 999px;
        font-size: 0.88rem; font-weight: 600;
        color: #ffffff;
        cursor: pointer; text-decoration: none;
        transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
        box-shadow: 0 4px 16px rgba(21,15,58,0.25);
        font-family: inherit;
        letter-spacing: 0.01em;
    }}
    .google-btn:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(21,15,58,0.35);
        background: #1e1554;
    }}
    .google-btn svg {{ width: 18px; height: 18px; flex-shrink: 0; }}
    .divider {{
        display: flex; align-items: center; gap: 12px;
        margin: 24px 0 0;
    }}
    .divider-line {{ flex: 1; height: 1px; background: #E8E8EC; }}
    .divider-text {{ color: #b0b0a8; font-size: 0.72rem; }}
    .footer-note {{
        margin-top: 16px;
        color: #737368;
        font-size: 0.72rem;
        line-height: 1.5;
    }}
    .footer-note strong {{ color: #150F3A; font-weight: 600; }}
    .error-box {{
        background: rgba(255,44,176,0.06);
        border: 1px solid rgba(255,44,176,0.2);
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 20px;
        color: #c0006e;
        font-size: 0.8rem;
        text-align: left;
        display: flex; align-items: flex-start; gap: 8px;
    }}
</style>
</head>
<body>
    <div class="card">
        <div class="m13-badge">M13</div>
        <h1>GitHub Sourcing</h1>
        <p class="subtitle">Sign in with your M13 account to continue</p>
        {error_html}
        <a href="{oauth_url}" target="_top" class="google-btn">
            <svg viewBox="0 0 24 24">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
            </svg>
            Continue with Google
        </a>
        {f'<div class="divider"><div class="divider-line"></div><div class="divider-text">restricted access</div><div class="divider-line"></div></div><p class="footer-note">Only <strong>@{allowed_domain}</strong> accounts are authorized</p>' if allowed_domain else ''}
    </div>
</body>
</html>"""

    encoded_html = base64.b64encode(login_html.encode()).decode()

    st.markdown("""
    <style>
        header[data-testid="stHeader"],
        footer, #MainMenu,
        div[data-testid="stDecoration"],
        div[data-testid="stToolbar"] { display: none !important; }
        .stApp { background: linear-gradient(145deg, #e8eef8 0%, #f5f7fc 50%, #dde6f5 100%) !important; }
        .stMainBlockContainer, .block-container { padding: 0 !important; max-width: 100% !important; }
        section[data-testid="stMain"] > div { padding: 0 !important; }
        .login-frame { border: none !important; width: 100%; height: 100vh; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        f'<iframe class="login-frame" src="data:text/html;base64,{encoded_html}" '
        f'sandbox="allow-scripts allow-top-navigation-by-user-activation" '
        f'scrolling="no"></iframe>',
        unsafe_allow_html=True,
    )
