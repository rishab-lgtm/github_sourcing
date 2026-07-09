"""
M13 GitHub Sourcing — Streamlit UI
"""

import os
import re
import json
import pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime, timedelta
from notifications import send_new_profiles_email, send_recap_email, send_weekly_digest
from github_sourcing import find_sf_ai_contributors, find_trending_repo_authors
from database import upsert_profiles, get_all_candidates, get_new_since

DIGEST_RECIPIENTS = ["brent@m13.co", "thomas@m13.co"]

load_dotenv()

SEEN_FILE = "output/seen_profiles.json"
LAST_RECAP_FILE = "output/last_recap.json"
RESULTS_CACHE_FILE = "output/last_results.json"

st.set_page_config(page_title="M13 GitHub Sourcing", page_icon="⚡", layout="wide")

st.session_state.setdefault("shortlist", set())

# ── Icon set (Feather-style strokes, matches AI Enrichment Engine) ──────────
def _icon(name: str, size: int = 14, color: str = "#0083FF") -> str:
    paths = {
        "zap":      '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
        "pin":      '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/>',
        "building": '<path d="M6 22V4a1 1 0 011-1h10a1 1 0 011 1v18"/><path d="M2 22h20"/><path d="M9 8h1M14 8h1M9 12h1M14 12h1M9 16h1M14 16h1"/>',
        "clock":    '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/>',
        "landmark": '<line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 21 8 3 8 12 2"/>',
        "rocket":   '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 00-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 012-3.95A12.88 12.88 0 0122 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 01-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
        "flask":    '<path d="M9 2v6.5L4.2 18a2 2 0 001.8 3h12a2 2 0 001.8-3L15 8.5V2"/><path d="M9 2h6"/><path d="M6.5 14h11"/>',
        "search":   '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
        "star":     '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    }
    d = paths.get(name, "")
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            f'style="vertical-align:-2px">{d}</svg>')

BADGE_ICON_MAP = {"🏛": "landmark", "🚀": "rocket", "🔬": "flask"}

