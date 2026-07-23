"""
M13 GitHub Sourcing — Streamlit UI
"""

from __future__ import annotations

import re
import logging
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

from streamlit_auth import check_auth, show_login_page, logout
from notifications import send_new_profiles_email, send_recap_email, send_weekly_digest
from github_sourcing import (
    find_sf_ai_contributors,
    find_trending_repo_authors,
    search_by_intent,
    expand_query,
    get_session_request_count,
    set_current_user,
    SESSION_REQUEST_LIMIT,
)
from search_service import SearchConfig, execute_search
from database import (
    upsert_user,
    upsert_profiles,
    get_all_candidates,
    get_new_since,
    create_search_run,
    update_search_run_count,
    record_matches,
    record_snapshots,
    get_run_history,
    save_search,
    get_saved_searches,
    delete_saved_search,
    get_prior_handles_for_search,
    get_notification_prefs,
    save_notification_prefs,
    get_breakout_candidates,
    get_snapshots_for_handle,
    load_user_results,
    save_user_results,
    load_user_recap,
    save_user_recap,
    audit,
    get_candidate_actions,
    set_candidate_action,
    get_user_pipeline,
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

DIGEST_RECIPIENTS = ["brent@m13.co", "thomas@m13.co"]

st.set_page_config(page_title="M13 GitHub Sourcing", page_icon="⚡", layout="wide")

# ── Auth gate ─────────────────────────────────────────────────────────────────
import os as _os
_DEV_BYPASS = _os.environ.get("DEV_BYPASS_AUTH") == "1"
if _DEV_BYPASS:
    st.session_state["user_email"] = "rishab@m13.co"
    st.session_state["user_name"] = "Rishab"
    st.session_state["authenticated"] = True
elif not check_auth():
    show_login_page()
    st.stop()

USER_EMAIL: str = st.session_state.get("user_email", "")
USER_NAME: str = st.session_state.get("user_name", USER_EMAIL)

# Register/update user on each login
if not st.session_state.get("_user_registered"):
    upsert_user(USER_EMAIL, USER_NAME)
    st.session_state["_user_registered"] = True

set_current_user(USER_EMAIL)

# ── Styles ────────────────────────────────────────────────────────────────────
st.markdown(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)
st.markdown("""
<style>
    :root { --space-blue:#150F3A; --wave-blue:#0083FF; --gray:#737368; --white:#FFFFFF; --off-white:#F7F7F8; --border:#E8E8EC; }
    html, body, [class*="css"], .stApp, .stApp * { font-family:'Poppins',-apple-system,BlinkMacSystemFont,sans-serif !important; -webkit-font-smoothing:antialiased; }
    body, .stApp { background:var(--off-white) !important; }
    #MainMenu, footer, header { visibility:hidden; }
    .block-container { padding-top:1.5rem; padding-bottom:2rem; }

    [data-testid="stSidebar"] { background:var(--space-blue); border-right:1px solid rgba(255,255,255,0.08); }
    [data-testid="stSidebar"] * { color:rgba(255,255,255,0.85) !important; }
    [data-testid="stSidebar"] label { color:rgba(255,255,255,0.5) !important; font-size:0.65rem !important; font-weight:600 !important; letter-spacing:0.1em !important; text-transform:uppercase !important; }
    [data-testid="stSidebar"] hr { border-color:rgba(255,255,255,0.1) !important; margin:1rem 0 !important; }
    [data-testid="stSidebar"] .stRadio label, [data-testid="stSidebar"] .stCheckbox label { font-size:0.83rem !important; text-transform:none !important; letter-spacing:0 !important; }
    [data-testid="stSidebar"] input, [data-testid="stSidebar"] .stSelectbox > div, [data-testid="stSidebar"] div[data-baseweb="select"] > div:first-child { background:rgba(255,255,255,0.07) !important; border-color:rgba(255,255,255,0.15) !important; border-radius:8px !important; }
    [data-testid="stSidebar"] span[data-baseweb="tag"] { background:rgba(0,131,255,0.2) !important; border-radius:999px !important; }

    .stButton > button { border-radius:999px !important; font-weight:600 !important; font-size:0.82rem !important; padding:0.5rem 1.25rem !important; transition:all 0.14s ease !important; border:1.5px solid var(--border) !important; background:var(--white) !important; color:var(--space-blue) !important; box-shadow:0 1px 2px rgba(21,15,58,0.06) !important; }
    .stButton > button:hover { border-color:#c5c5d0 !important; box-shadow:0 3px 8px rgba(21,15,58,0.1) !important; transform:translateY(-1px) !important; }
    .stButton > button[kind="primary"] { background:var(--wave-blue) !important; border-color:var(--wave-blue) !important; color:#fff !important; box-shadow:0 2px 8px rgba(0,131,255,0.35) !important; }
    .stButton > button[kind="primary"]:hover { background:#006FDB !important; border-color:#006FDB !important; box-shadow:0 4px 14px rgba(0,131,255,0.45) !important; }
    .stButton > button[kind="primary"] p { color:#fff !important; }

    [data-testid="metric-container"] { background:white; border-radius:14px; padding:1rem 1.25rem; border:1px solid var(--border); box-shadow:0 2px 8px rgba(21,15,58,0.05); }
    [data-testid="stMetricValue"] { font-size:2rem !important; font-weight:800 !important; color:var(--space-blue) !important; letter-spacing:-0.04em !important; }
    [data-testid="stMetricLabel"] { font-size:0.68rem !important; font-weight:600 !important; color:var(--gray) !important; text-transform:uppercase !important; letter-spacing:0.07em !important; }

    .profile-card { background:white; border-radius:16px; padding:1.25rem 1.5rem; border:1px solid var(--border); box-shadow:0 1px 4px rgba(21,15,58,0.04),0 4px 16px rgba(21,15,58,0.04); margin-bottom:0.75rem; transition:box-shadow 0.15s; }
    .profile-card:hover { box-shadow:0 2px 8px rgba(21,15,58,0.08),0 8px 24px rgba(21,15,58,0.07); }
    .profile-name { font-size:1.05rem; font-weight:700; color:var(--space-blue); }
    .profile-handle { font-size:0.78rem; color:var(--wave-blue); font-weight:600; text-decoration:none; }
    .profile-handle:hover { text-decoration:underline; }
    .profile-meta { font-size:0.75rem; color:rgba(21,15,58,0.45); }
    .profile-bio { font-size:0.82rem; color:rgba(21,15,58,0.65); margin-top:0.6rem; line-height:1.55; font-style:italic; padding:0.5rem 0.75rem; background:rgba(21,15,58,0.02); border-radius:8px; border-left:2px solid rgba(0,131,255,0.2); }
    .badge { display:inline-flex; align-items:center; gap:3px; background:rgba(0,131,255,0.08); color:#0055B3; border-radius:20px; padding:0.15rem 0.65rem; font-size:0.7rem; font-weight:600; }
    .reason-tag { display:inline-flex; align-items:center; background:#f8f9fa; color:#374151; border-radius:6px; padding:0.2rem 0.55rem; font-size:0.7rem; margin-right:0.3rem; margin-top:0.3rem; border:1px solid #e5e7eb; }
    .repo-tag { display:inline-block; background:rgba(0,131,255,0.05); color:#0055B3; border-radius:6px; padding:0.15rem 0.5rem; font-size:0.7rem; margin-right:0.25rem; margin-top:0.25rem; border:1px solid rgba(0,131,255,0.12); }
    .new-badge { display:inline-block; background:#0083FF; color:white; border-radius:5px; padding:0.08rem 0.45rem; font-size:0.58rem; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; vertical-align:middle; }
    .section-header { font-size:1.1rem; font-weight:700; color:var(--space-blue); margin:1.5rem 0 1rem; padding-bottom:0.5rem; border-bottom:2px solid rgba(0,131,255,0.15); }
    .intent-preview { background:rgba(0,131,255,0.05); border:1px solid rgba(0,131,255,0.15); border-radius:10px; padding:0.6rem 1rem; font-size:0.78rem; color:rgba(21,15,58,0.7); margin-bottom:1rem; }
    .saved-card { background:white; border-radius:12px; padding:1rem 1.25rem; border:1px solid var(--border); margin-bottom:0.6rem; box-shadow:0 1px 4px rgba(21,15,58,0.04); }
    .history-row { background:white; border-radius:10px; padding:0.7rem 1rem; border:1px solid var(--border); margin-bottom:0.4rem; font-size:0.83rem; }
    .stDownloadButton > button { border-radius:999px !important; font-weight:600 !important; font-size:0.8rem !important; background:var(--white) !important; color:var(--space-blue) !important; border:1.5px solid var(--border) !important; }
    .stDownloadButton > button:hover { border-color:var(--wave-blue) !important; color:var(--wave-blue) !important; }
    .stAlert { border-radius:10px !important; font-size:0.875rem !important; border:none !important; }
    .stTextInput > div > div > input, .stTextArea > div > div > textarea { border-radius:10px !important; border:1.5px solid var(--border) !important; background:var(--white) !important; font-size:0.875rem !important; color:var(--space-blue) !important; }
    .stTextInput > div > div > input:focus, .stTextArea > div > div > textarea:focus { border-color:var(--wave-blue) !important; box-shadow:0 0 0 3px rgba(0,131,255,0.12) !important; }
    .stTabs [data-baseweb="tab"] { font-size:0.83rem !important; font-weight:600 !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def total_stars(repos_str: str) -> int:
    return sum(int(s) for s in re.findall(r'\((\d+)⭐\)', repos_str or ""))


def score_class(score: int) -> str:
    if score >= 60: return "score-high"
    if score >= 50: return "score-mid"
    return "score-low"


def recap_due(frequency: str, last_recap: dict) -> bool:
    if not last_recap.get("sent_at"):
        return True
    last = datetime.fromisoformat(last_recap["sent_at"])
    delta = {"Daily": 1, "Bi-Daily": 2, "Weekly": 7, "Monthly": 30}[frequency]
    return datetime.now() >= last + timedelta(days=delta)


def _archetype_html(archetype: str, confidence: str, signals: list) -> str:
    if not archetype or archetype == "unknown":
        return ""
    icon = {"builder": "🔨", "researcher": "🔬"}.get(archetype, "")
    label = archetype.capitalize()
    conf_color = {"high": "#0a6641", "medium": "#7a6000", "low": "#6b7280"}.get(confidence, "#6b7280")
    conf_bg = {"high": "#d4f7e8", "medium": "#fff8d4", "low": "#f3f4f6"}.get(confidence, "#f3f4f6")
    tips = " · ".join(signals[:3]) if signals else ""
    tips_html = f'<div style="font-size:0.68rem;color:#6b7280;margin-top:3px">{tips}</div>' if tips else ""
    return f"""
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid #f0f0f5">
        <span style="font-size:0.65rem;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em">Profile reads as</span>
        <span style="margin-left:8px;background:{conf_bg};color:{conf_color};font-size:0.72rem;font-weight:700;padding:2px 10px;border-radius:8px">{icon} {label}</span>
        <span style="margin-left:6px;font-size:0.65rem;color:#9ca3af">({confidence} confidence)</span>
        {tips_html}
    </div>"""


_STATUS_LABELS = {
    "none": ("—", "#9ca3af", "#f3f4f6"),
    "interested": ("⭐ Interested", "#0a6641", "#d4f7e8"),
    "contacted": ("📨 Contacted", "#1d4ed8", "#dbeafe"),
    "passed": ("✗ Passed", "#6b7280", "#f3f4f6"),
}


def render_profile_card(row: dict, is_new: bool = False):
    handle = row.get("handle", "")
    name = row.get("name") or handle
    github_url = row.get("github_url", f"https://github.com/{handle}")
    linkedin_url = row.get("linkedin_url", "")
    location = row.get("location", "")
    company = row.get("company", "")
    bio = row.get("bio", "")
    badges = row.get("founder_badges", "")
    score = row.get("signal_score", 0)
    top_repos = row.get("top_repos", "")
    acct_age = row.get("account_age_years")
    followers = row.get("followers", 0) or 0
    public_repos = row.get("public_repos", 0) or 0
    reasons = row.get("match_reasons") or []
    archetype = row.get("profile_archetype", "")
    archetype_conf = row.get("archetype_confidence", "")
    archetype_signals = row.get("archetype_signals") or []

    meta_parts = []
    if location: meta_parts.append(f'<span style="display:inline-flex;align-items:center;gap:4px;color:rgba(21,15,58,0.5);font-size:0.75rem">&#x1F4CD; {location}</span>')
    if company: meta_parts.append(f'<span style="display:inline-flex;align-items:center;gap:4px;color:rgba(21,15,58,0.5);font-size:0.75rem">&#x1F3E2; {company}</span>')
    if acct_age is not None: meta_parts.append(f'<span style="display:inline-flex;align-items:center;gap:4px;color:rgba(21,15,58,0.5);font-size:0.75rem">&#x23F1; {acct_age}yr account</span>')

    badge_html = "".join(
        f'<span class="badge">{b.strip()}</span>'
        for b in str(badges).split("|") if b.strip()
    )
    repo_items = [r.strip() for r in str(top_repos).split(",") if r.strip()][:4]
    repos_html = "".join(f'<span class="repo-tag">{r}</span>' for r in repo_items)

    # Score breakdown — show what drove the score
    reasons_html = "".join(
        f'<span class="reason-tag">&#10003; {r}</span>' for r in reasons[:6]
    )

    new_html = '<span class="new-badge">NEW</span>' if is_new else ""
    bio_html = f'<div class="profile-bio">"{bio}"</div>' if bio and bio not in ("nan", "") else ""

    # Score color and label
    if score >= 70:
        score_bg, score_fg, score_label = "#d4f7e8", "#0a6641", "High"
    elif score >= 50:
        score_bg, score_fg, score_label = "#fff8d4", "#7a6000", "Mid"
    else:
        score_bg, score_fg, score_label = "#f3f4f6", "#4b5563", "Low"

    stats_html = ""
    if followers: stats_html += f'<span style="font-size:0.75rem;color:#6b7280;margin-right:12px"><strong style="color:#150F3A">{followers:,}</strong> followers</span>'
    if public_repos: stats_html += f'<span style="font-size:0.75rem;color:#6b7280;margin-right:12px"><strong style="color:#150F3A">{public_repos}</strong> repos</span>'
    stars = total_stars(top_repos)
    if stars: stats_html += f'<span style="font-size:0.75rem;color:#6b7280"><strong style="color:#150F3A">{stars:,}</strong> total stars</span>'

    actions = st.session_state.get("candidate_actions", {})
    current_action = actions.get(handle, {})
    current_status = current_action.get("status", "none")
    current_note = current_action.get("note", "")
    status_label, status_fg, status_bg = _STATUS_LABELS.get(current_status, _STATUS_LABELS["none"])
    status_chip = f'<span style="background:{status_bg};color:{status_fg};font-size:0.65rem;font-weight:700;padding:2px 8px;border-radius:6px;margin-left:6px">{status_label}</span>' if current_status != "none" else ""

    st.markdown(f"""
    <div class="profile-card">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem">
            <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                    <span class="profile-name">{name}</span>
                    {new_html}
                    {badge_html}
                    {status_chip}
                </div>
                <div style="display:flex;align-items:center;gap:10px;margin-top:3px;flex-wrap:wrap">
                    <a class="profile-handle" href="{github_url}" target="_blank" style="display:inline-flex;align-items:center;gap:4px">
                        @{handle}
                    </a>
                    {f'<a href="{linkedin_url}" target="_blank" style="display:inline-flex;align-items:center;gap:4px;font-size:0.72rem;color:#0a66c2;font-weight:600;text-decoration:none;background:#e8f0fb;padding:2px 8px;border-radius:6px">&#128279; LinkedIn</a>' if linkedin_url else ''}
                </div>
                <div class="profile-meta" style="margin-top:5px;display:flex;flex-wrap:wrap;gap:10px">{" ".join(meta_parts)}</div>
            </div>
            <div style="text-align:center;flex-shrink:0">
                <div style="background:{score_bg};color:{score_fg};border-radius:12px;padding:6px 14px;font-weight:800;font-size:1.4rem;letter-spacing:-0.02em;line-height:1">{score}</div>
                <div style="font-size:0.6rem;font-weight:600;color:{score_fg};text-transform:uppercase;letter-spacing:0.06em;margin-top:3px">{score_label} signal</div>
            </div>
        </div>
        {bio_html}
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid #f0f0f5">
            <div style="font-size:0.65rem;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">Why this score</div>
            <div>{reasons_html}</div>
        </div>
        {_archetype_html(archetype, archetype_conf, archetype_signals)}
        {f'<div style="margin-top:8px">{stats_html}</div>' if stats_html else ''}
        {f'<div style="margin-top:6px">{repos_html}</div>' if repos_html else ''}
    </div>
    """, unsafe_allow_html=True)

    # Status buttons + note — rendered outside the HTML block so Streamlit handles interactivity
    with st.expander("Track this person", expanded=(current_status != "none")):
        btn_cols = st.columns(4)
        for i, (st_key, (st_lbl, _, _)) in enumerate(_STATUS_LABELS.items()):
            if st_key == "none":
                continue
            btn_idx = i - 1
            if btn_idx < 3:
                pressed = btn_cols[btn_idx].button(
                    st_lbl, key=f"status_{handle}_{st_key}",
                    type="primary" if current_status == st_key else "secondary",
                    use_container_width=True,
                )
                if pressed and current_status != st_key:
                    set_candidate_action(USER_EMAIL, handle, st_key, current_note)
                    st.session_state["candidate_actions"][handle] = {"status": st_key, "note": current_note}
                    st.rerun()
        if current_status != "none":
            if btn_cols[3].button("Clear", key=f"status_{handle}_clear", use_container_width=True):
                set_candidate_action(USER_EMAIL, handle, "none", "")
                st.session_state["candidate_actions"][handle] = {"status": "none", "note": ""}
                st.rerun()
        note_val = st.text_input(
            "Note", value=current_note, placeholder="Add context, meeting notes…",
            key=f"note_{handle}", label_visibility="collapsed",
        )
        if note_val != current_note:
            set_candidate_action(USER_EMAIL, handle, current_status, note_val)
            st.session_state["candidate_actions"][handle] = {"status": current_status, "note": note_val}


def apply_filters(df: pd.DataFrame, prev_handles: set = None,
                  min_score: int = 0, filter_badges: list = None,
                  min_stars: int = 0, bio_keyword: str = "",
                  region: str = "", max_account_age: int = 15,
                  stealth_only: bool = False, show_new_only: bool = False,
                  hide_passed: bool = True, sort_by: str = "Signal Score") -> pd.DataFrame:
    for col in ["founder_badges", "company", "bio", "location", "top_repos"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df = df[df["signal_score"] >= min_score]

    if filter_badges:
        df = df[df["founder_badges"].apply(lambda b: any(badge in b for badge in filter_badges))]
    if min_stars > 0:
        df = df[df["top_repos"].apply(total_stars) >= min_stars]
    if bio_keyword.strip():
        df = df[df["bio"].str.lower().str.contains(bio_keyword.strip().lower(), na=False)]
    if region.strip():
        df = df[df["location"].str.lower().str.contains(region.strip().lower(), na=False)]
    if "account_age_years" in df.columns and max_account_age < 15:
        df = df[df["account_age_years"] <= max_account_age]
    if stealth_only:
        df = df[df["bio"].str.lower().str.contains("stealth", na=False)]
    if hide_passed:
        actions = st.session_state.get("candidate_actions", {})
        passed_handles = {h for h, a in actions.items() if a.get("status") == "passed"}
        if passed_handles:
            df = df[~df["handle"].isin(passed_handles)]
    if show_new_only and prev_handles:
        df = df[~df["handle"].isin(prev_handles)]

    if sort_by == "Followers" and "followers" in df.columns:
        df = df.sort_values("followers", ascending=False)
    elif sort_by == "Repo Stars":
        df = df.copy()
        df["_stars"] = df["top_repos"].apply(total_stars)
        df = df.sort_values("_stars", ascending=False)
    else:
        df = df.sort_values("signal_score", ascending=False)

    return df


def run_search(mode: str, intent: str, region: str, max_results: int = 75,
               saved_search_id: str = None, triggered_by: str = "manual") -> list:
    return execute_search(SearchConfig(
        mode=mode,
        intent=intent.strip(),
        region=region.strip(),
        max_results=max_results,
    ))


def display_results(results: list, prev_handles: set,
                    min_score: int, filter_badges: list, min_stars: int,
                    bio_keyword: str, region: str, max_account_age: int,
                    stealth_only: bool, show_new_only: bool, hide_passed: bool,
                    sort_by: str):
    new_handle_set = {p["handle"] for p in results if p["handle"] not in prev_handles}
    removed = len(prev_handles - {p["handle"] for p in results})

    df = apply_filters(
        pd.DataFrame(results), prev_handles=prev_handles,
        min_score=min_score, filter_badges=filter_badges, min_stars=min_stars,
        bio_keyword=bio_keyword, region=region, max_account_age=max_account_age,
        stealth_only=stealth_only, show_new_only=show_new_only, hide_passed=hide_passed,
        sort_by=sort_by,
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Found", len(results))
    m2.metric("After Filters", len(df))
    m3.metric("New This Run", len(new_handle_set))

    if df.empty:
        st.info("No profiles match your filters. Try lowering the score threshold.")
        return

    st.markdown(f'<div class="section-header">Results — {len(df)} profiles</div>', unsafe_allow_html=True)
    for _, row in df.iterrows():
        render_profile_card(row.to_dict(), is_new=row.get("handle") in new_handle_set)

    st.markdown("")
    csv_data = df.drop(columns=["_stars"], errors="ignore").to_csv(index=False)
    st.download_button(
        "⬇  Download CSV", data=csv_data,
        file_name=f"m13_sourcing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )


# ── Load notification prefs & pipeline actions ────────────────────────────────
prefs = get_notification_prefs(USER_EMAIL)

if "candidate_actions" not in st.session_state:
    try:
        st.session_state["candidate_actions"] = get_candidate_actions(USER_EMAIL)
    except Exception:
        st.session_state["candidate_actions"] = {}


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"""
    <div style="padding:0.5rem 0 1rem">
        <div style="font-size:1.1rem;font-weight:700;">⚡ GitHub Sourcing</div>
        <div style="font-size:0.72rem;opacity:0.5;margin-top:0.2rem;">Surface engineers before they're on anyone's radar</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    st.markdown("#### Scan Mode")
    mode = st.radio("", ["Intent Search", "SF-Based AI Contributors", "Trending Repo Authors"],
                    label_visibility="collapsed")

    st.markdown("---")
    st.markdown("#### Filters")
    max_results = st.slider("Max results", 10, 200, 75, step=5)
    min_score   = st.slider("Min signal score", 0, 100, 20, step=5)
    filter_badges = st.multiselect("Founder signals",
                                   ["🏛 Top Lab", "🚀 Building", "🔬 Researcher"], default=[])
    min_stars   = st.number_input("Min repo stars", min_value=0, value=0, step=50)
    bio_keyword = st.text_input("Bio keyword", placeholder="stealth, agent, infra…")
    region      = st.text_input("Region", placeholder="SF, NYC, London — blank for all")
    max_account_age = st.slider("Max account age (yrs)", 1, 15, 15)
    stealth_only    = st.checkbox("Stealth only", value=False)
    show_new_only   = st.checkbox("Only show new profiles", value=False)
    hide_passed     = st.checkbox("Hide passed profiles", value=True)
    sort_by         = st.selectbox("Sort by", ["Signal Score", "Followers", "Repo Stars"])

    # Rate limit indicator
    used = get_session_request_count()
    pct = int(used / SESSION_REQUEST_LIMIT * 100)
    if pct > 80:
        st.markdown(f"<div style='font-size:0.72rem;color:rgba(255,200,0,0.8)'>⚠ API usage: {used}/{SESSION_REQUEST_LIMIT} requests this session</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown(f"""
    <div style="font-size:0.72rem;opacity:0.6;margin-bottom:0.5rem;">
        Signed in as<br><strong style="opacity:1">{USER_NAME}</strong>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Sign out"):
        audit(USER_EMAIL, "logout")
        logout()
        st.rerun()
    st.caption("Built for M13 · GitHub API")


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="background:linear-gradient(135deg,#150F3A 0%,#1e1660 100%);border-radius:18px;
            padding:1.75rem 2rem;margin-bottom:1.75rem;">
    <div style="color:rgba(255,255,255,0.5);font-size:0.65rem;font-weight:600;
                letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.3rem;">M13 Internal</div>
    <div style="color:white;font-size:1.6rem;font-weight:700;letter-spacing:-0.02em;line-height:1.1;">GitHub Sourcing</div>
    <div style="color:rgba(255,255,255,0.55);font-size:0.82rem;margin-top:0.4rem;">
        Find talented engineers and future founders before they appear on AngelList or TechCrunch.
    </div>
</div>
""", unsafe_allow_html=True)


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_search, tab_pipeline, tab_saved, tab_breakout, tab_history, tab_settings = st.tabs(
    ["Search", "Pipeline", "Saved Searches", "Breakouts", "History", "Settings"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Search
# ═══════════════════════════════════════════════════════════════════════════════
with tab_search:
    intent = ""
    if mode == "Intent Search":
        st.markdown("#### What are you looking for?")
        intent = st.text_input(
            "", placeholder='"biotech AI researcher", "climate infra engineer", "stealth AI founder"',
            label_visibility="collapsed",
        )
        if intent:
            expanded = expand_query(intent)
            top_terms = sorted(expanded, key=len, reverse=True)[:10]
            st.markdown(
                f'<div class="intent-preview">🔍 Searching for: <strong>{" · ".join(top_terms)}</strong></div>',
                unsafe_allow_html=True,
            )
        st.markdown("")

    c1, c2, c3, c4 = st.columns([3, 1.4, 1.4, 1.4])
    with c1:
        label = "⚡  Search" if mode == "Intent Search" else "⚡  Run Scan"
        run_btn = st.button(label, type="primary", use_container_width=True,
                            disabled=(mode == "Intent Search" and not intent.strip()))
    with c2:
        save_btn_top = st.button("Save Search", use_container_width=True,
                                 disabled=(mode == "Intent Search" and not intent.strip()))
    with c3:
        recap_btn = st.button("Send Recap", use_container_width=True)
    with c4:
        digest_btn = st.button("Weekly Digest", use_container_width=True)

    if save_btn_top and intent.strip():
        st.session_state["_show_save_form"] = True
        st.session_state["_save_intent"] = intent.strip()

    if st.session_state.get("_show_save_form"):
        with st.container():
            st.markdown("---")
            st.markdown("**Save this search**")
            qs_name = st.text_input("Name", value=st.session_state.get("_save_intent", "")[:60], key="qs_name")
            qs_notify = st.checkbox("Email me when new people match", value=True, key="qs_notify")
            col_sv, col_cx = st.columns([1, 4])
            with col_sv:
                if st.button("Save", type="primary", key="qs_submit"):
                    save_search(
                        user_email=USER_EMAIL,
                        name=qs_name.strip() or st.session_state.get("_save_intent", ""),
                        intent=st.session_state.get("_save_intent", ""),
                        mode=mode,
                        filters={"region": region, "bio_keyword": bio_keyword},
                        notify_on_new=qs_notify,
                    )
                    st.session_state["_show_save_form"] = False
                    st.success(f"Saved '{qs_name}'! Go to Saved Searches to see it.")
                    st.rerun()
            with col_cx:
                if st.button("Cancel", key="qs_cancel"):
                    st.session_state["_show_save_form"] = False
                    st.rerun()

    notify_email = prefs.get("notify_email") or USER_EMAIL
    recap_frequency = prefs.get("recap_frequency", "Weekly")

    if digest_btn:
        with st.spinner("Pulling candidates from database..."):
            try:
                new_candidates = get_new_since(days=7)
                all_candidates = get_all_candidates()
                if not new_candidates:
                    st.info("No new candidates in the last 7 days.")
                else:
                    for email in DIGEST_RECIPIENTS:
                        send_weekly_digest(new_profiles=new_candidates, all_profiles=all_candidates, to_email=email)
                    audit(USER_EMAIL, "digest_sent", {"count": len(new_candidates)})
                    st.success(f"Weekly digest sent to Brent & Thomas — {len(new_candidates)} new candidates.")
            except Exception as e:
                st.error(f"Failed: {e}")

    if recap_btn:
        last_results = load_user_results(USER_EMAIL)
        last_recap = load_user_recap(USER_EMAIL)
        if not last_results:
            st.warning("No scan results yet — run a scan first.")
        else:
            prev_handles = set(last_recap.get("handles", []))
            current_handles = {p["handle"] for p in last_results}
            new_profiles = [p for p in last_results if p["handle"] not in prev_handles]
            removed_handles = list(prev_handles - current_handles)
            with st.spinner("Sending recap..."):
                sent = send_recap_email(
                    profiles=last_results, new_profiles=new_profiles,
                    removed_handles=removed_handles, to_email=notify_email,
                    frequency=recap_frequency,
                )
            if sent:
                save_user_recap(USER_EMAIL, {"sent_at": datetime.now().isoformat(), "handles": list(current_handles)})
                audit(USER_EMAIL, "recap_sent", {"to": notify_email, "count": len(last_results)})
                st.success(f"Recap sent to {notify_email}!")
            else:
                st.error("Failed — check your Resend API key.")

    if run_btn:
        scan_label = intent.strip() if mode == "Intent Search" else mode
        with st.spinner(f"Searching GitHub for '{scan_label}'… this takes 2–3 minutes"):
            results = run_search(mode, intent, region, max_results=max_results)

        if not results:
            st.warning("No profiles found. Try broadening your search.")
        else:
            run_id = create_search_run(
                user_email=USER_EMAIL, intent=scan_label, mode=mode,
                filters={"min_score": min_score, "region": region, "bio_keyword": bio_keyword},
            )
            prev_results = load_user_results(USER_EMAIL)
            prev_handles = {p["handle"] for p in prev_results}
            new_profiles = [p for p in results if p["handle"] not in prev_handles]

            save_user_results(USER_EMAIL, results)

            with st.spinner("Saving to database..."):
                try:
                    db_result = upsert_profiles(results)
                    record_matches(run_id, results, source_query=scan_label)
                    record_snapshots(results)
                    update_search_run_count(run_id, len(results))
                    audit(USER_EMAIL, "search_run", {"intent": scan_label, "mode": mode, "count": len(results)})
                    st.caption(f"Saved — {len(db_result['new'])} new, {len(db_result['updated'])} updated")
                except Exception as e:
                    st.caption(f"Database save failed: {e}")

            if new_profiles and notify_email and prefs.get("notify_on_new_match", True):
                send_new_profiles_email(new_profiles, to_email=notify_email)

            last_recap = load_user_recap(USER_EMAIL)
            if recap_due(recap_frequency, last_recap) and notify_email:
                removed_handles = list(prev_handles - {p["handle"] for p in results})
                sent = send_recap_email(
                    profiles=results, new_profiles=new_profiles,
                    removed_handles=removed_handles, to_email=notify_email,
                    frequency=recap_frequency,
                )
                if sent:
                    save_user_recap(USER_EMAIL, {
                        "sent_at": datetime.now().isoformat(),
                        "handles": [p["handle"] for p in results],
                    })

            display_results(
                results, prev_handles,
                min_score, filter_badges, min_stars, bio_keyword, region,
                max_account_age, stealth_only, show_new_only, hide_passed, sort_by,
            )

            # Save search — prominent, not hidden
            st.markdown("""
            <div style="background:linear-gradient(135deg,rgba(0,131,255,0.06),rgba(0,131,255,0.02));
                        border:1px solid rgba(0,131,255,0.2);border-radius:14px;padding:1.25rem 1.5rem;margin-top:1.5rem">
                <div style="font-weight:700;color:#150F3A;font-size:0.95rem;margin-bottom:0.25rem">
                    Save this search
                </div>
                <div style="font-size:0.78rem;color:#6b7280;margin-bottom:1rem">
                    Auto-runs on a schedule and emails you when new people match.
                </div>
            </div>
            """, unsafe_allow_html=True)
            col_sn, col_sb = st.columns([4, 1])
            with col_sn:
                save_name = st.text_input("Search name", value=scan_label[:60], key="save_name", label_visibility="collapsed", placeholder="Name this search…")
            with col_sb:
                notify_toggle = st.checkbox("Email alerts", value=True, key="save_notify")
            if st.button("Save & set up alerts", type="primary", key="do_save"):
                if save_name.strip():
                    save_search(
                        user_email=USER_EMAIL,
                        name=save_name.strip(),
                        intent=scan_label,
                        mode=mode,
                        filters={"region": region, "bio_keyword": bio_keyword},
                        notify_on_new=notify_toggle,
                    )
                    st.success(f"Saved '{save_name}' — it will auto-run and alert you when new people match.")
                else:
                    st.warning("Enter a name first.")
    else:
        # Show cached results
        last_results = load_user_results(USER_EMAIL)
        if not last_results:
            st.markdown("""
            <div style="text-align:center;padding:4rem 1rem;color:rgba(21,15,58,0.4);">
                <div style="font-size:3rem;margin-bottom:0.75rem;">⚡</div>
                <p style="font-size:1rem;font-weight:600;color:rgba(21,15,58,0.6);">No scan run yet</p>
                <p>Choose a mode and hit <strong>Run Scan</strong> to surface high-signal engineers.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("Showing your cached results from last scan. Hit **Search** to refresh.")
            display_results(
                last_results, set(),
                min_score, filter_badges, min_stars, bio_keyword, region,
                max_account_age, stealth_only, show_new_only, hide_passed, sort_by,
            )
            if mode == "Intent Search" and intent.strip():
                scan_label = intent.strip()
                st.markdown("""
                <div style="background:linear-gradient(135deg,rgba(0,131,255,0.06),rgba(0,131,255,0.02));
                            border:1px solid rgba(0,131,255,0.2);border-radius:14px;padding:1.25rem 1.5rem;margin-top:1.5rem">
                    <div style="font-weight:700;color:#150F3A;font-size:0.95rem;margin-bottom:0.25rem">Save this search</div>
                    <div style="font-size:0.78rem;color:#6b7280;margin-bottom:1rem">Auto-runs on a schedule and emails you when new people match.</div>
                </div>
                """, unsafe_allow_html=True)
                col_sn2, col_sb2 = st.columns([4, 1])
                with col_sn2:
                    save_name2 = st.text_input("Search name", value=scan_label[:60], key="save_name2", label_visibility="collapsed", placeholder="Name this search…")
                with col_sb2:
                    notify_toggle2 = st.checkbox("Email alerts", value=True, key="save_notify2")
                if st.button("Save & set up alerts", type="primary", key="do_save2"):
                    if save_name2.strip():
                        save_search(user_email=USER_EMAIL, name=save_name2.strip(), intent=scan_label,
                                    mode=mode, filters={"region": region, "bio_keyword": bio_keyword},
                                    notify_on_new=notify_toggle2)
                        st.success(f"Saved '{save_name2}' — will auto-run and alert you when new people match.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Pipeline
# ═══════════════════════════════════════════════════════════════════════════════
with tab_pipeline:
    st.markdown('<div class="section-header">Your Pipeline</div>', unsafe_allow_html=True)
    st.markdown("""
    <p style="font-size:0.83rem;color:rgba(21,15,58,0.6);margin-top:-0.5rem;margin-bottom:1rem;">
        Everyone you've marked as Interested or Contacted across all searches.
    </p>
    """, unsafe_allow_html=True)

    pipe_col1, pipe_col2 = st.columns([2, 1])
    with pipe_col1:
        pipe_status_filter = st.multiselect(
            "Show statuses", ["interested", "contacted", "passed"],
            default=["interested", "contacted"], label_visibility="collapsed",
        )
    with pipe_col2:
        pipe_refresh = st.button("🔄 Refresh", key="pipe_refresh")

    if "pipeline_data" not in st.session_state or pipe_refresh:
        try:
            st.session_state["pipeline_data"] = get_user_pipeline(
                USER_EMAIL, statuses=pipe_status_filter or ["interested", "contacted"]
            )
        except Exception as e:
            st.session_state["pipeline_data"] = []
            st.warning(f"Could not load pipeline — run the schema migration first. ({e})")

    pipeline = st.session_state.get("pipeline_data", [])

    if not pipeline:
        st.markdown("""
        <div style="text-align:center;padding:3rem 1rem;color:rgba(21,15,58,0.4);">
            <div style="font-size:2.5rem;margin-bottom:0.5rem;">📋</div>
            <p>No one tracked yet.<br>
            Run a search, then mark people as <strong>Interested</strong> or <strong>Contacted</strong>.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        pm1, pm2, pm3 = st.columns(3)
        pm1.metric("Tracking", len(pipeline))
        pm2.metric("Interested", sum(1 for p in pipeline if p.get("_status") == "interested"))
        pm3.metric("Contacted", sum(1 for p in pipeline if p.get("_status") == "contacted"))
        st.markdown("")

        for person in pipeline:
            handle = person.get("handle", "")
            status = person.get("_status", "")
            note = person.get("_note", "")
            updated = person.get("_action_updated", "")
            github_url = person.get("github_url") or f"https://github.com/{handle}"
            linkedin_url = person.get("linkedin_url", "")
            name = person.get("name") or handle
            bio = person.get("bio") or ""
            company = person.get("company") or ""
            score = person.get("signal_score") or 0
            badges = person.get("founder_badges") or ""
            _, status_fg, status_bg = _STATUS_LABELS.get(status, _STATUS_LABELS["none"])
            status_label = _STATUS_LABELS.get(status, _STATUS_LABELS["none"])[0]

            li_html = f'<a href="{linkedin_url}" target="_blank" style="font-size:0.72rem;color:#0a66c2;font-weight:600;text-decoration:none;background:#e8f0fb;padding:2px 8px;border-radius:6px">&#128279; LinkedIn</a>' if linkedin_url else ""
            badge_html = "".join(f'<span class="badge">{b.strip()}</span>' for b in str(badges).split("|") if b.strip())
            note_html = f'<div style="font-size:0.78rem;color:#6b7280;margin-top:6px;padding:4px 8px;background:#f9fafb;border-radius:6px;border-left:2px solid #e5e7eb">📝 {note}</div>' if note else ""

            st.markdown(f"""
            <div class="profile-card">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div style="flex:1">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                            <span class="profile-name">{name}</span>
                            <span style="background:{status_bg};color:{status_fg};font-size:0.65rem;font-weight:700;padding:2px 8px;border-radius:6px">{status_label}</span>
                            {badge_html}
                        </div>
                        <div style="display:flex;align-items:center;gap:10px;margin-top:4px;flex-wrap:wrap">
                            <a class="profile-handle" href="{github_url}" target="_blank">@{handle}</a>
                            {li_html}
                            {f'<span style="font-size:0.75rem;color:#9ca3af">{company}</span>' if company else ''}
                        </div>
                        {f'<div class="profile-bio">"{bio}"</div>' if bio else ''}
                        {note_html}
                    </div>
                    <div style="text-align:center;flex-shrink:0">
                        <div style="background:#f3f4f6;color:#150F3A;border-radius:12px;padding:6px 14px;font-weight:800;font-size:1.4rem">{score}</div>
                        <div style="font-size:0.6rem;color:#9ca3af;margin-top:2px">Updated {updated}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("")
        if pipeline:
            pipe_csv = pd.DataFrame([{
                "handle": p.get("handle"), "name": p.get("name"), "status": p.get("_status"),
                "note": p.get("_note"), "company": p.get("company"), "bio": p.get("bio"),
                "signal_score": p.get("signal_score"), "github_url": p.get("github_url"),
            } for p in pipeline]).to_csv(index=False)
            st.download_button("⬇ Export Pipeline CSV", data=pipe_csv,
                               file_name="m13_pipeline.csv", mime="text/csv")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Saved Searches
# ═══════════════════════════════════════════════════════════════════════════════
with tab_saved:
    st.markdown('<div class="section-header">Your Saved Searches</div>', unsafe_allow_html=True)
    st.markdown("""
    <p style="font-size:0.83rem;color:rgba(21,15,58,0.6);margin-top:-0.5rem;margin-bottom:1rem;">
        Saved searches run automatically via the scheduler (<code>python3 scheduler.py</code>) and notify you when new candidates match.
    </p>
    """, unsafe_allow_html=True)

    saved = get_saved_searches(USER_EMAIL)

    if not saved:
        st.markdown("""
        <div style="text-align:center;padding:3rem 1rem;color:rgba(21,15,58,0.4);">
            <div style="font-size:2.5rem;margin-bottom:0.5rem;">⭐</div>
            <p>No saved searches yet.<br>Run a search and click <strong>Save this search</strong> to get started.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        for s in saved:
            last_run = s.get("last_run_at", "")[:10] if s.get("last_run_at") else "Never"
            notify_icon = "🔔" if s.get("notify_on_new") else "🔕"
            col_a, col_b, col_c = st.columns([5, 1.2, 1])
            with col_a:
                st.markdown(f"""
                <div class="saved-card">
                    <div style="font-weight:700;color:#150F3A;font-size:0.95rem;">{s['name']}</div>
                    <div style="font-size:0.75rem;color:#737368;margin-top:0.2rem;">
                        {s['mode']} · {notify_icon} notify on new · Last run: {last_run} · {s.get('last_result_count',0)} results
                    </div>
                    <div style="font-size:0.78rem;color:#0083FF;margin-top:0.3rem;">"{s['intent']}"</div>
                </div>
                """, unsafe_allow_html=True)
            with col_b:
                if st.button("▶ Run now", key=f"run_{s['search_id']}"):
                    with st.spinner(f"Running '{s['name']}'…"):
                        res = run_search(s["mode"], s["intent"], s.get("filters", {}).get("region", ""))
                    if res:
                        prior = get_prior_handles_for_search(s["search_id"])
                        new_p = [p for p in res if p["handle"] not in prior]
                        run_id = create_search_run(
                            user_email=USER_EMAIL, intent=s["intent"], mode=s["mode"],
                            filters=s.get("filters") or {}, saved_search_id=s["search_id"],
                        )
                        upsert_profiles(res)
                        record_matches(run_id, res, source_query=s["intent"])
                        update_search_run_count(run_id, len(res))
                        from database import update_saved_search_last_run
                        update_saved_search_last_run(s["search_id"], len(res))
                        st.success(f"{len(res)} results — {len(new_p)} new")
                        if new_p and s.get("notify_on_new"):
                            send_new_profiles_email(new_p, to_email=prefs.get("notify_email") or USER_EMAIL)
                            audit(USER_EMAIL, "notification_sent", {"search": s["name"], "new_count": len(new_p)})
                    else:
                        st.warning("No results.")
            with col_c:
                if st.button("🗑 Delete", key=f"del_{s['search_id']}"):
                    delete_saved_search(s["search_id"], USER_EMAIL)
                    st.rerun()

    st.markdown("---")
    st.markdown("#### Add New Saved Search")
    with st.form("new_saved_search"):
        ns_name   = st.text_input("Name", placeholder="e.g. Biotech AI Researchers")
        ns_intent = st.text_input("Search intent", placeholder="biotech AI researcher")
        ns_mode   = st.selectbox("Mode", ["Intent Search", "SF-Based AI Contributors", "Trending Repo Authors"])
        ns_region = st.text_input("Region filter", placeholder="SF, NYC — blank for all")
        ns_notify = st.checkbox("Notify me when new candidates match", value=True)
        submitted = st.form_submit_button("Save Search", type="primary")
        if submitted:
            if ns_name.strip() and ns_intent.strip():
                save_search(
                    user_email=USER_EMAIL, name=ns_name.strip(),
                    intent=ns_intent.strip(), mode=ns_mode,
                    filters={"region": ns_region.strip()},
                    notify_on_new=ns_notify,
                )
                st.success(f"Saved '{ns_name}'!")
                st.rerun()
            else:
                st.warning("Name and intent are required.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Breakouts
# ═══════════════════════════════════════════════════════════════════════════════
with tab_breakout:
    st.markdown('<div class="section-header">🚀 Breakout Candidates</div>', unsafe_allow_html=True)
    st.markdown("""
    <p style="font-size:0.83rem;color:rgba(21,15,58,0.6);margin-top:-0.5rem;margin-bottom:1rem;">
        GitHub profiles that have 3x'd their followers or repo stars recently —
        people gaining momentum fast, before they're on anyone's radar.
        Snapshots are recorded on every scan run; accuracy improves over time.
    </p>
    """, unsafe_allow_html=True)

    bo_col1, bo_col2, bo_col3 = st.columns([1, 1, 2])
    with bo_col1:
        bo_days = st.selectbox("Lookback window", [7, 14, 30, 60, 90], index=2,
                               format_func=lambda d: f"Last {d} days")
    with bo_col2:
        bo_mult = st.selectbox("Min growth multiplier", [2.0, 3.0, 5.0, 10.0], index=1,
                               format_func=lambda m: f"{m}x")
    with bo_col3:
        bo_min_abs = st.number_input("Min absolute follower/star gain", min_value=0, value=100, step=50)

    bo_refresh = st.button("🔄 Refresh", key="bo_refresh")

    if "breakout_data" not in st.session_state or bo_refresh:
        with st.spinner("Scanning for breakout candidates…"):
            st.session_state["breakout_data"] = get_breakout_candidates(
                days=bo_days, min_multiplier=bo_mult, min_abs_growth=int(bo_min_abs)
            )

    breakouts = st.session_state.get("breakout_data", [])

    if not breakouts:
        st.markdown("""
        <div style="text-align:center;padding:3rem 1rem;color:rgba(21,15,58,0.4);">
            <div style="font-size:2.5rem;margin-bottom:0.5rem;">📈</div>
            <p>No breakout candidates found with these settings.<br>
            Try a longer lookback window or lower multiplier.<br>
            <em>Breakouts require at least 2 snapshot runs to compute velocity.</em></p>
        </div>
        """, unsafe_allow_html=True)
    else:
        bm1, bm2, bm3 = st.columns(3)
        bm1.metric("Breakout Candidates", len(breakouts))
        bm2.metric("Fastest Follower Growth",
                   f"{max(b['follower_mult'] for b in breakouts)}x",
                   delta=f"+{max(b['follower_gain'] for b in breakouts):,} followers")
        bm3.metric("Fastest Star Growth",
                   f"{max(b['star_mult'] for b in breakouts)}x",
                   delta=f"+{max(b['star_gain'] for b in breakouts):,} stars")

        st.markdown("")

        for b in breakouts:
            handle = b["handle"]
            url = b["github_url"]
            f_mult = b["follower_mult"]
            s_mult = b["star_mult"]
            signal = "🚀 Follower surge" if f_mult >= s_mult else "⭐ Star surge"
            top_mult = max(f_mult, s_mult)
            score_delta = b["score_delta"]
            delta_str = f"+{score_delta}" if score_delta >= 0 else str(score_delta)
            delta_color = "#16a34a" if score_delta >= 0 else "#dc2626"

            st.markdown(f"""
            <div class="profile-card">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div>
                        <span class="score-pill {'score-high' if top_mult >= 5 else 'score-mid'}">{top_mult}x</span>
                        <div class="profile-name">
                            <a class="profile-handle" href="{url}" target="_blank">@{handle}</a>
                            &nbsp;<span style="font-size:0.78rem;color:#737368;">{signal}</span>
                        </div>
                    </div>
                </div>
                <div style="display:flex;gap:2rem;margin-top:0.75rem;flex-wrap:wrap;">
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Followers</div>
                        <div style="font-weight:700;color:#150F3A;">{b['followers_now']:,}
                            <span style="font-size:0.75rem;color:#16a34a;">↑{b['follower_gain']:,} ({f_mult}x)</span>
                        </div>
                        <div style="font-size:0.72rem;color:#737368;">was {b['followers_then']:,}</div>
                    </div>
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Repo Stars</div>
                        <div style="font-weight:700;color:#150F3A;">{b['stars_now']:,}
                            <span style="font-size:0.75rem;color:#0083FF;">↑{b['star_gain']:,} ({s_mult}x)</span>
                        </div>
                        <div style="font-size:0.72rem;color:#737368;">was {b['stars_then']:,}</div>
                    </div>
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Signal Score</div>
                        <div style="font-weight:700;color:#150F3A;">{b['score_now']}
                            <span style="font-size:0.75rem;color:{delta_color};">{delta_str} pts</span>
                        </div>
                    </div>
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Tracked Since</div>
                        <div style="font-weight:700;color:#150F3A;">{b['first_seen']}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("")
        if st.button("📬 Email breakout list to Brent & Thomas", key="email_breakouts"):
            from notifications import send_breakout_alert
            for email in ["brent@m13.co", "thomas@m13.co"]:
                send_breakout_alert(breakouts, to_email=email)
            audit(USER_EMAIL, "breakout_alert_sent", {"count": len(breakouts), "triggered_by": "manual"})
            st.success(f"Breakout alert sent — {len(breakouts)} candidates.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: History
# ═══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.markdown('<div class="section-header">Your Search History</div>', unsafe_allow_html=True)
    history = get_run_history(USER_EMAIL, limit=25)
    if not history:
        st.caption("No searches run yet.")
    else:
        for run in history:
            ran_at = (run.get("ran_at") or "")[:16].replace("T", " ")
            triggered = run.get("triggered_by", "manual")
            trigger_icon = "🤖" if triggered == "scheduler" else "👤"
            st.markdown(f"""
            <div class="history-row">
                <strong style="color:#150F3A;">{run.get('intent','')}</strong>
                <span style="color:#737368;margin-left:0.75rem;font-size:0.75rem;">
                    {run.get('mode','')} · {trigger_icon} {triggered} · {ran_at} · {run.get('result_count',0)} results
                </span>
            </div>
            """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Settings
# ═══════════════════════════════════════════════════════════════════════════════
with tab_settings:
    st.markdown('<div class="section-header">Notification Preferences</div>', unsafe_allow_html=True)

    with st.form("notif_prefs"):
        pref_email = st.text_input(
            "Send notifications to",
            value=prefs.get("notify_email") or USER_EMAIL,
        )
        pref_freq = st.selectbox(
            "Recap frequency",
            ["Weekly", "Bi-Daily", "Daily", "Monthly"],
            index=["Weekly", "Bi-Daily", "Daily", "Monthly"].index(
                prefs.get("recap_frequency", "Weekly")
            ),
        )
        pref_new_match = st.checkbox(
            "Notify me when a saved search finds new candidates",
            value=prefs.get("notify_on_new_match", True),
        )
        save_prefs_btn = st.form_submit_button("Save Preferences", type="primary")
        if save_prefs_btn:
            if not pref_email.endswith("@m13.co"):
                st.warning("Notification email must be an @m13.co address.")
            else:
                ok = save_notification_prefs(
                    user_email=USER_EMAIL,
                    notify_email=pref_email,
                    recap_frequency=pref_freq,
                    notify_on_new_match=pref_new_match,
                    digest_recipients=[],
                )
                if ok:
                    st.success("Preferences saved!")
                    prefs["notify_email"] = pref_email
                    prefs["recap_frequency"] = pref_freq
                    prefs["notify_on_new_match"] = pref_new_match
                else:
                    st.error("Failed to save. Make sure the Supabase schema is up to date.")

    st.markdown("---")
    st.markdown('<div class="section-header">Scheduler</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="background:white;border-radius:12px;padding:1.25rem 1.5rem;border:1px solid #E8E8EC;font-size:0.85rem;color:#150F3A;">
        <p style="margin:0 0 0.75rem;font-weight:600;">Daily automated sourcing</p>
        <p style="margin:0 0 0.5rem;color:#737368;">
            The Render cron service runs saved searches daily and notifies each owner only about newly matched candidates.
        </p>
        <code style="background:#F7F7F8;padding:0.4rem 0.75rem;border-radius:6px;display:block;margin-top:0.5rem;font-size:0.78rem;color:#0083FF;">
            github-sourcing-daily · 16:00 UTC
        </code>
        <p style="margin:0.75rem 0 0;color:#737368;font-size:0.78rem;">
            The scheduler runs each saved search, finds new candidates vs prior runs, and sends email notifications — fully automated, no manual clicks needed.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown('<div class="section-header">Account</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:white;border-radius:12px;padding:1.25rem 1.5rem;border:1px solid #E8E8EC;font-size:0.85rem;">
        <div style="font-weight:600;color:#150F3A;">{USER_NAME}</div>
        <div style="color:#737368;">{USER_EMAIL}</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("")
    if st.button("Sign out", key="signout_settings"):
        audit(USER_EMAIL, "logout")
        logout()
        st.rerun()

st.markdown("")
st.caption(f"Built for M13 · Powered by GitHub API · {datetime.now().strftime('%b %d, %Y')}")