st.markdown(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">',
    unsafe_allow_html=True
)
st.markdown("""
<style>
    /* ─── M13 Brand Tokens ───────────────────────────────────── */
    :root {
        --space-blue: #150F3A;
        --wave-blue:  #0083FF;
        --gray:       #737368;
        --white:      #FFFFFF;
        --off-white:  #F7F7F8;
        --border:     #E8E8EC;
        --radius-sm:  8px;
        --radius-md:  12px;
        --radius-pill:999px;
    }

    /* ─── Reset & base ──────────────────────────────────────── */
    html, body, [class*="css"], .stApp, .stApp * {
        font-family: 'Poppins', -apple-system, BlinkMacSystemFont, sans-serif !important;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }
    body, .stApp { background: var(--off-white) !important; line-height: 1.55; }
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

    h1,h2,h3,h4,h5,h6 { font-weight: 700 !important; letter-spacing: -0.02em !important; color: var(--space-blue) !important; line-height: 1.2 !important; }
    p, .stMarkdown p { color: var(--space-blue) !important; }
    hr { border: none !important; border-top: 1px solid var(--border) !important; margin: 1.25rem 0 !important; }
    label { font-size: 0.72rem !important; font-weight: 600 !important; color: var(--gray) !important;
            letter-spacing: 0.06em !important; text-transform: uppercase !important; }

    /* ─── Sidebar ───────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        background: var(--space-blue);
        border-right: 1px solid rgba(255,255,255,0.08);
    }
    [data-testid="stSidebar"] * { color: rgba(255,255,255,0.85) !important; }
    [data-testid="stSidebar"] label {
        color: rgba(255,255,255,0.55) !important;
    }
    [data-testid="stSidebar"] .stMarkdown h4 {
        color: rgba(255,255,255,0.5) !important;
        font-size: 0.65rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.12em !important;
        text-transform: uppercase !important;
        margin-bottom: 0.25rem !important;
    }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.1) !important; margin: 1rem 0 !important; }
    [data-testid="stSidebar"] [data-testid="stSlider"] > div > div > div { background: var(--wave-blue) !important; }
    [data-testid="stSidebar"] .stRadio label,
    [data-testid="stSidebar"] .stCheckbox label { font-size: 0.85rem !important; text-transform: none !important; letter-spacing: 0 !important; }
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] textarea,
    [data-testid="stSidebar"] .stSelectbox > div,
    [data-testid="stSidebar"] div[data-baseweb="select"] > div:first-child {
        background: rgba(255,255,255,0.07) !important;
        border-color: rgba(255,255,255,0.15) !important;
        border-radius: var(--radius-sm) !important;
    }
    [data-testid="stSidebar"] span[data-baseweb="tag"] {
        background: rgba(0,131,255,0.2) !important;
        border-radius: var(--radius-pill) !important;
    }

    /* ─── Buttons — pill shaped per M13 brand ───────────────── */
    .stButton > button {
        border-radius: var(--radius-pill) !important;
        font-weight: 600 !important;
        font-size: 0.82rem !important;
        padding: 0.5rem 1.25rem !important;
        transition: all 0.14s ease !important;
        border: 1.5px solid var(--border) !important;
        background: var(--white) !important;
        color: var(--space-blue) !important;
        box-shadow: 0 1px 2px rgba(21,15,58,0.06) !important;
        letter-spacing: 0.01em !important;
    }
    .stButton > button:hover {
        border-color: #c5c5d0 !important;
        background: var(--off-white) !important;
        box-shadow: 0 3px 8px rgba(21,15,58,0.1) !important;
        transform: translateY(-1px) !important;
    }
    .stButton > button:active { transform: translateY(0) !important; }
    .stButton > button[kind="primary"] {
        background: var(--wave-blue) !important;
        border-color: var(--wave-blue) !important;
        color: #ffffff !important;
        box-shadow: 0 2px 8px rgba(0,131,255,0.35) !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: #006FDB !important;
        border-color: #006FDB !important;
        color: #ffffff !important;
        box-shadow: 0 4px 14px rgba(0,131,255,0.45) !important;
    }
    .stButton > button[kind="primary"] p { color: #ffffff !important; }

    /* ─── Inputs ────────────────────────────────────────────── */
    .stTextInput > div > div > input,
    .stNumberInput > div > div > input {
        border-radius: var(--radius-md) !important;
        border: 1.5px solid var(--border) !important;
        background: var(--white) !important;
        font-size: 0.875rem !important;
        color: var(--space-blue) !important;
        box-shadow: none !important;
    }
    .stTextInput > div > div > input:focus,
    .stNumberInput > div > div > input:focus {
        border-color: var(--wave-blue) !important;
        box-shadow: 0 0 0 3px rgba(0,131,255,0.12) !important;
        outline: none !important;
    }
    div[data-baseweb="select"] > div:first-child {
        border-radius: var(--radius-md) !important;
        border: 1.5px solid var(--border) !important;
        background: var(--white) !important;
    }

    /* ─── Multiselect tags ──────────────────────────────────── */
    span[data-baseweb="tag"] {
        background: rgba(0,131,255,0.1) !important;
        border-radius: var(--radius-pill) !important;
    }
    span[data-baseweb="tag"] > span:first-child {
        color: var(--wave-blue) !important; font-weight: 600 !important;
    }

    /* ─── Checkboxes ────────────────────────────────────────── */
    .stCheckbox > label {
        font-size: 0.875rem !important;
        font-weight: 500 !important;
        text-transform: none !important;
        letter-spacing: 0 !important;
        gap: 10px !important;
        align-items: center !important;
    }
    .stCheckbox > label > div:first-child {
        border-radius: 5px !important;
    }

    /* ─── Alerts ────────────────────────────────────────────── */
    .stAlert { border-radius: var(--radius-md) !important; font-size: 0.875rem !important; border: none !important; }
    [data-testid="stAlertContentSuccess"] { background:#f0fdf6 !important; color:#166534 !important; border-left:3px solid #00D083 !important; }
    [data-testid="stAlertContentWarning"] { background:#fffbeb !important; color:#78350f !important; border-left:3px solid #FF9300 !important; }
    [data-testid="stAlertContentError"]   { background:#fef2f2 !important; color:#7f1d1d !important; border-left:3px solid #FF2CB0 !important; }
    [data-testid="stAlertContentInfo"]    { background:#eff6ff !important; color:var(--space-blue) !important; border-left:3px solid var(--wave-blue) !important; }

    /* ─── Metric cards ──────────────────────────────────────── */
    [data-testid="stMetric"] {
        background: var(--white); border: 1.5px solid var(--border);
        border-radius: var(--radius-md); padding: 18px 22px;
        box-shadow: 0 1px 3px rgba(21,15,58,0.05);
    }
    [data-testid="stMetricValue"] { font-size: 2rem !important; font-weight: 800 !important; color: var(--space-blue) !important; letter-spacing: -0.04em !important; }
    [data-testid="stMetricLabel"] { font-size: 0.68rem !important; font-weight: 600 !important; color: var(--gray) !important; text-transform: uppercase !important; letter-spacing: 0.07em !important; }

    /* ─── Profile cards ─────────────────────────────────────── */
    .profile-card {
        background: var(--white);
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        border: 1.5px solid var(--border);
        box-shadow: 0 2px 12px rgba(21,15,58,0.05);
        margin-bottom: 0.4rem;
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .profile-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(21,15,58,0.12);
        border-color: #c5c5d0;
    }
    .profile-avatar { width: 46px; height: 46px; border-radius: 50%; object-fit: cover; border: 1.5px solid var(--border); flex-shrink: 0; }
    .profile-name { font-size: 1rem; font-weight: 700; color: var(--space-blue); margin-bottom: 0.1rem; }
    .profile-handle { font-size: 0.78rem; color: var(--wave-blue); font-weight: 500; text-decoration: none; }
    .profile-handle:hover { text-decoration: underline; }
    .profile-meta { font-size: 0.78rem; color: rgba(21,15,58,0.5); margin-top: 0.25rem; }
    .profile-bio { font-size: 0.82rem; color: rgba(21,15,58,0.75); margin-top: 0.5rem; line-height: 1.5; font-style: italic; }
    .badge { display: inline-block; background: rgba(0,131,255,0.1); color: #0055B3; border-radius: var(--radius-pill); padding: 0.15rem 0.6rem; font-size: 0.72rem; font-weight: 600; margin-right: 0.3rem; margin-top: 0.4rem; }
    .score-pill { display: inline-block; border-radius: var(--radius-pill); padding: 0.2rem 0.75rem; font-size: 0.75rem; font-weight: 700; float: right; transition: transform 0.15s ease; }
    .profile-card:hover .score-pill { transform: scale(1.08); }
    .score-high { background: #d4f7e8; color: #0a6641; }
    .score-mid  { background: #fff8d4; color: #7a6000; }
    .score-low  { background: #fde8e8; color: #8b1a1a; }
    .repo-tag { display: inline-block; background: rgba(21,15,58,0.05); color: rgba(21,15,58,0.65); border-radius: var(--radius-sm); padding: 0.1rem 0.5rem; font-size: 0.7rem; margin-right: 0.25rem; margin-top: 0.3rem; }
    .new-badge { display: inline-block; background: var(--wave-blue); color: white; border-radius: 6px; padding: 0.1rem 0.45rem; font-size: 0.62rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; vertical-align: middle; margin-left: 0.4rem; }
    .section-header { font-size: 1.1rem; font-weight: 700; color: var(--space-blue); margin: 1.5rem 0 1rem; padding-bottom: 0.5rem; border-bottom: 2px solid rgba(0,131,255,0.15); }
    .empty-state { text-align: center; padding: 3rem 1rem; color: rgba(21,15,58,0.4); }
    .empty-state .icon { margin-bottom: 0.9rem; }

    /* ─── Shortlist bar ──────────────────────────────────────── */
    .shortlist-bar {
        display: flex; align-items: center; justify-content: space-between;
        background: linear-gradient(135deg, rgba(0,131,255,0.08), rgba(21,15,58,0.04));
        border: 1.5px solid rgba(0,131,255,0.2); border-radius: var(--radius-md);
        padding: 0.7rem 1.1rem; margin-bottom: 1rem;
    }
    .shortlist-bar span { font-size: 0.85rem; font-weight: 600; color: var(--space-blue); }

    /* ─── Shortlist checkbox row — pull tight under the card ── */
    div[data-testid="stCheckbox"]:has(input[id*="sl_"]) { margin-top: -0.65rem !important; margin-bottom: 0.5rem !important; padding-left: 0.25rem !important; }
    div[data-testid="stCheckbox"]:has(input[id*="sl_"]) label p { font-size: 0.72rem !important; color: rgba(21,15,58,0.55) !important; font-weight: 500 !important; }

    /* ─── Spinner ────────────────────────────────────────────── */
    .stSpinner > div { border-top-color: var(--wave-blue) !important; }

    /* ─── Charts ─────────────────────────────────────────────── */
    .chart-card {
        background: var(--white); border: 1.5px solid var(--border); border-radius: var(--radius-md);
        padding: 1rem 1.1rem 0.25rem; box-shadow: 0 1px 3px rgba(21,15,58,0.05);
    }
    .chart-title { font-size: 0.68rem; font-weight: 700; color: var(--gray); text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 0.5rem; }

    /* ─── Download button ───────────────────────────────────── */
    .stDownloadButton > button {
        border-radius: var(--radius-pill) !important; font-weight: 600 !important; font-size: 0.8rem !important;
        background: var(--white) !important; color: var(--space-blue) !important;
        border: 1.5px solid var(--border) !important; box-shadow: 0 1px 2px rgba(21,15,58,0.06) !important;
    }
    .stDownloadButton > button:hover { border-color: var(--wave-blue) !important; color: var(--wave-blue) !important; background: var(--off-white) !important; }
</style>
""", unsafe_allow_html=True)


# --- Persistence helpers ---

def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    os.makedirs("output", exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def load_last_results() -> list:
    if os.path.exists(RESULTS_CACHE_FILE):
        with open(RESULTS_CACHE_FILE) as f:
            return json.load(f)
    return []

def save_last_results(results: list):
    os.makedirs("output", exist_ok=True)
    with open(RESULTS_CACHE_FILE, "w") as f:
        json.dump(results, f)

def load_last_recap() -> dict:
    if os.path.exists(LAST_RECAP_FILE):
        with open(LAST_RECAP_FILE) as f:
            return json.load(f)
    return {}

def save_last_recap(data: dict):
    os.makedirs("output", exist_ok=True)
    with open(LAST_RECAP_FILE, "w") as f:
        json.dump(data, f)

def score_color_class(score):
    if score >= 60: return "score-high"
    elif score >= 50: return "score-mid"
    return "score-low"

def recap_due(frequency: str, last_recap: dict) -> bool:
    if not last_recap.get("sent_at"):
        return True
    last = datetime.fromisoformat(last_recap["sent_at"])
    delta = {"Daily": 1, "Bi-Daily": 2, "Weekly": 7, "Monthly": 30}[frequency]
    return datetime.now() >= last + timedelta(days=delta)

def total_stars(repos_str):
    stars = re.findall(r'\((\d+)⭐\)', repos_str or "")
    return sum(int(s) for s in stars)

def render_profile_card(row: dict, is_new: bool = False):
    name = row.get("name") or row.get("handle", "")
    handle = row.get("handle", "")
    github_url = row.get("github_url", f"https://github.com/{handle}")
    location = row.get("location", "")
    company = row.get("company", "")
    bio = row.get("bio", "")
    badges = row.get("founder_badges", "")
    score = row.get("signal_score", 0)
    top_repos = row.get("top_repos", "")
    acct_age = row.get("account_age_years")

    meta_parts = []
    if location: meta_parts.append(f'{_icon("pin", color="#9a9aa8")} {location}')
    if company: meta_parts.append(f'{_icon("building", color="#9a9aa8")} {company}')
    if acct_age is not None: meta_parts.append(f'{_icon("clock", color="#9a9aa8")} {acct_age}yr account')
    meta_str = " &nbsp;·&nbsp; ".join(meta_parts)

    badge_html = ""
    if badges:
        # Split on known badge emoji boundaries (robust to both old space-joined
        # and current "|"-joined founder_badges strings from cached/DB data).
        emoji_class = "".join(BADGE_ICON_MAP.keys())
        for b in re.split(f"(?=[{emoji_class}])", str(badges)):
            b = b.strip(" |").strip()
            if not b:
                continue
            emoji, _, label = b.partition(" ")
            label = label.rstrip("|").strip()
            icon_name = BADGE_ICON_MAP.get(emoji)
            content = f'{_icon(icon_name, size=12, color="#0055B3")} {label}' if icon_name else b
            badge_html += f'<span class="badge">{content}</span>'

    repos_html = ""
    if top_repos:
        repo_items = [r.strip().replace("⭐", "★") for r in str(top_repos).split(",") if r.strip()][:3]
        for repo in repo_items:
            repos_html += f'<span class="repo-tag">{repo}</span>'

    score_class = score_color_class(score)
    new_html = '<span class="new-badge">New</span>' if is_new else ""
    bio_html = f'<div class="profile-bio">"{bio}"</div>' if bio and bio not in ("nan", "") else ""

    avatar_url = f"https://github.com/{handle}.png?size=96"

    st.markdown(f"""
    <div class="profile-card">
        <div style="display:flex; gap:12px; align-items:flex-start;">
            <img class="profile-avatar" src="{avatar_url}" loading="lazy" onerror="this.style.visibility='hidden'">
            <div style="flex:1; min-width:0;">
                <span class="score-pill {score_class}">{score}</span>
                <div class="profile-name">{name}{new_html}</div>
                <a class="profile-handle" href="{github_url}" target="_blank">@{handle}</a>
                <div class="profile-meta">{meta_str}</div>
            </div>
        </div>
        {bio_html}
        <div style="margin-top:0.5rem">{badge_html}</div>
        <div>{repos_html}</div>
    </div>
    """, unsafe_allow_html=True)

    def _toggle_shortlist(h=handle):
        if st.session_state.get(f"sl_{h}"):
            st.session_state.shortlist.add(h)
        else:
            st.session_state.shortlist.discard(h)

    st.checkbox(
        "Shortlist" if handle in st.session_state.shortlist else "Add to shortlist",
        value=handle in st.session_state.shortlist,
        key=f"sl_{handle}",
        on_change=_toggle_shortlist,
    )


# --- Sidebar ---
with st.sidebar:
    st.markdown(f"""
    <div style="padding: 0.5rem 0 1rem">
        <div style="font-size:1.1rem;font-weight:700;letter-spacing:-0.01em;">{_icon("zap", size=16, color="#ffffff")} GitHub Sourcing</div>
        <div style="font-size:0.72rem;opacity:0.55;margin-top:0.2rem;">Surface engineers before they're on anyone's radar</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    st.markdown("#### Scan Type")
    mode = st.radio("", ["SF-Based AI Contributors", "Trending Repo Authors"], label_visibility="collapsed")

    st.markdown("---")
    st.markdown("#### Filters")

    min_score = st.slider("Min signal score", 0, 100, 40, step=5)
    filter_badges = st.multiselect(
        "Founder signals",
        ["Top Lab", "Building", "Researcher"],
        default=[],
        help="Only show profiles with these badges. Leave blank to show all."
    )
    min_stars = st.number_input("Min repo stars", min_value=0, value=0, step=50)
    bio_keyword = st.text_input("Bio keyword", placeholder="stealth, agent, infra…")
    region = st.text_input("Region", placeholder="SF, NYC, London — blank for all")
    max_account_age_years = st.slider("Max account age (yrs)", 1, 15, 15)
    stealth_only = st.checkbox("Stealth only", value=False)
    sort_by = st.selectbox("Sort by", ["Signal Score", "Followers", "Repo Stars"])
    track_mode = st.checkbox("Only show new profiles", value=False)

    st.markdown("---")
    st.markdown("#### Notifications")
    notify_email = st.text_input("Your email", value=st.session_state.get("saved_email", "rishab@m13.co"))
    recap_frequency = st.selectbox("Recap frequency", ["Weekly", "Bi-Daily", "Daily", "Monthly"])
    if st.button("Save settings"):
        st.session_state["saved_email"] = notify_email
        st.session_state["saved_frequency"] = recap_frequency
        st.success("Saved!")

    st.markdown("---")
    st.caption("Built for M13 · GitHub API")


# --- Main header ---
st.markdown("""
<div style="background:linear-gradient(135deg,#150F3A 0%,#1e1660 100%);border-radius:18px;padding:1.75rem 2rem;margin-bottom:1.75rem;">
    <div style="color:rgba(255,255,255,0.5);font-size:0.65rem;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.3rem;">M13 Internal</div>
    <div style="color:white;font-size:1.6rem;font-weight:700;letter-spacing:-0.02em;line-height:1.1;">GitHub Sourcing</div>
    <div style="color:rgba(255,255,255,0.55);font-size:0.82rem;margin-top:0.4rem;">Find talented engineers and future founders before they appear on AngelList or TechCrunch.</div>
</div>
""", unsafe_allow_html=True)

# --- Action bar ---
col1, col2, col3 = st.columns([3, 1.4, 1.4])
with col1:
    run_scan = st.button("Run Scan", type="primary", use_container_width=True)
with col2:
    send_recap = st.button("Send Recap", use_container_width=True)
with col3:
    send_digest = st.button("Weekly Digest", use_container_width=True, help="Sends new candidates to Brent & Thomas")


# --- Send weekly digest ---
if send_digest:
    with st.spinner("Pulling latest candidates from database..."):
        try:
            new_candidates = get_new_since(days=7)
            all_candidates = get_all_candidates()
            if not new_candidates:
                st.info("No new candidates in the last 7 days — nothing to send.")
            else:
                for email in DIGEST_RECIPIENTS:
                    send_weekly_digest(new_profiles=new_candidates, all_profiles=all_candidates, to_email=email)
                st.success(f"Weekly digest sent to Brent & Thomas! ({len(new_candidates)} new candidates)")
        except Exception as e:
            st.error(f"Failed: {e}")


# --- Send recap ---
if send_recap:
    last_results = load_last_results()
    last_recap = load_last_recap()
    if not last_results:
        st.warning("No scan results yet — run a scan first.")
    else:
        prev_handles = set(last_recap.get("handles", []))
        current_handles = set(p["handle"] for p in last_results)
        new_profiles = [p for p in last_results if p["handle"] not in prev_handles]
        removed_handles = prev_handles - current_handles
        with st.spinner("Sending recap..."):
            sent = send_recap_email(
                profiles=last_results,
                new_profiles=new_profiles,
                removed_handles=list(removed_handles),
                to_email=notify_email,
                frequency=recap_frequency,
            )
        if sent:
            save_last_recap({"sent_at": datetime.now().isoformat(), "handles": list(current_handles)})
            st.success(f"Recap sent to {notify_email}!")
        else:
            st.error("Failed to send recap — check your Resend API key.")


def _apply_filters(df, prev_handles=None):
    """Apply sidebar filters to a dataframe of profiles."""
    for col in ["founder_badges", "company", "bio", "location"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df = df[df["signal_score"] >= min_score]

    if filter_badges:
        df = df[df["founder_badges"].apply(lambda b: any(badge in b for badge in filter_badges))]

    if min_stars > 0:
        df = df[df["top_repos"].apply(total_stars) >= min_stars]

    if bio_keyword.strip():
        kw = bio_keyword.strip().lower()
        df = df[df["bio"].str.lower().str.contains(kw, na=False)]

    if region.strip():
        r = region.strip().lower()
        df = df[df["location"].str.lower().str.contains(r, na=False)]

    if "account_age_years" in df.columns and max_account_age_years < 15:
        df = df[df["account_age_years"] <= max_account_age_years]

    if stealth_only:
        df = df[df["bio"].str.lower().str.contains("stealth", na=False)]

    if track_mode and prev_handles:
        df = df[~df["handle"].isin(prev_handles)]

    if sort_by == "Followers":
        df = df.sort_values("followers", ascending=False)
    elif sort_by == "Repo Stars":
        df = df.copy()
        df["_total_stars"] = df["top_repos"].apply(total_stars)
        df = df.sort_values("_total_stars", ascending=False)
    else:
        df = df.sort_values("signal_score", ascending=False)

    return df


def _score_histogram(df: pd.DataFrame):
    chart = (
        alt.Chart(df)
        .mark_bar(color="#0083FF", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("signal_score:Q", bin=alt.Bin(maxbins=12), title="Signal score"),
            y=alt.Y("count()", title="Profiles"),
            tooltip=[alt.Tooltip("count()", title="Profiles")],
        )
        .properties(height=200)
        .configure_view(strokeWidth=0)
        .configure_axis(gridColor="#F0F0F3", domainColor="#E8E8EC", labelColor="#737368", titleColor="#737368", labelFontSize=10, titleFontSize=10)
    )
    return chart


def _top_companies_chart(df: pd.DataFrame, n: int = 8):
    counts = df[df["company"].str.strip() != ""]["company"].value_counts().head(n).reset_index()
    counts.columns = ["company", "count"]
    if counts.empty:
        return None
    chart = (
        alt.Chart(counts)
        .mark_bar(color="#150F3A", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("count:Q", title="Profiles"),
            y=alt.Y("company:N", sort="-x", title=None),
            tooltip=["company", "count"],
        )
        .properties(height=200)
        .configure_view(strokeWidth=0)
        .configure_axis(gridColor="#F0F0F3", domainColor="#E8E8EC", labelColor="#737368", titleColor="#737368", labelFontSize=10, titleFontSize=10)
    )
    return chart


def render_results_section(df: pd.DataFrame, all_profiles: list, section_label: str, new_handle_set=None, extra_metrics=None):
    """Shared rendering for both a fresh scan and the cached view: stats, charts, shortlist, cards."""
    new_handle_set = new_handle_set or set()

    # --- Stats row ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Profiles Found", len(all_profiles))
    m2.metric("After Filters", len(df))
    if extra_metrics:
        m3.metric(extra_metrics[0][0], extra_metrics[0][1])
        m4.metric(extra_metrics[1][0], extra_metrics[1][1])
    else:
        avg_score = round(df["signal_score"].mean()) if not df.empty else 0
        m3.metric("Avg Score", avg_score)
        m4.metric("Companies", df[df["company"].str.strip() != ""]["company"].nunique() if not df.empty else 0)

    # --- Charts ---
    if not df.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="chart-card"><div class="chart-title">Signal Score Distribution</div>', unsafe_allow_html=True)
            st.altair_chart(_score_histogram(df), use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with c2:
            company_chart = _top_companies_chart(df)
            st.markdown('<div class="chart-card"><div class="chart-title">Top Companies</div>', unsafe_allow_html=True)
            if company_chart is not None:
                st.altair_chart(company_chart, use_container_width=True)
            else:
                st.markdown('<p style="color:rgba(21,15,58,0.4);font-size:0.8rem;padding:2rem 0;text-align:center;">No company data yet</p>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # --- Shortlist bar ---
    profiles_by_handle = {p["handle"]: p for p in all_profiles}
    shortlist_rows = [profiles_by_handle[h] for h in st.session_state.shortlist if h in profiles_by_handle]
    if shortlist_rows:
        sl_col1, sl_col2 = st.columns([4, 1])
        with sl_col1:
            st.markdown(
                f'<div class="shortlist-bar"><span>{_icon("star", size=15, color="#0083FF")} {len(shortlist_rows)} shortlisted</span></div>',
                unsafe_allow_html=True,
            )
        with sl_col2:
            st.download_button(
                "Download Shortlist",
                data=pd.DataFrame(shortlist_rows).to_csv(index=False),
                file_name=f"m13_shortlist_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if df.empty:
        st.markdown(f"""
        <div class="empty-state">
            <div class="icon">{_icon("search", size=40, color="rgba(21,15,58,0.25)")}</div>
            <p>No profiles match your current filters.<br>Try loosening the score threshold or removing badge filters.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    header_col, toggle_col = st.columns([4, 1.2])
    with header_col:
        st.markdown(f'<div class="section-header">{section_label}</div>', unsafe_allow_html=True)
    with toggle_col:
        view_mode = st.radio("View", ["List", "Grid"], horizontal=True, key="view_mode", label_visibility="collapsed")

    if view_mode == "Grid":
        cols = st.columns(3)
        for i, (_, row) in enumerate(df.iterrows()):
            with cols[i % 3]:
                render_profile_card(row.to_dict(), is_new=row.get("handle") in new_handle_set)
    else:
        for _, row in df.iterrows():
            render_profile_card(row.to_dict(), is_new=row.get("handle") in new_handle_set)

    st.markdown("")
    csv = df.to_csv(index=False)
    st.download_button(
        "Download CSV",
        data=csv,
        file_name=f"m13_sourcing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )


# --- Run scan ---
if run_scan:
    with st.spinner("Scanning GitHub… this takes 2–3 minutes"):
        if mode == "SF-Based AI Contributors":
            results = find_sf_ai_contributors(limit_per_repo=100)
        else:
            results = find_trending_repo_authors(days=7)

    if not results:
        st.warning("No profiles found.")
    else:
        seen = load_seen()
        prev_results = load_last_results()
        prev_handles = set(p["handle"] for p in prev_results)
        new_profiles = [p for p in results if p["handle"] not in seen]
        removed_handles = prev_handles - set(p["handle"] for p in results)
        seen.update(p["handle"] for p in results)
        save_seen(seen)
        save_last_results(results)

        with st.spinner("Saving to database..."):
            try:
                db_result = upsert_profiles(results)
                st.caption(f"Saved — {len(db_result['new'])} new, {len(db_result['updated'])} updated")
            except Exception as e:
                st.caption(f"Database save failed: {e}")

        if new_profiles and notify_email:
            send_new_profiles_email(new_profiles, to_email=notify_email)

        last_recap = load_last_recap()
        if recap_due(recap_frequency, last_recap) and notify_email:
            sent = send_recap_email(
                profiles=results,
                new_profiles=new_profiles,
                removed_handles=list(removed_handles),
                to_email=notify_email,
                frequency=recap_frequency,
            )
            if sent:
                save_last_recap({"sent_at": datetime.now().isoformat(), "handles": [p["handle"] for p in results]})

        df = _apply_filters(pd.DataFrame(results), prev_handles)
        new_handle_set = set(p["handle"] for p in new_profiles)

        render_results_section(
            df, results, f"Results — {len(df)} profiles",
            new_handle_set=new_handle_set,
            extra_metrics=[("New This Scan", len(new_profiles)), ("Dropped Off", len(removed_handles))],
        )

else:
    last_results = load_last_results()
    if not last_results:
        st.markdown(f"""
        <div class="empty-state" style="padding:4rem 1rem;">
            <div class="icon">{_icon("zap", size=40, color="rgba(21,15,58,0.25)")}</div>
            <p style="font-size:1rem;font-weight:600;color:rgba(21,15,58,0.6);">No scan run yet</p>
            <p>Hit <strong>Run Scan</strong> above to surface high-signal engineers from GitHub.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Showing cached results from last scan. Hit **Run Scan** to refresh.")
        df = _apply_filters(pd.DataFrame(last_results))
        render_results_section(df, last_results, f"{len(df)} profiles")

st.markdown("")
st.caption(f"Built for M13 · Powered by GitHub API · {datetime.now().strftime('%b %d, %Y')}")
